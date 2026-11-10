"""Операции панели: узлы, клиенты, правила, установка по SSH."""

from __future__ import annotations

import json
import secrets
import threading
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from .awg import AwgParams, generate_params, generate_psk, generate_wg_keypair, render_client_conf, render_server_conf
from .bundle import (
    SUB_PORT,
    agent_document,
    agent_unit,
    awg_start_script,
    render_install_script,
    render_remove_script,
    tproxy_nft,
    xray_document,
    xray_unit,
)
from .chain import build_chain, pick_port
from .crypto import vault
from .db import session
from .detect import Probe, classify, classify_body
from .hysteria import plan as hysteria_plan
from .lists import fetch_text, now_iso, parse_cidr_list, parse_domain_list
from .models import AddressEntry, Balance, Client, Job, JobLine, ListSource, RoutingRule, Server, Setting
from .presets import PRESETS, list_presets
from .qr import png_data_url
from .reality import RealityMaterial, generate_reality, subscription_body, vless_uri
from .redact import redact
from .routing import Catalog, RuleView, evaluate
from .settings import AWG_CIDR, AWG_SERVER_IP, CLIENT_IP_START
from .ssh_client import ParamikoSession, SSHError, SSHTarget
from .vpnuri import build_envelope, build_last_config, encode_vpn_uri

ROLES = {"entry", "standby_entry", "transit", "exit", "standby_exit"}
ENTRY_ROLES = {"entry", "standby_entry"}
PROTOCOL_LABELS = {"awg": "AWG 3.1", "vless": "VLESS Reality"}
_connector = None


def set_connector(factory) -> None:
    global _connector
    _connector = factory


def _box():
    return vault()


def dns_pair(db: Session) -> tuple[str, str]:
    dns1 = db.get(Setting, "dns1")
    dns2 = db.get(Setting, "dns2")
    return (dns1.value if dns1 else "1.1.1.1", dns2.value if dns2 else "1.0.0.1")


def load_params(server: Server) -> AwgParams:
    data = json.loads(server.awg_params_json or "{}")
    data["header_protection_key"] = _box().decrypt(server.hpk_enc)
    return AwgParams.from_json(data)


def _store_params(server: Server, params: AwgParams) -> None:
    dumped = params.to_json()
    server.hpk_enc = _box().encrypt(dumped["header_protection_key"])
    dumped["header_protection_key"] = ""
    server.awg_params_json = json.dumps(dumped)


def _material(server: Server) -> RealityMaterial:
    return RealityMaterial(
        uuid=server.node_uuid,
        private_key=_box().decrypt(server.reality_private_enc),
        public_key=server.reality_public,
        short_id=_box().decrypt(server.reality_short_enc),
        sni=server.reality_sni,
        dest=f"{server.reality_sni}:443",
        fingerprint="chrome",
        flow="xtls-rprx-vision",
        port=server.reality_port or 443,
    )


def _stats(server: Server) -> dict:
    try:
        return json.loads(server.stats_json or "{}")
    except json.JSONDecodeError:
        return {}


def protocol_ids(server: Server) -> list[str]:
    raw = (server.protocols_json or "").strip()
    if raw:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            data = None
        if isinstance(data, list) and data:
            return _normalize_protocols(server.role, data, strict=False)
    if server.role in ENTRY_ROLES:
        return ["awg", "vless"]
    return ["vless"]


def _normalize_protocols(role: str, items, strict: bool = True) -> list[str]:
    chosen: list[str] = []
    for item in items:
        key = str(item).strip().lower()
        if key in {"awg", "awg 3.1", "amneziawg"}:
            key = "awg"
        elif key in {"vless", "vless reality", "reality"}:
            key = "vless"
        if key in PROTOCOL_LABELS and key not in chosen:
            chosen.append(key)
    if "awg" in chosen and role not in ENTRY_ROLES:
        if strict:
            raise ValueError("AmneziaWG включается только на входе")
        chosen = [key for key in chosen if key != "awg"]
    if not chosen:
        if strict:
            raise ValueError("нужен хотя бы один протокол: AWG 3.1 или VLESS Reality")
        return ["awg", "vless"] if role in ENTRY_ROLES else ["vless"]
    return chosen


def server_view(server: Server) -> dict:
    stats = _stats(server)
    ids = protocol_ids(server)
    protocols = [PROTOCOL_LABELS[item] for item in ids]
    return {
        "id": server.id,
        "name": server.name,
        "host": server.host,
        "ssh_port": server.ssh_port,
        "ssh_user": server.ssh_user,
        "auth_type": server.auth_type,
        "role": server.role,
        "country": server.country,
        "status": server.status,
        "demo": server.demo,
        "awg_port": server.awg_port,
        "reality_port": server.reality_port,
        "link_protocol": server.link_protocol,
        "protocols": protocols,
        "protocol_ids": ids,
        "hysteria2": hysteria_plan(),
        "cpu": stats.get("cpu"),
        "ram": stats.get("ram"),
        "disk": stats.get("disk") or "",
        "uptime": stats.get("uptime") or "",
        "ping_ms": stats.get("ping_ms"),
        "checked_at": stats.get("checked_at") or "",
        "live": {
            "online": stats.get("online"),
            "docker": stats.get("docker") or "",
            "agent": stats.get("agent") or "",
            "xray": stats.get("xray") or "",
            "awg": stats.get("awg"),
        },
        "share": stats.get("share"),
        "speed_mbps": stats.get("speed_mbps"),
        "spark": stats.get("spark") or [],
        "config_dirty": server.config_dirty,
        "last_error": server.last_error,
        "precheck": json.loads(server.precheck_json) if server.precheck_json else None,
        "created_at": server.created_at.isoformat() if server.created_at else "",
    }


