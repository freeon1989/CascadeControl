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
    },
    {
        "id": "antifilter-ip",
        "title": "antifilter — подсети",
        "url": "https://antifilter.download/list/ip.lst",
        "kind": "cidr",
        "action": "cascade",
        "enabled": True,
    },
    {
        "id": "refilter-domains",
        "title": "re:filter — домены",
        "url": "https://raw.githubusercontent.com/1andrevich/Re-filter-lists/main/domains_all.lst",
        "kind": "domain",
        "action": "cascade",
        "enabled": False,
    },
    {
        "id": "runetfreedom-geosite",
        "title": "runetfreedom geosite.dat",
        "url": "https://raw.githubusercontent.com/runetfreedom/russia-v2ray-rules-dat/release/geosite.dat",
        "kind": "geosite-dat",
        "action": "direct",
        "enabled": False,
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


def fetch_text(url: str, timeout: float = 20.0) -> str:
    req = Request(url, headers={"User-Agent": "cascade-control/0.1"})
    with urlopen(req, timeout=timeout) as resp:  # noqa: S310 — URL задаёт владелец
        data = resp.read(8_000_000)
    return data.decode("utf-8", errors="replace")


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()
