"""Разбор текстовых списков доменов и CIDR. Загрузка по URL — отдельный вызов."""

from __future__ import annotations

import ipaddress
import json
from datetime import datetime, timezone
from urllib.error import URLError
from urllib.request import Request, urlopen

from .tls import certifi_context, explain_fetch_error, is_cert_error, system_context


class FetchError(Exception):
    def __init__(self, message: str, detail: str) -> None:
        super().__init__(message)
        self.message = message
        self.detail = detail

    def as_json(self) -> str:
        return json.dumps({"message": self.message, "detail": self.detail}, ensure_ascii=False)


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


def _read(url: str, timeout: float, context, limit: int) -> bytes:
    req = Request(url, headers={"User-Agent": "cascade-control/0.1"})
    with urlopen(req, timeout=timeout, context=context) as resp:  # noqa: S310 — URL задаёт владелец
        return resp.read(limit)


def _download(url: str, timeout: float, limit: int) -> bytes:
    """Сначала системное хранилище. Если оно не ответило или не доверяет сертификату, берём certifi."""
    try:
        return _read(url, min(timeout, 8), system_context(), limit)
    except (URLError, OSError, TimeoutError) as exc:
        timed_out = "timed out" in str(exc).lower() or isinstance(exc, TimeoutError)
        if not is_cert_error(exc) and not timed_out:
            message, detail = explain_fetch_error(exc)
            raise FetchError(message, detail) from exc
        try:
            return _read(url, timeout, certifi_context(), limit)
        except (URLError, OSError, TimeoutError) as second:
            message, detail = explain_fetch_error(second)
            raise FetchError(message, detail) from second


def fetch_bytes(url: str, timeout: float = 30.0) -> bytes:
    return _download(url, timeout, 20_000_000)


def fetch_text(url: str, timeout: float = 20.0) -> str:
    return _download(url, timeout, 8_000_000).decode("utf-8", errors="replace")


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()