def client_view(client: Client, entry_name: str = "") -> dict:
    total = client.rx_bytes + client.tx_bytes
    return {
        "id": client.id,
        "name": client.name,
        "kind": client.kind,
        "protocol": client.protocol,
        "online": client.online,
        "enabled": client.enabled,
        "address": client.address,
        "rx_bytes": client.rx_bytes,
        "tx_bytes": client.tx_bytes,
        "total_bytes": total,
        "total_gb": round(total / 1024**3, 1),
        "last_seen": client.last_seen,
        "traffic": json.loads(client.traffic_json or "[]"),
        "entry": entry_name,
        "demo": client.demo,
        "server_id": client.server_id,
    }


def rule_view(rule: RoutingRule) -> dict:
    return {
        "id": rule.id,
        "position": rule.position,
        "name": rule.name,
        "match": json.loads(rule.match_json),
        "action": rule.action,
        "exit_id": rule.exit_id,
        "chain_id": rule.chain_id,
        "group_id": rule.group_id,
        "enabled": rule.enabled,
        "hits": rule.hits,
    }


def _rules(db: Session) -> list[RuleView]:
    rows = db.scalars(select(RoutingRule).order_by(RoutingRule.position)).all()
    return [
        RuleView(
            id=row.id,
            position=row.position,
            enabled=row.enabled,
            name=row.name,
            match=json.loads(row.match_json),
            action=row.action,
            exit_id=row.exit_id,
            chain_id=row.chain_id,
            group_id=row.group_id,
            hits=row.hits,
        )
        for row in rows
    ]


def _active_entry(db: Session) -> Server | None:
    return db.scalar(select(Server).where(Server.role == "entry"))


def _next_address(db: Session) -> str:
    used = {row.address for row in db.scalars(select(Client)).all() if row.address}
    host = int(AWG_SERVER_IP.split(".")[-1])
    for last in range(CLIENT_IP_START, 250):
        if last == host:
            continue
        candidate = ".".join(AWG_SERVER_IP.split(".")[:-1] + [str(last)])
        if candidate not in used:
            return candidate
    raise ValueError("в подсети клиентов не осталось адресов")


def _guard_role(db: Session, role: str, server_id: str | None) -> None:
    if role not in ROLES:
        raise ValueError("неизвестная роль")
    if role in {"entry", "standby_entry"}:
        other = db.scalar(select(Server).where(Server.role == role, Server.id != server_id))
        if other:
            label = "вход" if role == "entry" else "запасной вход"
            raise ValueError(f"{label} уже назначен: {other.name}")


def _issue_node_crypto(server: Server) -> None:
    material = generate_reality(server.reality_port or 443)
    server.reality_public = material.public_key
    server.reality_private_enc = _box().encrypt(material.private_key)
    server.reality_short_enc = _box().encrypt(material.short_id)
    server.reality_sni = material.sni
    server.node_uuid = material.uuid
    server.reality_port = material.port
    if server.role in {"entry", "standby_entry"}:
        keys = generate_wg_keypair()
        params = generate_params()
        server.awg_public = keys.public_b64
        server.awg_private_enc = _box().encrypt(keys.private_b64)
        server.awg_port = server.awg_port or 51820
        _store_params(server, params)


def create_server(db: Session, payload: dict) -> Server:
    role = payload["role"]
    _guard_role(db, role, None)
    if not payload.get("host") or not payload.get("name"):
        raise ValueError("нужны имя и адрес")
    if payload.get("auth_type") not in {"password", "key"}:
        raise ValueError("тип доступа: password или key")
    if not payload.get("secret"):
        raise ValueError("нужен пароль или ключ SSH")
    server = Server(
        id=str(uuid.uuid4()),
        name=payload["name"].strip(),
        host=payload["host"].strip(),
        ssh_port=int(payload.get("ssh_port") or 22),
        ssh_user=(payload.get("ssh_user") or "root").strip(),
        auth_type=payload["auth_type"],
        ssh_secret_enc=_box().encrypt(payload["secret"]),
        role=role,
        country=(payload.get("country") or "").upper()[:8],
        status="unknown",
        demo=False,
        link_protocol=payload.get("link_protocol") or "vless",
        reality_port=443 if role in {"entry", "exit"} else 8443,
        config_dirty=True,
    )
    _issue_node_crypto(server)
    db.add(server)
    db.flush()
    return server


def update_server(db: Session, server: Server, payload: dict) -> Server:
    """Меняет карточку узла и не выпускает ключи заново."""
    name = (payload.get("name") or "").strip()
    host = (payload.get("host") or "").strip()
    if not name or not host:
        raise ValueError("нужны имя и адрес")
    role = payload.get("role") or server.role
    _guard_role(db, role, server.id)
    auth_type = payload.get("auth_type") or server.auth_type
    if auth_type not in {"password", "key"}:
        raise ValueError("тип доступа: password или key")
    secret = payload.get("secret") or ""
    if auth_type != server.auth_type and not str(secret).strip():
        raise ValueError("для нового типа доступа нужен пароль или ключ")
    port = int(payload.get("ssh_port") or server.ssh_port or 22)
    if port < 1 or port > 65535:
        raise ValueError("порт SSH вне диапазона")
    user = (payload.get("ssh_user") or server.ssh_user or "root").strip()
    if not user:
        raise ValueError("нужен пользователь SSH")
    protocols = payload.get("protocols")
    if protocols is None:
        stored = protocol_ids(server)
        if role != server.role:
            stored = _normalize_protocols(role, stored, strict=False)
        server.protocols_json = json.dumps(stored)
    else:
        server.protocols_json = json.dumps(_normalize_protocols(role, protocols))
    server.name = name
    server.host = host
    server.ssh_port = port
    server.ssh_user = user
    server.auth_type = auth_type
    server.role = role
    server.country = str(payload.get("country") or "").upper()[:8]
    if str(secret).strip():
        stored_secret = secret if auth_type == "key" else str(secret).strip()
        server.ssh_secret_enc = _box().encrypt(stored_secret)
    _ensure_crypto(server)
    server.config_dirty = True
    return server


