"""HTTP API панели. Слушает только тот адрес, который передал процесс (127.0.0.1)."""

from __future__ import annotations

import asyncio
import json
import os
import threading
import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import select

from . import __version__
from .editor import (
    chain_is_used,
    group_is_used,
    replace_group_members,
    replace_transits,
    validate_action,
    validate_chain,
    validate_match,
)
from .models import Chain, ChainTransit, ExitGroup, ExitGroupMember
from .db import init_db, session
from .detect import probe_domain
from .models import AddressEntry, Balance, Client, Job, JobLine, ListSource, RoutingRule, Server, Setting
from .seed import seed_if_empty
from .service import (
    apply_preset_rule,
    chain_summary,
    clear_demo,
    client_view,
    create_client,
    create_server,
    decide,
    export_client,
    ingest_list_text,
    install_worker,
    run_due_sources,
    set_detect_enabled,
    set_preset_enabled,
    upsert_address,
    overview,
    precheck_worker,
    record_detection,
    refresh_source,
    reissue_client,
    remove_worker,
    rule_view,
    server_view,
    settings_view,
    start_job,
    subscription_for_token,
)
from .settings import db_path, seed_enabled, web_dist


class ServerIn(BaseModel):
    name: str
    host: str
    ssh_port: int = 22
    ssh_user: str = "root"
    auth_type: str = "password"
    secret: str
    role: str
    country: str = ""
    link_protocol: str = "vless"


class RoleIn(BaseModel):
    role: str


class ClientIn(BaseModel):
    name: str
    kind: str = "pc"
    protocol: str = "awg"


class RuleIn(BaseModel):
    name: str
    match: dict
    action: str
    exit_id: str | None = None
    chain_id: str | None = None
    group_id: str | None = None
    enabled: bool = True


class RulePatch(BaseModel):
    name: str | None = None
    match: dict | None = None
    action: str | None = None
    exit_id: str | None = None
    chain_id: str | None = None
    group_id: str | None = None
    enabled: bool | None = None


class MemberIn(BaseModel):
    server_id: str
    weight: int = 1
    standby: bool = False


class GroupIn(BaseModel):
    name: str
    members: list[MemberIn]


class ChainIn(BaseModel):
    name: str
    entry_id: str
    transit_ids: list[str] = []
    exit_id: str | None = None
    group_id: str | None = None


class OrderIn(BaseModel):
    ids: list[str]


class BalanceIn(BaseModel):
    strategy: str
    auto_failover: bool = True
    interval_sec: int = Field(default=30, ge=5, le=3600)
    weights: dict[str, int] = Field(default_factory=dict)


class DnsIn(BaseModel):
    dns1: str
    dns2: str


class SourceIn(BaseModel):
    id: str
    title: str
    url: str
    kind: str = "domain"
    action: str = "cascade"
    enabled: bool = True
    interval_hours: int = 24
    selector: str = ""


class AddressIn(BaseModel):
    kind: str = "domain"
    value: str
    action: str = "cascade"


class DetectToggle(BaseModel):
    enabled: bool


class PresetToggle(BaseModel):
    enabled: bool


class DetectIn(BaseModel):
    domain: str


class ClassifyIn(BaseModel):
    domain: str
    status_code: int = 200
    body: str = ""
    probe_status: str | None = None


class ListTextIn(BaseModel):
    text: str


