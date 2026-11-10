"""Разбор текстовых списков доменов и CIDR. Загрузка по URL — отдельный вызов."""

from __future__ import annotations

import ipaddress
from datetime import datetime, timezone
from urllib.request import Request, urlopen


DEFAULT_SOURCES = [
    {
        "id": "antifilter-domains",
        "title": "antifilter — домены",
        "url": "https://antifilter.download/list/domains.lst",
        "kind": "domain",
        "action": "cascade",
        "enabled": True,
        "interval_hours": 24,
        "selector": "",
    },
    {
        "id": "antifilter-ip",
        "title": "antifilter — подсети",
        "url": "https://antifilter.download/list/ip.lst",
        "kind": "cidr",
        "action": "cascade",
        "enabled": True,
        "interval_hours": 24,
        "selector": "",
    },
    {
        "id": "refilter-domains",
        "title": "re:filter — домены",
        "url": "https://raw.githubusercontent.com/1andrevich/Re-filter-lists/main/domains_all.lst",
        "kind": "domain",
        "action": "cascade",
        "enabled": False,
        "interval_hours": 24,
        "selector": "",
    },
    {
        "id": "runetfreedom-geosite",
        "title": "runetfreedom geosite.dat",
        "url": "https://raw.githubusercontent.com/runetfreedom/russia-v2ray-rules-dat/release/geosite.dat",
        "kind": "geosite-dat",
        "action": "direct",
        "enabled": False,
        "interval_hours": 24,
        "selector": "RU",
    },
]


def parse_domain_list(text: str) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip().lower().rstrip(".")
        if not line or " " in line or "/" in line:
            continue
        if line.startswith("regexp:") or line.startswith("full:") or line.startswith("domain:"):
            line = line.split(":", 1)[1]
        if not line or "." not in line:
            continue
        if line not in seen:
            seen.add(line)
            found.append(line)
    return found


def parse_cidr_list(text: str) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        try:
            net = ipaddress.ip_network(line, strict=False)
        except ValueError:
            continue
        rendered = str(net)
        if rendered not in seen:
            seen.add(rendered)
            found.append(rendered)
    return found


def _read_varint(buf: bytes, index: int) -> tuple[int, int]:
    number = 0
    shift = 0
    while index < len(buf) and shift <= 63:
        byte = buf[index]
        index += 1
        number |= (byte & 0x7F) << shift
        if byte < 128:
            return number, index
        shift += 7
    raise ValueError("битый varint в geosite.dat")


def _walk_protobuf(buf: bytes):
    index = 0
    while index < len(buf):
        key, index = _read_varint(buf, index)
        field, wire = key >> 3, key & 7
        if wire == 0:
            value, index = _read_varint(buf, index)
            yield field, value
        elif wire == 2:
            length, index = _read_varint(buf, index)
            end = index + length
            if end > len(buf):
                raise ValueError("битая длина в geosite.dat")
            yield field, buf[index:end]
            index = end
        elif wire == 1:
            index += 8
        elif wire == 5:
            index += 4
        else:
            raise ValueError("неизвестный тип поля protobuf")


def parse_geosite_dat(data: bytes) -> dict[str, list[str]]:
    """Разбор v2ray geosite.dat: список GeoSite, внутри домены.

    Тип Regex пропускается. Plain, Domain и Full попадают в список категории.
    """
    found: dict[str, list[str]] = {}
    for field, payload in _walk_protobuf(data):
        if field != 1 or isinstance(payload, int):
            continue
        code = ""
        domains: list[str] = []
        for inner, value in _walk_protobuf(payload):
            if inner == 1 and isinstance(value, bytes):
                code = value.decode("utf-8", "replace").strip()
            elif inner == 2 and isinstance(value, bytes):
                kind = 0
                name = ""
                for leaf, leaf_value in _walk_protobuf(value):
                    if leaf == 1 and isinstance(leaf_value, int):
                        kind = leaf_value
                    elif leaf == 2 and isinstance(leaf_value, bytes):
                        name = leaf_value.decode("utf-8", "replace").strip().lower().rstrip(".")
                if kind != 1 and name and "." in name and " " not in name:
                    domains.append(name)
        if code:
            bucket = found.setdefault(code, [])
            bucket.extend(domains)
    return found


def fetch_bytes(url: str, timeout: float = 30.0) -> bytes:
    req = Request(url, headers={"User-Agent": "cascade-control/0.1"})
    with urlopen(req, timeout=timeout) as resp:  # noqa: S310 — URL задаёт владелец
        return resp.read(20_000_000)


def fetch_text(url: str, timeout: float = 20.0) -> str:
    req = Request(url, headers={"User-Agent": "cascade-control/0.1"})
    with urlopen(req, timeout=timeout) as resp:  # noqa: S310 — URL задаёт владелец
        data = resp.read(8_000_000)
    return data.decode("utf-8", errors="replace")


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()