def _ensure_crypto(server: Server) -> None:
    """Дописывает только отсутствующие ключи. Уже выпущенные не меняет."""
    if not server.reality_private_enc or not server.node_uuid:
        material = generate_reality(server.reality_port or 443)
        server.reality_public = material.public_key
        server.reality_private_enc = _box().encrypt(material.private_key)
        server.reality_short_enc = _box().encrypt(material.short_id)
        server.reality_sni = material.sni
        server.node_uuid = material.uuid
        server.reality_port = server.reality_port or material.port
    if server.role in ENTRY_ROLES and not server.awg_private_enc:
        keys = generate_wg_keypair()
        params = generate_params()
        server.awg_public = keys.public_b64
        server.awg_private_enc = _box().encrypt(keys.private_b64)
        server.awg_port = server.awg_port or 51820
        _store_params(server, params)


def refresh_status(server_id: str) -> Server:
    """Один опрос: TCP-пинг с панели и снимок по SSH. Демо-узел не трогает сеть."""
    from .status import STATS_SCRIPT, parse_stats_output, tcp_ping

    with session() as db:
        server = db.get(Server, server_id)
        if server is None:
            raise ValueError("узел не найден")
        demo = server.demo
        host = server.host
        port = server.ssh_port or 22
        previous = _stats(server)
    checked = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    if demo:
        previous["checked_at"] = checked
        previous["online"] = True
        with session() as db:
            server = db.get(Server, server_id)
            assert server
            server.stats_json = json.dumps(previous, ensure_ascii=False)
            if server.status in {"", "unknown"}:
                server.status = "online"
        return server
    ping = tcp_ping(host, port)
    remote: dict | None = None
    error = ""
    try:
        with session() as db:
            server = db.get(Server, server_id)
            assert server
            ssh_cm = connect(server, timeout=12)
        with ssh_cm as ssh:
            ssh.write_file("/opt/cascade/stats.sh", STATS_SCRIPT.encode(), 0o700)
            lines: list[str] = []
            code = ssh.run("bash /opt/cascade/stats.sh", lambda line: lines.append(line))
        if code != 0 and not any("CASCADE_STATS" in line for line in lines):
            raise SSHError("скрипт статуса завершился с ошибкой")
        remote = parse_stats_output("\n".join(lines))
    except Exception as exc:  # noqa: BLE001
        error = redact(str(exc))[:240]
    spark = list(previous.get("spark") or [])
    if remote and remote.get("cpu") is not None:
        spark = (spark + [remote["cpu"]])[-16:]
    merged = dict(previous)
    merged.update(
        {
            "ping_ms": ping,
            "checked_at": checked,
            "online": remote is not None,
            "spark": spark,
            "error": "" if remote is not None else error or "SSH не ответил",
        }
    )
    if remote:
        for key in ("cpu", "ram", "disk", "uptime", "docker", "agent", "xray", "awg"):
            if key in remote:
                merged[key] = remote[key]
    with session() as db:
        server = db.get(Server, server_id)
        assert server
        server.stats_json = json.dumps(merged, ensure_ascii=False)
        server.status = "online" if remote is not None else "offline"
    return server


def refresh_all_status() -> int:
    with session() as db:
        ids = [row.id for row in db.scalars(select(Server)).all() if not row.demo]
    done = 0
    for server_id in ids:
        try:
            refresh_status(server_id)
            done += 1
        except Exception:
            continue
    return done


def connect(server: Server, timeout: float | None = None):
    if server.demo:
        raise SSHError("это демонстрационный узел, SSH к нему не открывается")
    if _connector is not None:
        return _connector(server)
    secret = _box().decrypt(server.ssh_secret_enc)
    target = SSHTarget(server.host, server.ssh_port, server.ssh_user, server.auth_type, secret)
    return ParamikoSession(target, timeout=timeout or 25.0)


def _log(job_id: str, step: str, message: str, level: str = "info") -> None:
    with session() as db:
        db.add(JobLine(job_id=job_id, step=step, level=level, message=redact(message)[:4000]))


def _finish(job_id: str, status: str, server_id: str | None = None, error: str = "") -> None:
    with session() as db:
        job = db.get(Job, job_id)
        if job:
            job.status = status
            job.finished_at = datetime.now(timezone.utc)
        if server_id:
            server = db.get(Server, server_id)
            if server:
                server.last_error = redact(error)[:500]
                if status == "done":
                    server.status = "online"
                    server.config_dirty = False
                elif status == "error":
                    server.status = "error"


def start_job(kind: str, server_id: str | None, worker) -> str:
    job_id = str(uuid.uuid4())
    with session() as db:
        db.add(Job(id=job_id, server_id=server_id, kind=kind, status="running"))
    threading.Thread(target=_guard_worker, args=(job_id, server_id, worker), daemon=True).start()
    return job_id


def _guard_worker(job_id: str, server_id: str | None, worker) -> None:
    try:
        worker(job_id, server_id)
    except Exception as exc:  # noqa: BLE001
        _log(job_id, "error", f"{exc.__class__.__name__}: {exc}", "error")
        _finish(job_id, "error", server_id, str(exc))


