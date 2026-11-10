"""Проверка правил, групп выходов и каскадов перед записью в SQLite."""

from __future__ import annotations

import ipaddress
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Chain, ChainTransit, ExitGroup, ExitGroupMember, RoutingRule, Server
from .presets import PRESETS

MATCH_TYPES = ("domain", "suffix", "ip", "cidr", "list", "preset", "geo", "any")
ACTIONS = ("direct", "cascade", "chain", "group", "exit", "block")
ENTRY_ROLES = {"entry", "standby_entry"}
EXIT_ROLES = {"exit", "standby_exit"}


def validate_match(match: dict) -> dict:
    if "type" not in match:
        if any(key in match for key in ("any", "domain", "domains", "geosite", "geoip", "cidr", "cidrs", "preset", "any_of")):
            return match
        raise ValueError("укажите тип совпадения")
    kind = str(match.get("type") or "")
    value = str(match.get("value") or "").strip()
    if kind not in MATCH_TYPES:
        raise ValueError("неизвестный тип совпадения")
    if kind == "any":
        return {"type": "any", "value": ""}
    if kind in {"domain", "suffix"}:
        if "." not in value or " " in value or "/" in value:
            raise ValueError("нужно доменное имя")
        return {"type": kind, "value": value.lower().rstrip(".")}
    if kind == "ip":
        try:
            ipaddress.ip_address(value)
        except ValueError as exc:
            raise ValueError("нужен IP-адрес") from exc
        return {"type": "ip", "value": value}
    if kind == "cidr":
        try:
            ipaddress.ip_network(value, strict=False)
        except ValueError as exc:
            raise ValueError("нужна сеть CIDR") from exc
        return {"type": "cidr", "value": str(ipaddress.ip_network(value, strict=False))}
    if kind == "list":
        if not value:
            raise ValueError("выберите список адресов")
        return {"type": "list", "value": value}
    if kind == "preset":
        if value not in PRESETS:
            raise ValueError("нет такого пресета")
        return {"type": "preset", "value": value}
    if kind == "geo":
        if not (value.startswith("geosite:") or value.startswith("geoip:")) or len(value.split(":", 1)[1]) < 2:
            raise ValueError("гео пишется как geosite:ru или geoip:ru")
        return {"type": "geo", "value": value}
    raise ValueError("неизвестный тип совпадения")


def validate_action(db: Session, action: str, exit_id: str | None, chain_id: str | None, group_id: str | None) -> None:
    if action not in ACTIONS:
        raise ValueError("неизвестное действие")
    # Пустые цели допустимы: правило можно создать до серверов и выбрать каскад позже.
    if action == "exit" and exit_id:
        server = db.get(Server, exit_id)
        if server is None or server.role not in EXIT_ROLES:
            raise ValueError("для этого действия нужен выход")
    if action == "chain" and chain_id and db.get(Chain, chain_id) is None:
        raise ValueError("выберите каскад")
    if action == "group" and group_id and db.get(ExitGroup, group_id) is None:
        raise ValueError("выберите группу выходов")


def validate_chain(db: Session, entry_id: str, transit_ids: list[str], exit_id: str | None, group_id: str | None) -> None:
    entry = db.get(Server, entry_id)
    if entry is None or entry.role not in ENTRY_ROLES:
        raise ValueError("в каскаде должен быть ровно один вход")
    if bool(exit_id) == bool(group_id):
        raise ValueError("укажите один выход или одну группу выходов")
    seen = {entry_id}
    for transit_id in transit_ids:
        transit = db.get(Server, transit_id)
        if transit is None or transit.role != "transit":
            raise ValueError("в цепочке можно ставить только транзиты")
        if transit_id in seen:
            raise ValueError("узел повторяется в каскаде")
        seen.add(transit_id)
    if exit_id:
        exit_node = db.get(Server, exit_id)
        if exit_node is None or exit_node.role not in EXIT_ROLES:
            raise ValueError("в каскаде нужен хотя бы один выход")
        return
    members = db.scalars(select(ExitGroupMember).where(ExitGroupMember.group_id == group_id)).all()
    if not members:
        raise ValueError("в каскаде нужен хотя бы один выход")
    for member in members:
        node = db.get(Server, member.server_id)
        if node is None or node.role not in EXIT_ROLES:
            raise ValueError("в группе должен быть выход")


def replace_group_members(db: Session, group_id: str, members: list[dict]) -> None:
    if not members:
        raise ValueError("в группе нужен хотя бы один выход")
    seen: set[str] = set()
    for member in members:
        server = db.get(Server, member["server_id"])
        if server is None or server.role not in EXIT_ROLES:
            raise ValueError("в группу можно добавить только выход")
        if server.id in seen:
            raise ValueError("выход уже есть в группе")
        seen.add(server.id)
        weight = int(member.get("weight") or 0)
        if weight < 0 or weight > 100:
            raise ValueError("вес выхода от 0 до 100")
    for old in db.scalars(select(ExitGroupMember).where(ExitGroupMember.group_id == group_id)).all():
        db.delete(old)
    db.flush()
    for member in members:
        db.add(
            ExitGroupMember(
                id=str(uuid.uuid4()),
                group_id=group_id,
                server_id=member["server_id"],
                weight=int(member.get("weight") or 0),
                standby=bool(member.get("standby")),
            )
        )


def replace_transits(db: Session, chain_id: str, transit_ids: list[str]) -> None:
    for old in db.scalars(select(ChainTransit).where(ChainTransit.chain_id == chain_id)).all():
        db.delete(old)
    db.flush()
    for index, transit_id in enumerate(transit_ids, start=1):
        db.add(ChainTransit(id=str(uuid.uuid4()), chain_id=chain_id, server_id=transit_id, position=index))


def group_is_used(db: Session, group_id: str) -> bool:
    return db.scalar(select(Chain).where(Chain.group_id == group_id)) is not None or db.scalar(
        select(RoutingRule).where(RoutingRule.group_id == group_id)
    ) is not None


def chain_is_used(db: Session, chain_id: str) -> bool:
    return db.scalar(select(RoutingRule).where(RoutingRule.chain_id == chain_id)) is not None
