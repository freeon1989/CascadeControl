"""Героиня карточки и схемы: авто по стране/имени или ручной выбор."""

from __future__ import annotations

import re
from typing import Any

# Есть отдельные портреты, как в AmneziaMonitor. Остальные страны получают общую.
KNOWN = {"RU", "NL", "AT", "DE", "FI", "US", "FR", "GB", "SE", "PL", "KZ", "HK", "IL", "IN"}

TITLES = {
    "ru": "Россия",
    "nl": "Нидерланды",
    "at": "Австрия",
    "de": "Германия",
    "fi": "Финляндия",
    "us": "США",
    "fr": "Франция",
    "gb": "Великобритания",
    "se": "Швеция",
    "pl": "Польша",
    "kz": "Казахстан",
    "hk": "Гонконг",
    "il": "Израиль",
    "in": "Индия",
    "default": "Общая",
}

ALIASES: dict[str, tuple[str, ...]] = {
    "ru": (
        "russia", "piter", "spb", "petersburg", "peterburg", "sankt", "moscow", "moskva",
        "питер", "спб", "петербург", "москва", "росси", "санкт",
    ),
    "nl": (
        "amsterdam", "netherlands", "holland", "dutch", "nederland",
        "амстердам", "нидерланд", "голланд",
    ),
    "at": (
        "vena", "vienna", "wien", "austria", "osterreich", "österreich",
        "вена", "австри", "виен",
    ),
    "de": (
        "germany", "deutschland", "frankfurt", "berlin", "munich", "falkenstein", "nuremberg",
        "герман", "франкфурт", "берлин", "мюнхен", "фалькенштайн", "нюрнберг",
    ),
    "fi": ("finland", "suomi", "helsinki", "финлянд", "хельсинки"),
    "us": (
        "usa", "united states", "america", "new york", "charlotte",
        "сша", "америк", "нью-йорк",
    ),
    "fr": ("france", "paris", "франци", "париж"),
    "gb": (
        "united kingdom", "england", "britain", "london", "великобритан", "англи", "британ", "лондон",
    ),
    "se": ("sweden", "stockholm", "швец", "стокгольм"),
    "pl": ("poland", "polska", "warsaw", "warszawa", "польш", "варшав"),
    "kz": ("kazakhstan", "almaty", "astana", "казах", "алматы", "астана"),
    "hk": ("hong kong", "hongkong", "гонконг"),
    "il": ("israel", "tel aviv", "jerusalem", "израил", "тель-авив", "иерусалим"),
    "in": ("india", "mumbai", "delhi", "индия", "индии", "мумба", "дели"),
}

_IP_RE = re.compile(r"^(\d{1,3}\.){3}\d{1,3}$|^[0-9a-f:]+$", re.I)
_SPLIT_RE = re.compile(r"[^a-zа-яё0-9]+", re.I)
AUTO_KEY = "auto"


def resolve_country(*parts: str | None) -> str:
    """Двухбуквенный код из поля страны, имени узла или города. Иначе пусто."""
    blob = " ".join(str(part or "") for part in parts).strip().lower()
    if not blob:
        return ""
    tokens = [tok for tok in _SPLIT_RE.split(blob) if tok]
    for tok in tokens:
        code = tok.upper()
        if code in KNOWN:
            return code
    for code, words in ALIASES.items():
        for word in words:
            if len(word) >= 3 and word in blob:
                return code.upper()
            if tokens and len(word) >= 5 and any(word.startswith(tok) and len(tok) >= 5 for tok in tokens):
                return code.upper()
    return ""


def normalize_key(value: str | None) -> str:
    key = (value or "").strip().lower()
    if key in {"", AUTO_KEY}:
        return AUTO_KEY
    if key == "default" or key.upper() in KNOWN:
        return key
    raise ValueError("нет такой героини")


def heroine_src(key: str) -> str:
    name = "default" if key in {"", AUTO_KEY} else key
    if name != "default" and name.upper() not in KNOWN:
        name = "default"
    return f"/heroines/heroine_{name}.webp"


def heroine_path(country: str | None, name: str | None = "", host: str | None = "") -> str:
    hint = host if host and not _IP_RE.match(host.strip()) else ""
    code = resolve_country(country, name, hint)
    if code in KNOWN:
        return f"/heroines/heroine_{code.lower()}.webp"
    return "/heroines/heroine_default.webp"


def heroine_for(server: Any) -> str:
    try:
        key = normalize_key(getattr(server, "heroine_key", "") or AUTO_KEY)
    except ValueError:
        key = AUTO_KEY
    if key != AUTO_KEY:
        return heroine_src(key)
    host = getattr(server, "host", "") or ""
    return heroine_path(getattr(server, "country", ""), name=getattr(server, "name", ""), host=host)


def catalog() -> list[dict]:
    items = [{"key": AUTO_KEY, "title": "Авто (по стране)", "src": "", "auto": True}]
    for key in ("ru", "nl", "at", "de", "fi", "us", "fr", "gb", "se", "pl", "kz", "hk", "il", "in", "default"):
        items.append({"key": key, "title": TITLES[key], "src": heroine_src(key), "auto": False})
    return items