def precheck_worker(job_id: str, server_id: str | None) -> None:
    assert server_id
    with session() as db:
        server = db.get(Server, server_id)
        if server is None:
            raise ValueError("узел не найден")
        demo = server.demo
        host = server.host
    if demo:
        _log(job_id, "precheck", f"{host}: демо-узел, SSH не вызывался", "warn")
        _finish(job_id, "done", server_id)
        return
    _log(job_id, "precheck", f"подключение к {host}")
    with session() as db:
        server = db.get(Server, server_id)
        assert server
        ssh_cm = connect(server)
    with ssh_cm as ssh:
        ssh.write_file("/opt/cascade/precheck.sh", __import__("cascade_panel.precheck", fromlist=["PRECHECK_SCRIPT"]).PRECHECK_SCRIPT.encode(), 0o700)
        lines: list[str] = []
        code = ssh.run("bash /opt/cascade/precheck.sh", lambda line: lines.append(line) or _log(job_id, "precheck", line))
    from .precheck import evaluate, parse_precheck_output

    if code != 0 and not any("CASCADE_JSON" in line for line in lines):
        raise SSHError("скрипт проверки завершился с ошибкой")
    report = evaluate(parse_precheck_output("\n".join(lines)))
    with session() as db:
        server = db.get(Server, server_id)
        assert server
        was_dirty = server.config_dirty
        server.precheck_json = json.dumps(report.to_json(), ensure_ascii=False)
        used = set(report.used_ports)
        if server.role in {"entry", "standby_entry"}:
            server.awg_port = pick_port(used, [server.awg_port or 0], 40000)
            prefer = [443] if report.raw.get("port_443_tcp_free") else []
            server.reality_port = pick_port(used, prefer, 8443)
        else:
            server.reality_port = pick_port(used, [server.reality_port or 0] if server.reality_port not in used else [], 2053)
        server.config_dirty = True
    failed = [item for item in report.checks if item.level == "fail"]
    summary = "; ".join(f"{item.title}: {item.detail}" for item in failed)[:500]
    level = "info" if report.ok else "error"
    _log(job_id, "precheck", "проверка пройдена" if report.ok else "есть блокирующие пункты", level)
    _finish(job_id, "done" if report.ok else "error", server_id, "" if report.ok else summary or "предпроверка не пройдена")
    if was_dirty:
        with session() as db:
            server = db.get(Server, server_id)
            if server:
                server.config_dirty = True


def assemble(db: Session):
    servers = list(db.scalars(select(Server)).all())
    entry = next((s for s in servers if s.role == "entry"), None)
    if entry is None:
        raise ValueError("нет активного входа")
    balance = db.get(Balance, 1)
    strategy = balance.strategy if balance else "least_ping"
    materials = {}
    nodes = []
    transits = [s for s in servers if s.role == "transit"]
    first_transit = transits[0] if transits else None
    for server in servers:
        if server.role == "standby_entry":
            nodes.append(
                {
                    "id": server.id,
                    "name": server.name,
                    "host": server.host,
                    "role": server.role,
                    "awg_port": server.awg_port,
                    "accept_uuid": server.node_uuid,
                }
            )
            continue
        if not server.reality_private_enc:
            raise ValueError(f"у {server.name} нет ключей Reality")
        materials[server.id] = _material(server)
        if server.role == "transit":
            accept = entry.node_uuid
        elif server.role in {"exit", "standby_exit"}:
            accept = first_transit.node_uuid if first_transit else entry.node_uuid
        else:
            accept = server.node_uuid
        nodes.append(
            {
                "id": server.id,
                "name": server.name,
                "host": server.host,
                "role": server.role,
                "awg_port": server.awg_port,
                "accept_uuid": accept,
            }
        )
    users = []
    for client in db.scalars(select(Client).where(Client.protocol == "vless", Client.enabled.is_(True))).all():
        if client.vless_uuid:
            users.append({"id": client.vless_uuid, "flow": "xtls-rprx-vision"})
    plans = build_chain(
        nodes,
        _rules(db),
        strategy=strategy,
        materials=materials,
        client_users_by_entry={entry.id: users},
    )
    params = load_params(entry)
    peers = []
    for client in db.scalars(select(Client).where(Client.protocol == "awg", Client.enabled.is_(True))).all():
        if not client.public_key or not client.psk_enc:
            continue
        peers.append(
            {
                "public_key": client.public_key,
                "preshared_key": _box().decrypt(client.psk_enc),
                "address": client.address,
            }
        )
    awg_text = render_server_conf(
        server_private_key=_box().decrypt(entry.awg_private_enc),
        address_cidr=f"{AWG_SERVER_IP}/{AWG_CIDR}",
        listen_port=entry.awg_port or 51820,
        params=params,
        peers=peers,
    )
    weights = json.loads(balance.weights_json) if balance else {}
    exits = []
    for server in servers:
        if server.role not in {"exit", "standby_exit"}:
            continue
        exits.append(
            {
                "id": server.id,
                "name": server.name,
                "host": server.host,
                "port": server.reality_port,
                "weight": int(weights.get(server.id, 0 if server.role == "standby_exit" else 1)),
                "standby": server.role == "standby_exit",
            }
        )
    dns1, dns2 = dns_pair(db)
    sub_clients = []
    for client in db.scalars(select(Client).where(Client.protocol == "vless", Client.enabled.is_(True))).all():
        if not client.vless_uuid or not client.sub_token:
            continue
        link = vless_uri(materials[entry.id], entry.host, client.name, client.vless_uuid)
        sub_clients.append({"token": client.sub_token, "body_b64": subscription_body([link])})
    agent = {
        "interval_sec": balance.interval_sec if balance else 30,
        "strategy": strategy,
        "failover": bool(balance.auto_failover) if balance else True,
        "failure_threshold": 3,
        "xray_config": "/opt/cascade/xray/config.json",
        "xray_service": "cascade-xray",
        "probe_timeout_ms": 3000,
        "transits": [
            {"id": s.id, "name": s.name, "host": s.host, "port": s.reality_port}
            for s in transits
        ],
        "exits": exits,
        "sources": [
            {"id": src.id, "url": src.url, "kind": src.kind, "enabled": src.enabled}
            for src in db.scalars(select(ListSource)).all()
        ],
        "subscription": {
            "listen": f"0.0.0.0:{SUB_PORT}",
            "path": "/sub/",
            "clients": sub_clients,
        },
        "dns": [dns1, dns2],
        "watch_domains": [
            row.value
            for row in db.scalars(select(AddressEntry).where(AddressEntry.kind == "domain", AddressEntry.excluded.is_(False))).all()
            if row.action != "cascade"
        ][:40],
    }
    return plans, awg_text, agent, entry