def create_app(database: Path | None = None, do_seed: bool | None = None) -> FastAPI:
    init_db(database or db_path())
    app = FastAPI(title="Cascade Control", version=__version__, docs_url="/api/docs")
    with session() as db:
        if (do_seed if do_seed is not None else seed_enabled()):
            seed_if_empty(db)

    @app.get("/api/health")
    def health() -> dict:
        return {"ok": True, "version": __version__, "bind": "127.0.0.1"}

    @app.get("/api/overview")
    def get_overview() -> dict:
        with session() as db:
            return overview(db)

    @app.get("/api/chain")
    def get_chain() -> dict:
        with session() as db:
            return chain_summary(db)

    @app.get("/api/servers")
    def get_servers() -> list:
        with session() as db:
            rows = db.scalars(select(Server).order_by(Server.created_at)).all()
            return [server_view(row) for row in rows]

    @app.post("/api/servers")
    def post_server(body: ServerIn) -> dict:
        try:
            with session() as db:
                server = create_server(db, body.model_dump())
                return server_view(server)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.post("/api/servers/{server_id}/role")
    def post_role(server_id: str, body: RoleIn) -> dict:
        from .service import _guard_role

        try:
            with session() as db:
                server = db.get(Server, server_id)
                if server is None:
                    raise HTTPException(404, "узел не найден")
                _guard_role(db, body.role, server.id)
                server.role = body.role
                server.config_dirty = True
                return server_view(server)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.post("/api/servers/{server_id}/precheck")
    def post_precheck(server_id: str) -> dict:
        with session() as db:
            if db.get(Server, server_id) is None:
                raise HTTPException(404, "узел не найден")
        return {"job_id": start_job("precheck", server_id, precheck_worker)}

    @app.post("/api/servers/{server_id}/install")
    def post_install(server_id: str) -> dict:
        with session() as db:
            if db.get(Server, server_id) is None:
                raise HTTPException(404, "узел не найден")
        return {"job_id": start_job("install", server_id, install_worker)}

    @app.post("/api/servers/{server_id}/update")
    def post_update(server_id: str) -> dict:
        return post_install(server_id)

    @app.post("/api/servers/{server_id}/reinstall")
    def post_reinstall(server_id: str) -> dict:
        return post_install(server_id)

    @app.delete("/api/servers/{server_id}")
    def delete_server(server_id: str) -> dict:
        with session() as db:
            if db.get(Server, server_id) is None:
                raise HTTPException(404, "узел не найден")
        return {"job_id": start_job("remove", server_id, remove_worker)}

    @app.get("/api/jobs/{job_id}")
    def get_job(job_id: str) -> dict:
        with session() as db:
            job = db.get(Job, job_id)
            if job is None:
                raise HTTPException(404, "задача не найдена")
            lines = db.scalars(select(JobLine).where(JobLine.job_id == job_id).order_by(JobLine.id)).all()
            return {
                "id": job.id,
                "kind": job.kind,
                "status": job.status,
                "server_id": job.server_id,
                "lines": [{"id": line.id, "step": line.step, "level": line.level, "message": line.message} for line in lines],
            }

    @app.get("/api/jobs/{job_id}/stream")
    async def stream_job(job_id: str):
        async def events():
            last = 0
            for _ in range(600):
                with session() as db:
                    job = db.get(Job, job_id)
                    if job is None:
                        yield "data: {\"event\":\"missing\"}\n\n"
                        return
                    lines = db.scalars(
                        select(JobLine).where(JobLine.job_id == job_id, JobLine.id > last).order_by(JobLine.id)
                    ).all()
                    status = job.status
                    payload = [
                        {"id": line.id, "step": line.step, "level": line.level, "message": line.message} for line in lines
                    ]
                for item in payload:
                    last = item["id"]
                    yield "data: " + json.dumps(item, ensure_ascii=False) + "\n\n"
                if status in {"done", "error", "warning"}:
                    yield "data: " + json.dumps({"event": "end", "status": status}) + "\n\n"
                    return
                await asyncio.sleep(0.3)

        return StreamingResponse(events(), media_type="text/event-stream")

    @app.get("/api/journal")
    def journal() -> list:
        with session() as db:
            jobs = db.scalars(select(Job).order_by(Job.created_at.desc())).all()
            result = []
            for job in jobs[:50]:
                lines = db.scalars(select(JobLine).where(JobLine.job_id == job.id).order_by(JobLine.id)).all()
                result.append(
                    {
                        "id": job.id,
                        "kind": job.kind,
                        "status": job.status,
                        "server_id": job.server_id,
                        "created_at": job.created_at.isoformat() if job.created_at else "",
                        "lines": [line.message for line in lines],
                    }
                )
            return result

    @app.get("/api/clients")
    def get_clients() -> list:
        with session() as db:
            entry = db.scalar(select(Server).where(Server.role == "entry"))
            name = entry.name if entry else ""
            rows = db.scalars(select(Client).order_by(Client.created_at)).all()
            return [client_view(row, name) for row in rows]

    @app.post("/api/clients")
    def post_client(body: ClientIn) -> dict:
        try:
            with session() as db:
                client = create_client(db, body.model_dump())
                entry = db.get(Server, client.server_id) if client.server_id else None
                return client_view(client, entry.name if entry else "")
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.get("/api/clients/{client_id}")
    def get_client(client_id: str) -> dict:
        with session() as db:
            client = db.get(Client, client_id)
            if client is None:
                raise HTTPException(404, "клиент не найден")
            entry = db.scalar(select(Server).where(Server.role == "entry"))
            view = client_view(client, entry.name if entry else "")
            view["export"] = export_client(db, client)
            return view

    @app.post("/api/clients/{client_id}/reissue")
    def post_reissue(client_id: str) -> dict:
        with session() as db:
            client = db.get(Client, client_id)
            if client is None:
                raise HTTPException(404, "клиент не найден")
            reissue_client(db, client)
            return {"ok": True}

    @app.delete("/api/clients/{client_id}")
    def delete_client(client_id: str) -> dict:
        with session() as db:
            client = db.get(Client, client_id)
            if client is None:
                raise HTTPException(404, "клиент не найден")
            db.delete(client)
        return {"ok": True}

    @app.get("/api/clients/{client_id}/conf")
    def download_conf(client_id: str):
        with session() as db:
            client = db.get(Client, client_id)
            if client is None:
                raise HTTPException(404, "клиент не найден")
            payload = export_client(db, client)
        if not payload["conf"]:
            raise HTTPException(400, "у этого клиента нет конфига AmneziaWG")
        return PlainTextResponse(
            payload["conf"],
            headers={"Content-Disposition": f'attachment; filename="{client_id}.conf"'},
        )

    @app.get("/sub/{token}")
    def subscription(token: str):
        with session() as db:
            body = subscription_for_token(db, token)
        if body is None:
            raise HTTPException(404, "подписка не найдена")
        return PlainTextResponse(body)

    @app.get("/api/rules")
    def get_rules() -> list:
        with session() as db:
            rows = db.scalars(select(RoutingRule).order_by(RoutingRule.position)).all()
            return [rule_view(row) for row in rows]

    @app.post("/api/rules")
    def post_rule(body: RuleIn) -> dict:
        try:
            match = validate_match(body.match)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        with session() as db:
            try:
                validate_action(db, body.action, body.exit_id, body.chain_id, body.group_id)
            except ValueError as exc:
                raise HTTPException(400, str(exc)) from exc
            positions = list(db.scalars(select(RoutingRule.position)).all())
            rule = RoutingRule(
                id=str(uuid.uuid4()),
                position=(max(positions) + 1) if positions else 1,
                name=body.name.strip(),
                match_json=json.dumps(match, ensure_ascii=False),
                action=body.action,
                exit_id=body.exit_id if body.action == "exit" else None,
                chain_id=body.chain_id if body.action == "chain" else None,
                group_id=body.group_id if body.action == "group" else None,
                enabled=body.enabled,
            )
            db.add(rule)
            entry = db.scalar(select(Server).where(Server.role == "entry"))
            if entry:
                entry.config_dirty = True
            db.flush()
            return rule_view(rule)

    @app.patch("/api/rules/{rule_id}")
    def patch_rule(rule_id: str, body: RulePatch) -> dict:
        with session() as db:
            rule = db.get(RoutingRule, rule_id)
            if rule is None:
                raise HTTPException(404, "правило не найдено")
            if body.name is not None:
                rule.name = body.name
            if body.match is not None:
                try:
                    rule.match_json = json.dumps(validate_match(body.match), ensure_ascii=False)
                except ValueError as exc:
                    raise HTTPException(400, str(exc)) from exc
            if body.action is not None:
                rule.action = body.action
            if body.exit_id is not None:
                rule.exit_id = body.exit_id or None
            if body.chain_id is not None:
                rule.chain_id = body.chain_id or None
            if body.group_id is not None:
                rule.group_id = body.group_id or None
            try:
                validate_action(db, rule.action, rule.exit_id, rule.chain_id, rule.group_id)
            except ValueError as exc:
                raise HTTPException(400, str(exc)) from exc
            if rule.action != "exit":
                rule.exit_id = None
            if rule.action != "chain":
                rule.chain_id = None
            if rule.action != "group":
                rule.group_id = None
            if body.enabled is not None:
                rule.enabled = body.enabled
            entry = db.scalar(select(Server).where(Server.role == "entry"))
            if entry:
                entry.config_dirty = True
            return rule_view(rule)

    @app.put("/api/rules/order")
    def put_order(body: OrderIn) -> list:
        with session() as db:
            rows = {row.id: row for row in db.scalars(select(RoutingRule)).all()}
            if set(body.ids) != set(rows):
                raise HTTPException(400, "список правил не совпадает")
            for index, rule_id in enumerate(body.ids, start=1):
                rows[rule_id].position = index
            ordered = db.scalars(select(RoutingRule).order_by(RoutingRule.position)).all()
            return [rule_view(row) for row in ordered]

    @app.delete("/api/rules/{rule_id}")
    def delete_rule(rule_id: str) -> dict:
        with session() as db:
            rule = db.get(RoutingRule, rule_id)
            if rule is None:
                raise HTTPException(404, "правило не найдено")
            db.delete(rule)
        return {"ok": True}

    def _group_view(db, group: ExitGroup) -> dict:
        members = db.scalars(select(ExitGroupMember).where(ExitGroupMember.group_id == group.id)).all()
        servers = {row.id: row for row in db.scalars(select(Server)).all()}
        return {
            "id": group.id,
            "name": group.name,
            "members": [
                {
                    "server_id": member.server_id,
                    "name": servers[member.server_id].name if member.server_id in servers else "",
                    "role": servers[member.server_id].role if member.server_id in servers else "",
                    "demo": servers[member.server_id].demo if member.server_id in servers else False,
                    "weight": member.weight,
                    "standby": member.standby,
                }
                for member in members
            ],
        }

    def _chain_view(db, chain: Chain) -> dict:
        hops = db.scalars(
            select(ChainTransit).where(ChainTransit.chain_id == chain.id).order_by(ChainTransit.position)
        ).all()
        servers = {row.id: row for row in db.scalars(select(Server)).all()}

        def brief(server_id: str | None) -> dict | None:
            if not server_id or server_id not in servers:
                return None
            node = servers[server_id]
            return {"id": node.id, "name": node.name, "role": node.role, "demo": node.demo}

        return {
            "id": chain.id,
            "name": chain.name,
            "entry_id": chain.entry_id,
            "entry": brief(chain.entry_id),
            "transit_ids": [hop.server_id for hop in hops],
            "transits": [brief(hop.server_id) for hop in hops],
            "exit_id": chain.exit_id,
            "exit": brief(chain.exit_id),
            "group_id": chain.group_id,
        }

    @app.get("/api/groups")
    def get_groups() -> list:
        with session() as db:
            return [_group_view(db, row) for row in db.scalars(select(ExitGroup)).all()]

    @app.post("/api/groups")
    def post_group(body: GroupIn) -> dict:
        with session() as db:
            group = ExitGroup(id=str(uuid.uuid4()), name=body.name.strip())
            if not group.name:
                raise HTTPException(400, "нужно имя группы")
            db.add(group)
            db.flush()
            try:
                replace_group_members(db, group.id, [item.model_dump() for item in body.members])
            except ValueError as exc:
                raise HTTPException(400, str(exc)) from exc
            return _group_view(db, group)

    @app.put("/api/groups/{group_id}")
    def put_group(group_id: str, body: GroupIn) -> dict:
        with session() as db:
            group = db.get(ExitGroup, group_id)
            if group is None:
                raise HTTPException(404, "группа не найдена")
            group.name = body.name.strip() or group.name
            try:
                replace_group_members(db, group.id, [item.model_dump() for item in body.members])
            except ValueError as exc:
                raise HTTPException(400, str(exc)) from exc
            return _group_view(db, group)

    @app.delete("/api/groups/{group_id}")
    def delete_group(group_id: str) -> dict:
        with session() as db:
            group = db.get(ExitGroup, group_id)
            if group is None:
                raise HTTPException(404, "группа не найдена")
            if group_is_used(db, group_id):
                raise HTTPException(400, "группа используется каскадом или правилом")
            for member in db.scalars(select(ExitGroupMember).where(ExitGroupMember.group_id == group_id)).all():
                db.delete(member)
            db.delete(group)
        return {"ok": True}

    @app.get("/api/chains")
    def get_chains() -> list:
        with session() as db:
            return [_chain_view(db, row) for row in db.scalars(select(Chain)).all()]

    @app.post("/api/chains")
    def post_chain(body: ChainIn) -> dict:
        with session() as db:
            try:
                validate_chain(db, body.entry_id, body.transit_ids, body.exit_id, body.group_id)
            except ValueError as exc:
                raise HTTPException(400, str(exc)) from exc
            chain = Chain(
                id=str(uuid.uuid4()),
                name=body.name.strip() or "Каскад",
                entry_id=body.entry_id,
                exit_id=body.exit_id,
                group_id=body.group_id,
            )
            db.add(chain)
            db.flush()
            replace_transits(db, chain.id, body.transit_ids)
            return _chain_view(db, chain)

    @app.put("/api/chains/{chain_id}")
    def put_chain(chain_id: str, body: ChainIn) -> dict:
        with session() as db:
            chain = db.get(Chain, chain_id)
            if chain is None:
                raise HTTPException(404, "каскад не найден")
            try:
                validate_chain(db, body.entry_id, body.transit_ids, body.exit_id, body.group_id)
            except ValueError as exc:
                raise HTTPException(400, str(exc)) from exc
            chain.name = body.name.strip() or chain.name
            chain.entry_id = body.entry_id
            chain.exit_id = body.exit_id
            chain.group_id = body.group_id
            replace_transits(db, chain.id, body.transit_ids)
            return _chain_view(db, chain)

    @app.delete("/api/chains/{chain_id}")
    def delete_chain(chain_id: str) -> dict:
        with session() as db:
            chain = db.get(Chain, chain_id)
            if chain is None:
                raise HTTPException(404, "каскад не найден")
            if chain_is_used(db, chain_id):
                raise HTTPException(400, "каскад используется правилом")
            for hop in db.scalars(select(ChainTransit).where(ChainTransit.chain_id == chain_id)).all():
                db.delete(hop)
            db.delete(chain)
        return {"ok": True}

    @app.post("/api/route/decide")
    def post_decide(host: str | None = None, ip: str | None = None) -> dict:
        with session() as db:
            return decide(db, host, ip)

    @app.get("/api/balance")
    def get_balance() -> dict:
        with session() as db:
            row = db.get(Balance, 1)
            if row is None:
                return {"strategy": "least_ping", "auto_failover": True, "interval_sec": 30, "weights": {}}
            return {
                "strategy": row.strategy,
                "auto_failover": row.auto_failover,
                "interval_sec": row.interval_sec,
                "weights": json.loads(row.weights_json or "{}"),
            }

    @app.put("/api/balance")
    def put_balance(body: BalanceIn) -> dict:
        if body.strategy not in {"least_ping", "weighted", "round_robin"}:
            raise HTTPException(400, "неизвестная стратегия")
        with session() as db:
            row = db.get(Balance, 1)
            if row is None:
                row = Balance(id=1)
                db.add(row)
            row.strategy = body.strategy
            row.auto_failover = body.auto_failover
            row.interval_sec = body.interval_sec
            row.weights_json = json.dumps(body.weights)
            entry = db.scalar(select(Server).where(Server.role == "entry"))
            if entry:
                entry.config_dirty = True
            return {
                "strategy": row.strategy,
                "auto_failover": row.auto_failover,
                "interval_sec": row.interval_sec,
                "weights": body.weights,
            }

    @app.get("/api/addresses")
    def get_addresses() -> list:
        with session() as db:
            rows = db.scalars(select(AddressEntry).order_by(AddressEntry.source_date.desc())).all()
            return [
                {
                    "id": row.id,
                    "kind": row.kind,
                    "value": row.value,
                    "action": row.action,
                    "source": row.source,
                    "source_date": row.source_date,
                    "pinned": row.pinned,
                    "excluded": row.excluded,
                }
                for row in rows[:500]
            ]

    @app.post("/api/addresses/{entry_id}/pin")
    def pin_address(entry_id: str) -> dict:
        with session() as db:
            row = db.get(AddressEntry, entry_id)
            if row is None:
                raise HTTPException(404, "запись не найдена")
            row.pinned = not row.pinned
            return {"pinned": row.pinned}

    @app.post("/api/addresses/{entry_id}/exclude")
    def exclude_address(entry_id: str) -> dict:
        with session() as db:
            row = db.get(AddressEntry, entry_id)
            if row is None:
                raise HTTPException(404, "запись не найдена")
            row.excluded = not row.excluded
            return {"excluded": row.excluded}

    @app.get("/api/sources")
    def get_sources() -> list:
        with session() as db:
            rows = db.scalars(select(ListSource)).all()
            return [
                {
                    "id": row.id,
                    "title": row.title,
                    "url": row.url,
                    "kind": row.kind,
                    "action": row.action,
                    "enabled": row.enabled,
                    "interval_hours": row.interval_hours or 24,
                    "selector": row.selector or "",
                    "last_fetch": row.last_fetch,
                    "last_count": row.last_count,
                    "last_error": row.last_error,
                }
                for row in rows
            ]

    @app.post("/api/sources")
    def post_source(body: SourceIn) -> dict:
        kind = body.kind
        if kind not in {"domain", "cidr", "geosite-dat"}:
            raise HTTPException(400, "тип списка: domain, cidr или geosite-dat")
        if not body.url.startswith("http://") and not body.url.startswith("https://"):
            raise HTTPException(400, "нужен http(s) адрес списка")
        with session() as db:
            if db.get(ListSource, body.id):
                raise HTTPException(400, "такой источник уже есть")
            row = ListSource(
                id=body.id.strip(),
                title=body.title.strip() or body.id.strip(),
                url=body.url.strip(),
                kind=kind,
                action=body.action if body.action in {"direct", "cascade", "block"} else "cascade",
                enabled=body.enabled,
                interval_hours=max(1, min(body.interval_hours, 168)),
                selector=body.selector.strip(),
            )
            db.add(row)
            db.flush()
            return {"id": row.id, "title": row.title}

    @app.put("/api/sources/{source_id}")
    def put_source(source_id: str, body: SourceIn) -> dict:
        with session() as db:
            row = db.get(ListSource, source_id)
            if row is None:
                raise HTTPException(404, "источник не найден")
            row.title = body.title.strip() or row.title
            row.url = body.url.strip() or row.url
            if body.kind in {"domain", "cidr", "geosite-dat"}:
                row.kind = body.kind
            if body.action in {"direct", "cascade", "block"}:
                row.action = body.action
            row.enabled = body.enabled
            row.interval_hours = max(1, min(body.interval_hours, 168))
            row.selector = body.selector.strip()
            return {"id": row.id}

    @app.delete("/api/sources/{source_id}")
    def delete_source(source_id: str) -> dict:
        with session() as db:
            row = db.get(ListSource, source_id)
            if row is None:
                raise HTTPException(404, "источник не найден")
            db.delete(row)
        return {"ok": True}

    @app.post("/api/addresses")
    def post_address(body: AddressIn) -> dict:
        with session() as db:
            if body.kind == "domain":
                from .lists import parse_domain_list

                values = parse_domain_list(body.value)
                kind = "domain"
            elif body.kind == "cidr":
                from .lists import parse_cidr_list

                values = parse_cidr_list(body.value)
                kind = "cidr"
            else:
                raise HTTPException(400, "тип: domain или cidr")
            if len(values) != 1:
                raise HTTPException(400, "нужен один домен или одна сеть")
            action = body.action if body.action in {"direct", "cascade", "block"} else "cascade"
            row = upsert_address(db, kind, values[0], action, "manual")
            if row is None:
                raise HTTPException(400, "адрес в списке исключений")
            return {"id": row.id, "value": row.value, "source": row.source, "source_date": row.source_date}

    @app.delete("/api/addresses/{entry_id}")
    def delete_address(entry_id: str) -> dict:
        with session() as db:
            row = db.get(AddressEntry, entry_id)
            if row is None:
                raise HTTPException(404, "адрес не найден")
            db.delete(row)
        return {"ok": True}

    @app.post("/api/sources/{source_id}/refresh")
    def post_refresh(source_id: str) -> dict:
        with session() as db:
            source = db.get(ListSource, source_id)
            if source is None:
                raise HTTPException(404, "источник не найден")
            try:
                count = refresh_source(db, source)
            except Exception as exc:  # noqa: BLE001
                raise HTTPException(502, str(exc)) from exc
            return {"count": count, "error": source.last_error}

    @app.post("/api/sources/{source_id}/ingest")
    def post_ingest(source_id: str, body: ListTextIn) -> dict:
        with session() as db:
            source = db.get(ListSource, source_id)
            if source is None:
                raise HTTPException(404, "источник не найден")
            try:
                count = ingest_list_text(db, source, body.text)
            except ValueError as exc:
                raise HTTPException(400, str(exc)) from exc
            return {"count": count}

    @app.post("/api/presets/{preset_id}/enabled")
    def post_preset_enabled(preset_id: str, body: PresetToggle) -> dict:
        try:
            with session() as db:
                return set_preset_enabled(db, preset_id, body.enabled)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.put("/api/detect")
    def put_detect(body: DetectToggle) -> dict:
        with session() as db:
            return set_detect_enabled(db, body.enabled)

    @app.post("/api/presets/{preset_id}/apply")
    def post_preset(preset_id: str) -> dict:
        try:
            with session() as db:
                rule = apply_preset_rule(db, preset_id)
                return rule_view(rule)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.post("/api/detect/probe")
    def post_probe(body: DetectIn) -> dict:
        probe = probe_domain(body.domain.strip())
        with session() as db:
            return record_detection(db, body.domain.strip(), probe)

    @app.post("/api/detect/classify")
    def post_classify(body: ClassifyIn) -> dict:
        from .detect import Probe, classify_body

        probe = Probe(body.probe_status, "") if body.probe_status else classify_body(body.status_code, body.body)
        with session() as db:
            return record_detection(db, body.domain.strip(), probe)

    @app.get("/api/settings")
    def get_settings() -> dict:
        with session() as db:
            return settings_view(db)

    @app.put("/api/settings/dns")
    def put_dns(body: DnsIn) -> dict:
        with session() as db:
            for key, value in (("dns1", body.dns1), ("dns2", body.dns2)):
                row = db.get(Setting, key)
                if row is None:
                    db.add(Setting(key=key, value=value))
                else:
                    row.value = value
            return {"dns1": body.dns1, "dns2": body.dns2}

    @app.post("/api/settings/clear-demo")
    def post_clear_demo() -> dict:
        with session() as db:
            clear_demo(db)
        return {"ok": True}

    dist = web_dist()
    index = dist / "index.html"
    if index.exists():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

        @app.get("/")
        def root():
            return FileResponse(index)

        @app.get("/{path:path}")
        def spa(path: str):
            if path.startswith("api/") or path.startswith("sub/"):
                raise HTTPException(404)
            file = dist / path
            if file.is_file():
                return FileResponse(file)
            return FileResponse(index)
    else:

        @app.get("/")
        def root_missing():
            return HTMLResponse(
                "<!doctype html><meta charset=utf-8><title>Cascade Control</title>"
                "<body style='font-family:sans-serif;background:#071018;color:#e7eefb;padding:40px'>"
                "<h1>Cascade Control</h1><p>Интерфейс ещё не собран.</p>"
                "<pre>npm --prefix web ci && npm --prefix web run build</pre>"
                "<p>API уже отвечает на <a style='color:#2ee6c7' href='/api/health'>/api/health</a>.</p>"
            )

    if os.environ.get("CASCADE_SCHEDULER", "1") != "0":

        def _refresh_loop() -> None:
            import time

            time.sleep(45)
            while True:
                try:
                    with session() as db:
                        run_due_sources(db)
                except Exception:
                    pass
                time.sleep(3600)

        threading.Thread(target=_refresh_loop, name="cascade-lists", daemon=True).start()

    return app
