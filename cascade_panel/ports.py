"""Служебные порты. Новым узлам не выдаём общеизвестные VPN-порты.

Уже записанный порт не двигаем: после установки его же показывает ss.
10808 остаётся внутренним tproxy и наружу не публикуется.
"""

from __future__ import annotations

import random

from .bundle import SUB_PORT

PORT_MIN = 1024
PORT_MAX = 65535
HIGH_MIN = 20000
HIGH_MAX = 60000
TPROXY_PORT = 10808
WELL_KNOWN = frozenset(
    {
        22,
        53,
        80,
        443,
        1194,
        2053,
        2096,
        51820,
        51821,
        8080,
        8443,
        TPROXY_PORT,
        SUB_PORT,
    }
)


def random_high_port(used: set[int], rng: random.Random | None = None) -> int:
    picker = rng or random.SystemRandom()
    for _ in range(400):
        port = picker.randrange(HIGH_MIN, HIGH_MAX + 1)
        if port not in used and port not in WELL_KNOWN:
            used.add(port)
            return port
    for port in range(HIGH_MIN, HIGH_MAX + 1):
        if port not in used and port not in WELL_KNOWN:
            used.add(port)
            return port
    raise RuntimeError("в диапазоне 20000-60000 нет свободного порта")


def keep_or_assign(current: int | None, used: set[int], rng: random.Random | None = None) -> int:
    """Записанный порт оставляем, даже если это 443 или 51820. Новый берём из высокого диапазона."""
    if current and 1 <= int(current) <= PORT_MAX:
        return int(current)
    return random_high_port(used, rng)


def subscription_port(server) -> int:
    if getattr(server, "sub_port", None):
        return int(server.sub_port)
    return SUB_PORT


def used_ports_of(server) -> set[int]:
    import json

    try:
        raw = json.loads(server.precheck_json or "{}").get("raw") or {}
    except json.JSONDecodeError:
        return set()
    values = raw.get("used_ports") or []
    found = set()
    for item in values:
        try:
            found.add(int(item))
        except (TypeError, ValueError):
            continue
    return found


def ensure_listen_ports(server, used: set[int] | None = None, rng: random.Random | None = None) -> None:
    """Пустые порты заполняет высоким случайным. Занятые записью номера не трогает."""
    pool = set(used or set())
    role = server.role
    legacy = server.awg_port is not None or server.reality_port is not None or server.hysteria_port is not None
    if role in {"entry", "standby_entry"}:
        server.awg_port = keep_or_assign(server.awg_port, pool, rng)
        pool.add(int(server.awg_port))
        server.reality_port = keep_or_assign(server.reality_port, pool, rng)
        pool.add(int(server.reality_port))
        server.hysteria_port = keep_or_assign(server.hysteria_port, pool, rng)
        pool.add(int(server.hysteria_port))
        if server.sub_port is None and not legacy:
            server.sub_port = keep_or_assign(None, pool, rng)
        return
    server.reality_port = keep_or_assign(server.reality_port, pool, rng)
    pool.add(int(server.reality_port))
    server.hysteria_port = keep_or_assign(server.hysteria_port, pool, rng)


def public_ports(server) -> list[tuple[int, str]]:
    """Порты, которые можно открыть в файрволе. 10808 и выключенный Hysteria2 сюда не входят."""
    rows: list[tuple[int, str]] = []
    if server.role in {"entry", "standby_entry"} and server.awg_port:
        rows.append((int(server.awg_port), "udp"))
    if server.reality_port:
        rows.append((int(server.reality_port), "tcp"))
    if server.role in {"entry", "standby_entry"}:
        rows.append((subscription_port(server), "tcp"))
    clean = []
    seen = set()
    for port, proto in rows:
        if port == TPROXY_PORT or port in seen:
            continue
        seen.add(port)
        clean.append((port, proto))
    return clean


def listening_fields(server) -> list[dict]:
    role = server.role
    rows = []
    if role in {"entry", "standby_entry"}:
        rows.append(
            {
                "id": "awg_port",
                "label": "AWG, клиентский UDP",
                "proto": "udp",
                "value": server.awg_port,
                "client_facing": True,
                "exposed": True,
            }
        )
    rows.append(
        {
            "id": "reality_port",
            "label": "Reality/VLESS, TCP" if role in {"entry", "standby_entry"} else "Reality/VLESS, связь с узлом, TCP",
            "proto": "tcp",
            "value": server.reality_port,
            "client_facing": role in {"entry", "standby_entry"},
            "exposed": True,
        }
    )
    rows.append(
        {
            "id": "hysteria_port",
            "label": "Hysteria2, UDP",
            "proto": "udp",
            "value": server.hysteria_port,
            "client_facing": False,
            "exposed": False,
            "note": "протокол выключен, порт наружу не открывается",
        }
    )
    if role in {"entry", "standby_entry"}:
        rows.append(
            {
                "id": "sub_port",
                "label": "Подписка VLESS, TCP",
                "proto": "tcp",
                "value": server.sub_port or SUB_PORT,
                "client_facing": True,
                "exposed": True,
            }
        )
    return rows


def validate_port_map(incoming: dict[str, int | None], current: dict[str, int | None], busy: set[int]) -> None:
    seen: dict[int, str] = {}
    for key, value in incoming.items():
        if value is None:
            continue
        port = int(value)
        allowed = PORT_MIN <= port <= PORT_MAX or (key == "reality_port" and port == 443)
        if not allowed:
            raise ValueError(f"{key}: порт должен быть от {PORT_MIN} до {PORT_MAX}")
        if port == TPROXY_PORT:
            raise ValueError("10808 занят внутренним tproxy и наружу не открывается")
        if port in seen:
            raise ValueError(f"порт {port} указан дважды")
        seen[port] = key
        owned = {int(item) for item in current.values() if item}
        if port in busy and port not in owned:
            raise ValueError(f"порт {port} занят на сервере")