def files_for(db: Session, server: Server) -> dict[str, str]:
    plans, awg_text, agent, _entry = assemble(db)
    plan = plans.get(server.id)
    if plan is None:
        raise ValueError("узел не попал в цепочку")
    files: dict[str, str] = {}
    enabled = set(protocol_ids(server))
    want_xray = plan.xray is not None and "vless" in enabled
    want_awg = server.role == "entry" and "awg" in enabled
    if want_xray:
        files["/opt/cascade/xray/config.json"] = xray_document(plan.xray)
        files["/opt/cascade/xray/cascade-xray.service"] = xray_unit()
    if server.role == "entry":
        if want_awg:
            files["/opt/cascade/awg/cawg0.conf"] = awg_text
            files["/opt/cascade/awg/start.sh"] = awg_start_script()
            files["/opt/cascade/awg/tproxy.nft"] = tproxy_nft()
        files["/etc/cascade-agent/config.json"] = agent_document(agent)
        files["/opt/cascade/agent/cascade-agent.service"] = agent_unit()
    script = render_install_script(
        role=server.role,
        awg=want_awg,
        xray=want_xray,
        agent=server.role == "entry",
    )
    files["/opt/cascade/install.sh"] = script
    return files


def install_worker(job_id: str, server_id: str | None) -> None:
    assert server_id
    with session() as db:
        server = db.get(Server, server_id)
        if server is None:
            raise ValueError("узел не найден")
        if not server.precheck_json and not server.demo:
            raise ValueError("сначала выполните проверку")
        if server.precheck_json:
            report = json.loads(server.precheck_json)
            if not report.get("ok", False) and not server.demo:
                failed = [item for item in report.get("checks") or [] if item.get("level") == "fail"]
                detail = "; ".join(f"{item.get('title')}: {item.get('detail')}" for item in failed)
                raise ValueError("установка заблокирована: " + (detail or "предпроверка не пройдена"))
        demo = server.demo
        files = None if demo else files_for(db, server)
        binary = None
    if demo:
        _log(job_id, "install", "демо-узел не устанавливается по SSH", "warn")
        _finish(job_id, "done", server_id)
        return
    assert files is not None
    from .settings import agent_binary

    blob = agent_binary()
    if blob.exists() and server_role_needs_agent(server_id):
        binary = blob.read_bytes()
    with session() as db:
        server = db.get(Server, server_id)
        assert server
        ssh_cm = connect(server)
        role = server.role
    with ssh_cm as ssh:
        for path, text in files.items():
            _log(job_id, "upload", f"файл {path}")
            ssh.write_file(path, text.encode("utf-8"), 0o600 if path.endswith((".json", ".conf")) else 0o700)
        if role == "entry":
            if binary:
                ssh.write_file("/opt/cascade/bin/cascade-agent", binary, 0o755)
                _log(job_id, "agent", "бинарник агента записан")
            else:
                _log(job_id, "agent", "бинарник агента не найден локально, служба не запустится", "warn")
        _log(job_id, "install", "запуск установки")
        code = ssh.run("bash /opt/cascade/install.sh", lambda line: _log(job_id, "install", line))
    if code != 0:
        raise SSHError(f"установка завершилась с кодом {code}")
    _finish(job_id, "done", server_id)


def reconfigure_worker(job_id: str, server_id: str | None) -> None:
    """Заново записывает текущий конфиг. Запись сервера в базе не удаляет."""
    _log(job_id, "reconfigure", "перенастройка: текущие настройки записываются заново, сервер из списка не удаляется")
    install_worker(job_id, server_id)


def server_role_needs_agent(server_id: str) -> bool:
    with session() as db:
        server = db.get(Server, server_id)
        return bool(server and server.role == "entry")


def remove_worker(job_id: str, server_id: str | None) -> None:
    assert server_id
    with session() as db:
        server = db.get(Server, server_id)
        if server is None:
            raise ValueError("узел не найден")
        demo = server.demo
        ssh_cm = None if demo else connect(server)
    if ssh_cm is not None:
        script = render_remove_script()
        with ssh_cm as ssh:
            ssh.write_file("/opt/cascade/remove.sh", script.encode(), 0o700)
            code = ssh.run("bash /opt/cascade/remove.sh", lambda line: _log(job_id, "remove", line))
        if code != 0:
            raise SSHError(f"удаление на сервере завершилось с кодом {code}")
    else:
        _log(job_id, "remove", "демо-узел удалён только из локальной базы", "warn")
    with session() as db:
        server = db.get(Server, server_id)
        if server:
            for client in db.scalars(select(Client).where(Client.server_id == server.id)).all():
                client.server_id = None
            db.delete(server)
    _finish(job_id, "done", None)


