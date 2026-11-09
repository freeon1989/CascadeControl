"""Сборка цепочки вход → транзит → выход и выбор свободных портов."""

from __future__ import annotations

from dataclasses import dataclass, field

from .reality import RealityMaterial
from .routing import RuleView
from .xraycfg import entry_config, exit_config, link_outbound, transit_config

ENTRY_ROLES = {"entry"}
TRANSIT_ROLES = {"transit"}
EXIT_ROLES = {"exit", "standby_exit"}


@dataclass
class NodePlan:
    server_id: str
    name: str
    role: str
    host: str
    awg_port: int | None
    reality_port: int | None
    xray: dict | None = None
    notes: list[str] = field(default_factory=list)


def pick_port(used: set[int], preferred: list[int], fallback_start: int = 20000) -> int:
    for port in preferred:
        if port not in used and 1 <= port <= 65535:
            used.add(port)
            return port
    port = fallback_start
    while port in used:
        port += 1
        if port > 65000:
            raise RuntimeError("не осталось свободных портов")
    used.add(port)
    return port


def build_chain(
    nodes: list[dict],
    rules: list[RuleView],
    *,
    strategy: str,
    materials: dict[str, RealityMaterial],
    client_users_by_entry: dict[str, list[dict]],
    tproxy_port: int = 10808,
) -> dict[str, NodePlan]:
    """nodes: id, name, host, role, awg_port, reality_port.

    materials[id] — Reality этого узла (входной клиентский или межсерверный).
    Для транзита и выхода inbound — их собственный material.
    Клиенты VLESS сидят на material входа.
    Межузловая связь: вход подключается к transit.material как пользователь
    с uuid входа (node uuid хранится в materials отдельно через link_user).
    """
    entries = [n for n in nodes if n["role"] == "entry"]
    if len(entries) != 1:
        raise ValueError("нужен ровно один активный вход")
    entry = entries[0]
    transits = [n for n in nodes if n["role"] == "transit"]
    exits = [n for n in nodes if n["role"] in EXIT_ROLES and n["role"] != "standby_exit"]
    standbys = [n for n in nodes if n["role"] == "standby_exit"]
    if not exits:
        raise ValueError("нужен хотя бы один активный выход")

    plans: dict[str, NodePlan] = {}
    # Выходы. На вход каждый активный выход попадает отдельным тегом exit-<id>,
    # чтобы агент на входе выключал его из балансировщика, не переписывая транзит.
    exit_tags = []
    exit_outbounds_direct = []
    transit_upstreams = []
    transit_users = []
    for node in exits + standbys:
        mat = materials[node["id"]]
        users = [{"id": node["accept_uuid"], "flow": mat.flow}]
        plans[node["id"]] = NodePlan(
            server_id=node["id"],
            name=node["name"],
            role=node["role"],
            host=node["host"],
            awg_port=None,
            reality_port=mat.port,
            xray=exit_config(inbound=mat, inbound_users=users),
        )
        if node["role"] != "exit":
            continue
        tag = f"exit-{node['id']}"
        exit_tags.append(tag)
        exit_outbounds_direct.append(link_outbound(tag, node["host"], mat, node["accept_uuid"]))
        # Отдельный пользователь VLESS на транзите выбирает конкретный выход.
        transit_users.append({"id": node["id"], "flow": mat.flow, "email": tag})
        transit_upstreams.append(link_outbound(tag, node["host"], mat, node["accept_uuid"]))

    if not transits:
        hop_outbounds = exit_outbounds_direct
        hop_tags = exit_tags
        entry_notes = ["транзита нет: вход балансирует выходы напрямую"]
    else:
        transit = transits[0]
        tmat = materials[transit["id"]]
        plans[transit["id"]] = NodePlan(
            server_id=transit["id"],
            name=transit["name"],
            role="transit",
            host=transit["host"],
            awg_port=None,
            reality_port=tmat.port,
            xray=transit_config(
                inbound=tmat,
                inbound_users=[{"id": item["id"], "flow": item["flow"], "email": item["email"]} for item in transit_users],
                upstreams=transit_upstreams,
                tags=exit_tags,
                strategy=strategy,
                user_rules=True,
            ),
            notes=[f"выходы: {', '.join(n['name'] for n in exits)}"],
        )
        for extra in transits[1:]:
            plans[extra["id"]] = NodePlan(
                server_id=extra["id"],
                name=extra["name"],
                role="transit",
                host=extra["host"],
                awg_port=None,
                reality_port=materials[extra["id"]].port,
                xray=None,
                notes=["дополнительный транзит сохранён; в этой версии цепочка идёт через первый"],
            )
        hop_outbounds = []
        for node in exits:
            tag = f"exit-{node['id']}"
            hop_outbounds.append(link_outbound(tag, transit["host"], tmat, node["id"]))
        hop_tags = exit_tags
        entry_notes = [f"каскад через {transit['name']}, выход выбирает балансировщик входа"]

    emat = materials[entry["id"]]
    plans[entry["id"]] = NodePlan(
        server_id=entry["id"],
        name=entry["name"],
        role="entry",
        host=entry["host"],
        awg_port=entry.get("awg_port"),
        reality_port=emat.port,
        xray=entry_config(
            client_material=emat,
            client_users=client_users_by_entry.get(entry["id"], []),
            tproxy_port=tproxy_port,
            rules=rules,
            cascade_outbounds=hop_outbounds,
            cascade_tags=hop_tags,
            strategy=strategy,
        ),
        notes=entry_notes,
    )
    for node in nodes:
        if node["role"] == "standby_entry" and node["id"] not in plans:
            plans[node["id"]] = NodePlan(
                server_id=node["id"],
                name=node["name"],
                role="standby_entry",
                host=node["host"],
                awg_port=node.get("awg_port"),
                reality_port=None,
                xray=None,
                notes=["холодный запасной вход: контейнеры не поднимаются, пока роль не сменится"],
            )
    return plans
