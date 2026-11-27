"""Опции панели: WARP, защита выхода, SSH через вход, тест маршрута и схема трафика."""

from __future__ import annotations

import json
import uuid
from typing import Any

from sqlalchemy import select

from .db import session
from .heroine import heroine_for
from .jump import SSH_VIA_ENTRY_KEY, resolve_ssh_plan, setting_enabled
from .lists import DEFAULT_SOURCES, now_iso
from .models import AddressEntry, Client, ListSource, Server, Setting
from .routing import Catalog, RuleView, _match_clause, evaluate
from .ssh_client import SSHError, SSHTarget
from .warp import (
    DEFAULT_WARP_SITES,
    WARP_MODES,
    WARP_SOURCE,
    WarpError,
    collect_exclude_ips,
    detect_panel_public_ip,
    masque_supported,
    parse_warp_entry,
    parse_warp_output,
    public_warp_status,
    register_warp_account,
    render_disable_script,
    render_enable_script,
)

EXIT_ROLES = {"exit", "standby_exit"}


def flag_on(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip() in {"1", "true", "yes", "on"}


def ssh_via_entry(db) -> bool:
    row = db.get(Setting, SSH_VIA_ENTRY_KEY)
    return setting_enabled(row.value if row else "")


def set_ssh_via_entry(db, enabled: bool) -> dict:
    value = "1" if enabled else "0"
    row = db.get(Setting, SSH_VIA_ENTRY_KEY)
    if row is None:
        db.add(Setting(key=SSH_VIA_ENTRY_KEY, value=value))
    else:
        row.value = value
    return {"ssh_via_entry": enabled}


WARP_SITES_SEEDED = "warp_sites_seeded"


def ensure_defaults(db) -> None:
    if db.get(Setting, SSH_VIA_ENTRY_KEY) is None:
        db.add(Setting(key=SSH_VIA_ENTRY_KEY, value="0"))
    # На обновлении добавляем только новые источники. Пустую базу не заполняем — это делает seed.
    if db.scalar(select(ListSource).limit(1)) is None:
        return
    for item in DEFAULT_SOURCES:
        if db.get(ListSource, item["id"]) is None:
            db.add(ListSource(**item))
    ensure_warp_sites(db)


def ensure_warp_sites(db) -> None:
    """Один раз при миграции/старте, не на каждый запрос страницы."""
    flag = db.get(Setting, WARP_SITES_SEEDED)
    existing = list(db.scalars(select(AddressEntry).where(AddressEntry.source == WARP_SOURCE)).all())
    if existing:
        if flag is None:
            db.add(Setting(key=WARP_SITES_SEEDED, value="1"))
        return
    if flag is not None and flag.value == "1":
        return
    for kind, value in DEFAULT_WARP_SITES:
        db.add(
            AddressEntry(
                id=str(uuid.uuid4()),
                kind=kind,
                value=value,
                action="warp",
                source=WARP_SOURCE,
                source_date=now_iso(),
                pinned=False,
                excluded=False,
            )
        )
    if flag is None:
        db.add(Setting(key=WARP_SITES_SEEDED, value="1"))
    else:
        flag.value = "1"


def warp_site_rows(db) -> list[AddressEntry]:
    return list(
        db.scalars(
            select(AddressEntry)
            .where(AddressEntry.source == WARP_SOURCE)
            .order_by(AddressEntry.source_date.desc())
        ).all()
    )


def site_view(row: AddressEntry) -> dict:
    return {
        "id": row.id,
        "kind": row.kind,
        "value": row.value,
        "action": row.action,
        "source": row.source,
        "source_date": row.source_date,
        "pinned": row.pinned,
        "excluded": row.excluded,
    }


def add_warp_site(db, raw: str, *, pinned: bool = False) -> AddressEntry:
    kind, value = parse_warp_entry(raw)
    blocked = db.scalar(
        select(AddressEntry).where(
            AddressEntry.kind == kind,
            AddressEntry.value == value,
            AddressEntry.source == WARP_SOURCE,
            AddressEntry.excluded.is_(True),
        )
    )
    if blocked:
        raise ValueError("запись в списке исключений: сначала верните её")
    row = db.scalar(
        select(AddressEntry).where(
            AddressEntry.kind == kind, AddressEntry.value == value, AddressEntry.source == WARP_SOURCE
        )
    )
    if row:
        if not row.pinned:
            row.source_date = now_iso()
        touch_warp_exits(db)
        return row
    row = AddressEntry(
        id=str(uuid.uuid4()),
        kind=kind,
        value=value,
        action="warp",
        source=WARP_SOURCE,
        source_date=now_iso(),
        pinned=pinned,
        excluded=False,
    )
    db.add(row)
    touch_warp_exits(db)
    return row


def update_warp_site(db, entry_id: str, raw: str) -> AddressEntry:
    row = db.get(AddressEntry, entry_id)
    if row is None or row.source != WARP_SOURCE:
        raise ValueError("запись списка WARP не найдена")
    kind, value = parse_warp_entry(raw)
    row.kind = kind
    row.value = value
    row.source_date = now_iso()
    touch_warp_exits(db)
    return row


def touch_warp_exits(db) -> list[str]:
    ids: list[str] = []
    for server in db.scalars(select(Server).where(Server.warp_enabled.is_(True))).all():
        if server.role not in EXIT_ROLES:
            continue
        if (server.warp_mode or "sites") != "sites":
            continue
        server.config_dirty = True
        if not server.demo and server.precheck_json:
            ids.append(server.id)
    return ids


def protections_of(server: Server) -> dict:
    return {
        "bittorrent": bool(server.protect_bittorrent),
        "private": bool(server.protect_private),
        "ads": bool(server.protect_ads),
    }


def warp_payload(server: Server) -> dict:
    try:
        return json.loads(server.warp_json or "{}")
    except json.JSONDecodeError:
        return {}


def warp_of(server: Server) -> dict:
    mode = server.warp_mode if server.warp_mode in WARP_MODES else "sites"
    return public_warp_status(warp_payload(server), enabled=bool(server.warp_enabled), mode=mode, last_error=server.last_error if server.warp_enabled else "")


def node_card(server: Server | None) -> dict | None:
    if server is None:
        return None
    return {
        "id": server.id,
        "name": server.name,
        "role": server.role,
        "country": server.country,
        "status": server.status,
        "demo": server.demo,
        "heroine": heroine_for(server),
        "warp": warp_of(server) if server.role in EXIT_ROLES else None,
        "protect": protections_of(server) if server.role in EXIT_ROLES else None,
    }


def target_of(server: Server) -> SSHTarget:
    from .service import _box

    secret = _box().decrypt(server.ssh_secret_enc)
    return SSHTarget(server.host, int(server.ssh_port or 22), server.ssh_user, server.auth_type, secret)


def plan_for(server: Server) -> tuple[SSHTarget, SSHTarget | None]:
    target = target_of(server)
    with session() as db:
        via = ssh_via_entry(db)
        entry = db.scalar(select(Server).where(Server.role == "entry"))
        entry_target = None
        if entry is not None and not entry.demo:
            entry_target = target_of(entry)
    return resolve_ssh_plan(target, role=server.role, via_entry=via, entry=entry_target)


def load_warp_account(server: Server) -> dict | None:
    if not server.warp_wg_enc:
        return None
    from .service import _box

    try:
        data = json.loads(_box().decrypt(server.warp_wg_enc))
    except Exception:  # noqa: BLE001
        return None
    return data if isinstance(data, dict) and data.get("secret_key") else None


def store_warp_account(server: Server, account: dict) -> None:
    from .service import _box

    server.warp_wg_enc = _box().encrypt(json.dumps(account))


def sites_as_dicts(db) -> list[dict]:
    return [site_view(row) for row in warp_site_rows(db)]


def warp_plan_for_exit(db, server: Server) -> dict:
    account = load_warp_account(server) if server.warp_enabled and (server.warp_mode or "sites") == "sites" else None
    return {
        "enabled": bool(server.warp_enabled),
        "mode": server.warp_mode if server.warp_mode in WARP_MODES else "sites",
        "account": account,
        "sites": sites_as_dicts(db),
        "masque": warp_payload(server).get("masque") or "",
    }


def set_protections(db, server: Server, payload: dict) -> dict:
    if server.role not in EXIT_ROLES:
        raise ValueError("защита включается только на выходном узле")
    if "bittorrent" in payload:
        server.protect_bittorrent = bool(payload["bittorrent"])
    if "private" in payload:
        server.protect_private = bool(payload["private"])
    if "ads" in payload:
        server.protect_ads = bool(payload["ads"])
    server.config_dirty = True
    return protections_of(server)


def exclude_ips_for(db, server: Server) -> list[str]:
    entry = db.scalar(select(Server).where(Server.role == "entry"))
    via = ssh_via_entry(db)
    panel_ip = None
    if not via:
        panel_ip = detect_panel_public_ip()
    return collect_exclude_ips(entry_host=entry.host if entry else "", panel_ip=panel_ip, via_entry=via)


def start_warp_job(server_id: str, enabled: bool, mode: str | None = None) -> str:
    from .service import _finish, _log, start_job

    mode = (mode or "sites").strip() or "sites"
    if mode not in WARP_MODES:
        raise ValueError("режим WARP: sites или all")

    def worker(job_id: str, sid: str | None) -> None:
        assert sid
        _warp_worker(job_id, sid, enabled, mode)

    kind = "warp-on" if enabled else "warp-off"
    with session() as db:
        server = db.get(Server, server_id)
        if server is None:
            raise ValueError("узел не найден")
        if server.role not in EXIT_ROLES:
            raise ValueError("WARP-выход включается только на выходном узле")
        if server.demo:
            raise ValueError("демо-узел не меняется")
    return start_job(kind, server_id, worker)


def _warp_worker(job_id: str, server_id: str, enabled: bool, mode: str) -> None:
    from .service import _finish, _log, connect, files_for, reconfigure_worker

    action = "включаю" if enabled else "выключаю"
    _log(job_id, "warp", f"{action} Cloudflare WARP, режим {'весь трафик' if mode == 'all' else 'только выбранные сайты'}")
    try:
        with session() as db:
            server = db.get(Server, server_id)
            if server is None:
                raise ValueError("узел не найден")
            ssh_cm = connect(server, timeout=40)
            exclude = exclude_ips_for(db, server) if enabled and mode == "all" else []
        if enabled and mode == "all":
            script = render_enable_script(exclude)
            path = "/opt/cascade/warp/enable.sh"
        elif enabled:
            # Режим сайтов не перехватывает весь интернет сервера: только исходящий WARP в Xray.
            _prepare_sites_account(job_id, server_id, ssh_cm)
            with session() as db:
                server = db.get(Server, server_id)
                assert server
                server.warp_enabled = True
                server.warp_mode = "sites"
                payload = warp_payload(server)
                payload.update({"connected": True, "status": "подключён для выбранных сайтов", "error": "", "mode": "sites"})
                server.warp_json = json.dumps(payload, ensure_ascii=False)
                server.config_dirty = True
            _push_xray(job_id, server_id, ssh_cm)
            _log(job_id, "warp", "WARP для выбранных сайтов включён")
            _finish(job_id, "done", server_id)
            return
        else:
            script = render_disable_script(uninstall=True)
            path = "/opt/cascade/warp/disable.sh"

        if not enabled or mode == "all":
            with ssh_cm as ssh:
                ssh.write_file(path, script.encode("utf-8"), 0o700)
                lines: list[str] = []
                code = ssh.run(f"bash {path}", lambda line: lines.append(line) or _log(job_id, "warp", line))
            try:
                data = parse_warp_output(lines)
            except WarpError as exc:
                if code != 0:
                    raise
                data = {"error": str(exc)}
            if code != 0:
                raise WarpError(str(data.get("error") or "не удалось настроить Cloudflare WARP"))
            with session() as db:
                server = db.get(Server, server_id)
                assert server
                if enabled:
                    server.warp_enabled = True
                    server.warp_mode = "all"
                    payload = {**warp_payload(server), **data, "mode": "all", "error": ""}
                    server.warp_json = json.dumps(payload, ensure_ascii=False)
                    server.config_dirty = True
                else:
                    server.warp_enabled = False
                    payload = {**data, "mode": server.warp_mode or "sites", "connected": False, "egress_ip": "", "error": ""}
                    server.warp_json = json.dumps(payload, ensure_ascii=False)
                    server.warp_wg_enc = ""
                    server.config_dirty = True
            if not enabled or mode == "all":
                # Для «весь трафик» Xray тоже обновляем защиту; сам интернет уже через WARP.
                try:
                    with session() as db:
                        server = db.get(Server, server_id)
                        assert server
                        ssh_cm = connect(server, timeout=40)
                    _push_xray(job_id, server_id, ssh_cm)
                except Exception as exc:  # noqa: BLE001
                    _log(job_id, "warp", f"конфиг Xray обновить не удалось: {exc.__class__.__name__}", "warn")
            _log(job_id, "warp", "готово")
            _finish(job_id, "done", server_id)
    except Exception as exc:  # noqa: BLE001
        message = str(exc) if isinstance(exc, (WarpError, SSHError, ValueError)) else "не удалось настроить Cloudflare WARP"
        _log(job_id, "warp", message, "error")
        with session() as db:
            server = db.get(Server, server_id)
            if server is not None and enabled:
                server.warp_enabled = False
                payload = warp_payload(server)
                payload.update({"connected": False, "error": message, "status": "ошибка"})
                server.warp_json = json.dumps(payload, ensure_ascii=False)
        _finish(job_id, "error", server_id, message)


def _prepare_sites_account(job_id: str, server_id: str, ssh_cm) -> None:
    from .awg import generate_wg_keypair
    from .service import _log

    with session() as db:
        server = db.get(Server, server_id)
        assert server
        existing = load_warp_account(server)
    if existing:
        _log(job_id, "warp", "беру уже сохранённую учётную запись WARP")
        return
    keys = generate_wg_keypair()
    account = register_warp_account(keys.public_b64, keys.private_b64)
    with session() as db:
        server = db.get(Server, server_id)
        assert server
        store_warp_account(server, account)
    _log(job_id, "warp", "учётная запись WARP получена, системный туннель не включаю")
    # На сервере проверяем, умеет ли Xray MASQUE — только заметка, без обязательного включения.
    try:
        with ssh_cm as ssh:
            lines: list[str] = []
            ssh.run("if [ -x /opt/cascade/bin/xray ]; then /opt/cascade/bin/xray help 2>&1 | head -c 8000; else echo NOXRAY; fi", lines.append)
        note = "MASQUE доступен, можно использовать как запасной путь" if masque_supported("\n".join(lines)) else "пропущен: установленный Xray не поддерживает MASQUE"
        with session() as db:
            server = db.get(Server, server_id)
            assert server
            payload = warp_payload(server)
            payload["masque"] = note
            server.warp_json = json.dumps(payload, ensure_ascii=False)
        _log(job_id, "warp", note)
    except Exception as exc:  # noqa: BLE001
        _log(job_id, "warp", f"проверку MASQUE пропустил: {exc.__class__.__name__}", "warn")


def _push_xray(job_id: str, server_id: str, ssh_cm) -> None:
    from .service import _log, files_for

    with session() as db:
        server = db.get(Server, server_id)
        assert server
        files = files_for(db, server)
    config = files.get("/opt/cascade/xray/config.json")
    if not config:
        return
    with ssh_cm as ssh:
        ssh.write_file("/opt/cascade/xray/config.json", config.encode("utf-8"), 0o600)
        ssh.run(
            "if [ -x /opt/cascade/bin/xray ]; then sudo -n env XRAY_LOCATION_ASSET=/opt/cascade/bin /opt/cascade/bin/xray run -test -config /opt/cascade/xray/config.json && sudo -n systemctl restart cascade-xray; fi",
            lambda line: _log(job_id, "warp", line),
        )


def enrich_decision(db, host: str | None, ip: str | None, decision: dict) -> dict:
    """Дополняет тест маршрута: выход, WARP, человекочитаемый путь."""
    servers = {row.id: row for row in db.scalars(select(Server)).all()}
    exit_id = decision.get("exit_id")
    action = decision.get("action") or "cascade"
    chosen = servers.get(exit_id) if exit_id else None
    if chosen is None and action in {"cascade", "group", "chain", "exit"}:
        chosen = next((row for row in servers.values() if row.role == "exit"), None)
    warp = None
    outbound = "direct" if action == "direct" else "block" if action == "block" else "cascade"
    path = []
    entry = next((row for row in servers.values() if row.role == "entry"), None)
    if entry:
        path.append({"id": entry.id, "name": entry.name, "role": "entry", "heroine": heroine_for(entry)})
    label = decision.get("reason") or ""
    if action == "direct":
        outbound = "direct"
        label = label or "напрямую с входа, без каскада"
    elif action == "block":
        outbound = "block"
        label = label or "блокируется"
    elif chosen:
        path.append({"id": chosen.id, "name": chosen.name, "role": chosen.role, "heroine": heroine_for(chosen)})
        outbound = f"exit-{chosen.id}"
        status = warp_of(chosen)
        used = False
        if status["enabled"]:
            if status["mode"] == "all":
                used = True
                outbound = "warp"
            elif _matches_warp(db, host, ip):
                used = True
                outbound = "warp"
        warp = {**status, "used": used}
        if used:
            path.append({"id": "warp", "name": "Cloudflare WARP", "role": "warp", "heroine": ""})
            label = (label + ". Дальше через Cloudflare WARP").strip(". ")
        else:
            label = label or f"через выход {chosen.name}"
    path.append({"id": "internet", "name": "Интернет", "role": "internet", "heroine": ""})
    decision.update(
        {
            "outbound": outbound,
            "exit_id": chosen.id if chosen else decision.get("exit_id"),
            "exit_name": chosen.name if chosen else "",
            "exit_country": chosen.country if chosen else "",
            "heroine": heroine_for(chosen) if chosen else (heroine_for(entry) if entry else ""),
            "label": label,
            "path": path,
            "warp": warp,
        }
    )
    return decision


def _matches_warp(db, host: str | None, ip: str | None) -> bool:
    catalog = Catalog()
    for row in warp_site_rows(db):
        if row.excluded:
            continue
        clause: dict
        if row.kind == "geosite":
            clause = {"geosite": row.value}
        elif row.kind == "geoip":
            clause = {"geoip": row.value}
        elif row.kind == "cidr":
            clause = {"cidr": row.value}
        else:
            clause = {"domain": row.value}
        if _match_clause(clause, host, ip, catalog):
            return True
    return False


def flow_view(db) -> dict:
    """Данные живой схемы: клиенты → вход → прямая ветка и каскад → выходы → WARP → интернет."""
    servers = list(db.scalars(select(Server)).all())
    clients = list(db.scalars(select(Client)).all())
    entry = next((s for s in servers if s.role == "entry"), None)
    standby_entry = next((s for s in servers if s.role == "standby_entry"), None)
    transit = next((s for s in servers if s.role == "transit"), None)
    exits = [s for s in servers if s.role in EXIT_ROLES]
    rules = []
    try:
        from .service import _rules

        rules = _rules(db)
    except Exception:  # noqa: BLE001
        rules = []
    has_direct = any(rule.enabled and rule.action == "direct" for rule in rules) or True

    def node(server: Server | None, extra: dict | None = None) -> dict | None:
        if server is None:
            return None
        proto = []
        ports = []
        if server.awg_port:
            proto.append("AWG")
            ports.append({"name": "AWG", "port": server.awg_port, "proto": "udp"})
        if server.reality_port:
            proto.append("VLESS")
            ports.append({"name": "VLESS Reality", "port": server.reality_port, "proto": "tcp"})
        status = server.status if server.status in {"online", "offline", "error"} else "unknown"
        if server.role == "standby_exit" and status == "online":
            kind = "standby"
        elif status == "online":
            kind = "active"
        elif status in {"offline", "error"}:
            kind = "down"
        else:
            kind = "standby" if server.role in {"standby_entry", "standby_exit"} else "unknown"
        data = {
            "id": server.id,
            "name": server.name,
            "role": server.role,
            "country": server.country,
            "status": status,
            "kind": kind,
            "heroine": heroine_for(server),
            "protocols": proto,
            "ports": ports,
            "warp": warp_of(server) if server.role in EXIT_ROLES else None,
            "protect": protections_of(server) if server.role in EXIT_ROLES else None,
        }
        if extra:
            data.update(extra)
        return data

    def edge(src: str, dst: str, kind: str, *, active: bool, label: str = "", style: str = "") -> dict:
        return {"from": src, "to": dst, "kind": kind, "active": active, "label": label, "style": style or kind}

    client_nodes = [
        {
            "id": f"client-{row.id}",
            "name": row.name,
            "role": "client",
            "kind": "client",
            "status": "online" if row.online else "offline",
            "protocol": row.protocol,
            "heroine": "",
        }
        for row in clients[:12]
    ]
    if not client_nodes:
        client_nodes = [{"id": "clients", "name": "Клиенты", "role": "clients", "kind": "client", "status": "unknown", "protocol": "", "heroine": ""}]

    edges: list[dict] = []
    entry_up = bool(entry and entry.status == "online") or bool(entry and entry.demo)
    for item in client_nodes:
        active = item["status"] == "online" and (entry_up or not entry)
        label = "AWG" if item.get("protocol") == "awg" else "VLESS" if item.get("protocol") == "vless" else "VPN"
        if entry:
            port = ""
            if item.get("protocol") == "awg" and entry.awg_port:
                port = f":{entry.awg_port}"
            elif item.get("protocol") == "vless" and entry.reality_port:
                port = f":{entry.reality_port}"
            edges.append(edge(item["id"], entry.id, "client", active=active, label=f"{label}{port}"))

    if entry:
        edges.append(edge(entry.id, "direct-ru", "direct", active=has_direct and entry_up, label="российские сайты"))
        edges.append(edge("direct-ru", "internet-direct", "direct", active=has_direct and entry_up, label="напрямую"))
        hop = transit or None
        cascade_target = hop.id if hop else None
        if hop:
            hops_up = hop.status == "online" or hop.demo
            edges.append(edge(entry.id, hop.id, "cascade", active=entry_up and hops_up, label="каскад" + (f" :{hop.reality_port}" if hop.reality_port else "")))
        for item in exits:
            src = hop.id if hop else entry.id
            up = item.status == "online" or item.demo
            src_up = (hop.status == "online" or hop.demo) if hop else entry_up
            style = "standby" if item.role == "standby_exit" else "cascade"
            label = "резерв" if item.role == "standby_exit" else "выход"
            if item.reality_port:
                label += f" :{item.reality_port} VLESS"
            edges.append(edge(src, item.id, "cascade", active=src_up and up and item.role == "exit", label=label, style=style))
            warp = warp_of(item)
            if warp["enabled"]:
                warp_id = f"warp-{item.id}"
                warp_active = up and warp.get("connected")
                wlabel = "весь трафик" if warp["mode"] == "all" else "выбранные сайты"
                edges.append(edge(item.id, warp_id, "warp", active=bool(warp_active), label=f"WARP · {wlabel}"))
                edges.append(edge(warp_id, "internet", "warp", active=bool(warp_active), label="Cloudflare"))
                rest = warp["mode"] == "sites"
                edges.append(edge(item.id, "internet", "exit", active=up and rest, label="остальной трафик" if rest else "выход"))
            else:
                edges.append(edge(item.id, "internet", "exit", active=up, label="интернет"))

    specials = [
        {"id": "direct-ru", "name": "Российские сайты", "role": "direct", "kind": "direct", "status": "online" if has_direct else "unknown", "heroine": "", "icon": "direct-ru", "label": "напрямую с входа"},
        {"id": "internet-direct", "name": "Интернет", "role": "internet", "kind": "internet", "status": "online", "heroine": ""},
        {"id": "internet", "name": "Интернет", "role": "internet", "kind": "internet", "status": "online", "heroine": ""},
    ]
    for item in exits:
        warp = warp_of(item)
        if warp["enabled"]:
            specials.append(
                {
                    "id": f"warp-{item.id}",
                    "name": "Cloudflare WARP",
                    "role": "warp",
                    "kind": "warp" if warp.get("connected") else "down",
                    "status": "online" if warp.get("connected") else "offline",
                    "heroine": "",
                    "label": warp.get("egress_ip") or warp.get("status") or "",
                }
            )

    return {
        "clients": client_nodes,
        "entry": node(entry),
        "standby_entry": node(standby_entry),
        "transit": node(transit),
        "exits": [node(item) for item in exits],
        "specials": specials,
        "edges": edges,
        "ssh_via_entry": ssh_via_entry(db),
    }