def export_client(db: Session, client: Client) -> dict:
    entry = _active_entry(db)
    if entry is None:
        raise ValueError("нет входа, экспорт некуда привязать")
    dns1, dns2 = dns_pair(db)
    result = {
        "id": client.id,
        "name": client.name,
        "protocol": client.protocol,
        "conf": "",
        "vpn_uri": "",
        "vless_uri": "",
        "subscription": "",
        "subscription_url": "",
        "qr_conf": "",
        "qr_vpn": "",
        "qr_vless": "",
        "note": "",
    }
    if client.protocol == "awg":
        params = load_params(entry)
        private = _box().decrypt(client.private_enc)
        psk = _box().decrypt(client.psk_enc)
        conf = render_client_conf(
            address=f"{client.address}/32",
            dns1=dns1,
            dns2=dns2,
            private_key=private,
            params=params,
            server_public_key=entry.awg_public,
            preshared_key=psk,
            endpoint_host=entry.host,
            endpoint_port=entry.awg_port or 51820,
        )
        pair_public = client.public_key
        last = build_last_config(
            native_conf=conf,
            host=entry.host,
            port=entry.awg_port or 51820,
            client_ip=client.address,
            client_private=private,
            client_public=pair_public,
            server_public=entry.awg_public,
            psk=psk,
            params=params,
            dns1=dns1,
            dns2=dns2,
        )
        envelope = build_envelope(
            host=entry.host,
            description=client.name,
            port=entry.awg_port or 51820,
            last_config=last,
            dns1=dns1,
            dns2=dns2,
        )
        uri = encode_vpn_uri(envelope)
        result.update(conf=conf, vpn_uri=uri, qr_conf=_safe_qr(conf), qr_vpn=_safe_qr(uri))
    if client.protocol == "vless" or client.vless_uuid:
        material = _material(entry)
        link = vless_uri(material, entry.host, client.name, client.vless_uuid or material.uuid)
        body = subscription_body([link])
        result["vless_uri"] = link
        result["subscription"] = body
        result["subscription_url"] = f"http://{entry.host}:{SUB_PORT}/sub/{client.sub_token}"
        result["qr_vless"] = _safe_qr(link)
    if client.protocol == "vless":
        result["note"] = "Формат .conf AmneziaWG относится к клиентам AWG. Для этого клиента — ссылка VLESS и подписка."
    return result


def _safe_qr(text: str) -> str:
    try:
        return png_data_url(text)
    except Exception as exc:  # noqa: BLE001
        return ""


def subscription_for_token(db: Session, token: str) -> str | None:
    client = db.scalar(select(Client).where(Client.sub_token == token))
    if client is None or client.protocol != "vless":
        return None
    payload = export_client(db, client)
    return payload["subscription"]


def reissue_client(db: Session, client: Client) -> None:
    if client.protocol == "awg":
        keys = generate_wg_keypair()
        client.public_key = keys.public_b64
        client.private_enc = _box().encrypt(keys.private_b64)
        client.psk_enc = _box().encrypt(generate_psk())
    else:
        client.vless_uuid = str(uuid.uuid4())
    client.sub_token = secrets.token_urlsafe(18)
    entry = _active_entry(db)
    if entry:
        entry.config_dirty = True


def create_client(db: Session, payload: dict) -> Client:
    entry = _active_entry(db)
    if entry is None:
        raise ValueError("сначала назначьте вход")
    protocol = payload.get("protocol") or "awg"
    if protocol not in {"awg", "vless"}:
        raise ValueError("протокол: awg или vless")
    client = Client(
        id=str(uuid.uuid4()),
        name=payload["name"].strip(),
        kind=payload.get("kind") or "pc",
        protocol=protocol,
        server_id=entry.id,
        enabled=True,
        online=False,
        sub_token=secrets.token_urlsafe(18),
        traffic_json="[]",
    )
    if protocol == "awg":
        keys = generate_wg_keypair()
        client.address = _next_address(db)
        client.public_key = keys.public_b64
        client.private_enc = _box().encrypt(keys.private_b64)
        client.psk_enc = _box().encrypt(generate_psk())
    else:
        client.vless_uuid = str(uuid.uuid4())
    entry.config_dirty = True
    db.add(client)
    db.flush()
    return client


def upsert_address(db: Session, kind: str, value: str, action: str, source: str) -> AddressEntry | None:
    blocked = db.scalar(
        select(AddressEntry).where(
            AddressEntry.kind == kind, AddressEntry.value == value, AddressEntry.excluded.is_(True)
        )
    )
    if blocked:
        return None
    row = db.scalar(
        select(AddressEntry).where(
            AddressEntry.kind == kind, AddressEntry.value == value, AddressEntry.source == source
        )
    )
    if row:
        if row.pinned:
            return row
        row.action = action
        row.source_date = now_iso()
        return row
    row = AddressEntry(
        id=str(uuid.uuid4()),
        kind=kind,
        value=value,
        action=action,
        source=source,
        source_date=now_iso(),
    )
    db.add(row)
    return row


def ingest_values(db: Session, source: ListSource, kind: str, values: list[str], note: str = "") -> int:
    count = 0
    for value in values:
        if upsert_address(db, kind, value, source.action, f"list:{source.id}"):
            count += 1
    source.last_count = count
    source.last_fetch = now_iso()
    source.last_error = note
    return count


def ingest_list_text(db: Session, source: ListSource, text: str) -> int:
    if source.kind == "domain":
        values = parse_domain_list(text)
        kind = "domain"
    elif source.kind == "cidr":
        values = parse_cidr_list(text)
        kind = "cidr"
    elif source.kind == "geosite-dat":
        raise ValueError("geosite.dat обновляется кнопкой «Обновить сейчас», не текстом")
    else:
        raise ValueError("этот формат списка не разбирается")
    return ingest_values(db, source, kind, values)


def refresh_source(db: Session, source: ListSource) -> int:
    from .lists import fetch_bytes, parse_geosite_dat

    try:
        if source.kind == "geosite-dat":
            parsed = parse_geosite_dat(fetch_bytes(source.url))
            selector = (source.selector or "RU").upper()
            domains = [name for code, names in parsed.items() if code.upper() == selector for name in names]
            note = ""
            if len(domains) > 4000:
                note = f"взяты первые 4000 из {len(domains)}"
                domains = domains[:4000]
            return ingest_values(db, source, "domain", domains, note)
        text = fetch_text(source.url)
    except Exception as exc:  # noqa: BLE001
        from .lists import FetchError

        if isinstance(exc, FetchError):
            source.last_error = exc.as_json()[:2000]
        else:
            message, detail = ("Не удалось скачать список.", redact(str(exc))[:800])
            source.last_error = json.dumps({"message": message, "detail": detail}, ensure_ascii=False)
        source.last_fetch = now_iso()
        raise
    return ingest_list_text(db, source, text)


def run_due_sources(db: Session) -> int:
    from datetime import datetime, timedelta, timezone

    ran = 0
    now = datetime.now(timezone.utc)
    for source in db.scalars(select(ListSource).where(ListSource.enabled.is_(True))).all():
        interval = source.interval_hours or 24
        due = not source.last_fetch
        if source.last_fetch:
            try:
                last = datetime.fromisoformat(source.last_fetch)
                if last.tzinfo is None:
                    last = last.replace(tzinfo=timezone.utc)
                due = now - last >= timedelta(hours=interval)
            except ValueError:
                due = True
        if not due:
            continue
        try:
            refresh_source(db, source)
        except Exception:
            pass
        ran += 1
    return ran


def record_detection(db: Session, domain: str, probe: Probe) -> dict:
    action = classify(probe)
    saved = False
    if action:
        row = upsert_address(db, "domain", domain.lower().rstrip("."), action, "detect")
        saved = row is not None
    last = db.get(Setting, "detect_last")
    stamp = now_iso()
    if last is None:
        db.add(Setting(key="detect_last", value=stamp))
    else:
        last.value = stamp
    status = db.get(Setting, "detect_status")
    text = f"последняя проверка {domain}: {probe.status}"
    if status is None:
        db.add(Setting(key="detect_status", value=text))
    else:
        status.value = text
    return {"domain": domain, "probe": probe.status, "detail": probe.detail, "action": action, "saved": saved}


def decide(db: Session, host: str | None, ip: str | None) -> dict:
    catalog = Catalog()
    for row in db.scalars(select(AddressEntry).where(AddressEntry.excluded.is_(False))).all():
        if row.kind == "domain":
            catalog.geosite.setdefault("manual", set()).add(row.value)
            if row.source.startswith("list:"):
                catalog.lists.setdefault(row.source.split(":", 1)[1], set()).add(row.value)
        elif row.kind == "cidr":
            import ipaddress

            try:
                catalog.extra_cidrs.append((ipaddress.ip_network(row.value, strict=False), row.action))
            except ValueError:
                continue
    decision = evaluate(_rules(db), host, ip, catalog)
    return {
        "rule_id": decision.rule_id,
        "action": decision.action,
        "exit_id": decision.exit_id,
        "reason": decision.reason,
    }


def overview(db: Session) -> dict:
    servers = list(db.scalars(select(Server)).all())
    clients = list(db.scalars(select(Client)).all())
    entry = next((s for s in servers if s.role == "entry"), None)
    standby = next((s for s in servers if s.role == "standby_entry"), None)
    transit = next((s for s in servers if s.role == "transit"), None)
    exits = [s for s in servers if s.role in {"exit", "standby_exit"}]
    stats = _stats(entry) if entry else {}
    online_clients = sum(1 for c in clients if c.online)
    traffic = sum(c.rx_bytes + c.tx_bytes for c in clients)
    by_kind = {}
    for client in clients:
        by_kind.setdefault(client.kind, {"total": 0, "online": 0})
        by_kind[client.kind]["total"] += 1
        if client.online:
            by_kind[client.kind]["online"] += 1
    direct = int(stats.get("direct_share") or 0)
    cascade = int(stats.get("cascade_share") or 0)
    if not direct and not cascade:
        direct, cascade = 0, 100 if exits else 0
    pings = [(_stats(s).get("ping_ms")) for s in exits if _stats(s).get("ping_ms")]
    ping = int(sum(pings) / len(pings)) if pings else None
    nodes_up = sum(1 for s in servers if s.status == "online")
    return {
        "entry": server_view(entry) if entry else None,
        "standby_entry": server_view(standby) if standby else None,
        "transit": server_view(transit) if transit else None,
        "exits": [server_view(s) for s in exits],
        "clients_online": online_clients,
        "clients_total": len(clients),
        "by_kind": by_kind,
        "traffic_bytes": traffic,
        "traffic_gb": round(traffic / 1024**3, 1),
        "ping_ms": ping if ping is not None else stats.get("ping_ms"),
        "nodes_up": nodes_up,
        "nodes_total": len(servers),
        "direct_share": direct or 38 if entry and entry.demo else direct,
        "cascade_share": cascade or (62 if entry and entry.demo else 0),
        "speed_mbps": stats.get("speed_mbps") or 0,
        "demo": bool(entry and entry.demo),
        "sparks": {
            "clients": stats.get("spark") or [online_clients],
            "traffic": stats.get("spark") or [1],
            "ping": stats.get("spark") or [ping or 0],
            "nodes": stats.get("spark") or [nodes_up],
        },
    }


def chain_summary(db: Session) -> dict:
    try:
        plans, _awg, _agent, entry = assemble(db)
    except ValueError as exc:
        return {"ok": False, "error": str(exc), "nodes": []}
    nodes = []
    for plan in plans.values():
        nodes.append(
            {
                "id": plan.server_id,
                "name": plan.name,
                "role": plan.role,
                "host": plan.host,
                "awg_port": plan.awg_port,
                "reality_port": plan.reality_port,
                "notes": plan.notes,
                "has_xray": plan.xray is not None,
            }
        )
    return {"ok": True, "entry": entry.name, "dirty": entry.config_dirty, "nodes": nodes}


def clear_demo(db: Session) -> None:
    from .models import Chain, ChainTransit, ExitGroup, ExitGroupMember

    for model in (JobLine, Job, AddressEntry, RoutingRule, ChainTransit, Chain, ExitGroupMember, ExitGroup, Client, Server):
        db.query(model).delete()
    demo = db.get(Setting, "demo")
    if demo:
        demo.value = "0"
    balance = db.get(Balance, 1)
    if balance:
        balance.weights_json = "{}"


def apply_quick_setup(db: Session) -> dict:
    """Российские сайты напрямую, остальное через каскад, четыре пресета в каскад."""
    from .editor import validate_action, validate_match

    wanted = [
        ("Российские сайты", {"type": "geo", "value": "geosite:ru"}, "direct"),
        ("Всё остальное", {"type": "any", "value": ""}, "cascade"),
    ]
    rules = list(db.scalars(select(RoutingRule).order_by(RoutingRule.position)).all())
    by_name = {row.name: row for row in rules}
    created: list[str] = []
    for name, match, action in wanted:
        if name in by_name:
            continue
        checked = validate_match(match)
        validate_action(db, action, None, None, None)
        positions = [row.position for row in rules]
        position = (max(positions) + 1) if positions else 1
        catch = by_name.get("Всё остальное")
        if name == "Российские сайты" and catch is not None:
            for row in rules:
                if row.position >= catch.position:
                    row.position += 1
            position = catch.position
        rule = RoutingRule(
            id=str(uuid.uuid4()),
            position=position,
            name=name,
            match_json=json.dumps(checked, ensure_ascii=False),
            action=action,
            enabled=True,
        )
        db.add(rule)
        rules.append(rule)
        by_name[name] = rule
        created.append(name)
    presets = ["youtube", "openai", "google-ai", "telegram"]
    for preset_id in presets:
        set_preset_enabled(db, preset_id, True)
    entry = db.scalar(select(Server).where(Server.role == "entry"))
    if entry:
        entry.config_dirty = True
    return {"rules": created, "presets": presets}


def settings_view(db: Session) -> dict:
    from . import __version__
    from .settings import data_dir

    demo = db.get(Setting, "demo")
    dns1, dns2 = dns_pair(db)
    return {
        "version": __version__,
        "bind": "127.0.0.1",
        "data_dir": str(data_dir()),
        "telemetry": False,
        "demo": demo.value == "1" if demo else False,
        "dns1": dns1,
        "dns2": dns2,
        "presets": [
            {**item, "enabled": preset_enabled_map(db).get(item["id"], False)} for item in list_presets()
        ],
        "detect": detect_view(db),
        "hysteria2": hysteria_plan(),
    }


def preset_enabled_map(db: Session) -> dict:
    row = db.get(Setting, "presets_enabled")
    if row is None or not row.value:
        return {}
    try:
        data = json.loads(row.value)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def set_preset_enabled(db: Session, preset_id: str, enabled: bool) -> dict:
    from .presets import PRESETS

    preset = PRESETS.get(preset_id)
    if preset is None:
        raise ValueError("нет такого пресета")
    states = preset_enabled_map(db)
    states[preset_id] = enabled
    row = db.get(Setting, "presets_enabled")
    payload = json.dumps(states)
    if row is None:
        db.add(Setting(key="presets_enabled", value=payload))
    else:
        row.value = payload
    source = f"preset:{preset_id}"
    if enabled:
        for domain in preset["domains"]:
            upsert_address(db, "domain", domain, preset["action"], source)
        for cidr in preset["cidrs"]:
            upsert_address(db, "cidr", cidr, preset["action"], source)
    else:
        for entry in db.scalars(select(AddressEntry).where(AddressEntry.source == source)).all():
            if not entry.pinned:
                db.delete(entry)
    return {"id": preset_id, "enabled": enabled}


def detect_view(db: Session) -> dict:
    enabled = db.get(Setting, "detect_enabled")
    status = db.get(Setting, "detect_status")
    last = db.get(Setting, "detect_last")
    return {
        "enabled": bool(enabled and enabled.value == "1"),
        "status": status.value if status and status.value else "выключено",
        "last_run": last.value if last else "",
    }


def set_detect_enabled(db: Session, enabled: bool) -> dict:
    row = db.get(Setting, "detect_enabled")
    value = "1" if enabled else "0"
    if row is None:
        db.add(Setting(key="detect_enabled", value=value))
    else:
        row.value = value
    text = "включено. Новые домены пишутся в список адресов." if enabled else "выключено"
    status = db.get(Setting, "detect_status")
    if status is None:
        db.add(Setting(key="detect_status", value=text))
    else:
        status.value = text
    return detect_view(db)


def apply_preset_rule(db: Session, preset_id: str) -> RoutingRule:
    preset = PRESETS.get(preset_id)
    if not preset:
        raise ValueError("нет такого пресета")
    positions = [row.position for row in db.scalars(select(RoutingRule)).all()]
    catch_all = db.scalar(select(RoutingRule).where(RoutingRule.match_json.contains('"any": true')))
    position = (max(positions) + 1) if positions else 1
    if catch_all:
        position = catch_all.position
        catch_all.position += 1
    rule = RoutingRule(
        id=str(uuid.uuid4()),
        position=position,
        name=preset["title"],
        match_json=json.dumps({"preset": preset_id}, ensure_ascii=False),
        action=preset["action"],
        enabled=True,
        hits=0,
    )
    db.add(rule)
    entry = _active_entry(db)
    if entry:
        entry.config_dirty = True
    return rule


def classify_manual(status_code: int, body: str) -> Probe:
    return classify_body(status_code, body)
