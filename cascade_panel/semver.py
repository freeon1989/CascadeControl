"""Сравнение версий вида 1.2.3 (с необязательным v и суффиксом -rc1)."""

from __future__ import annotations

import re

_PATTERN = re.compile(r"^\s*v?(\d+)\.(\d+)(?:\.(\d+))?(?:-([0-9A-Za-z.-]+))?(?:\+[0-9A-Za-z.-]+)?\s*$")


def parse(text: str) -> tuple[int, int, int, tuple] | None:
    """Возвращает (major, minor, patch, ключ_предрелиза) или None, если это не версия."""
    match = _PATTERN.match(text or "")
    if not match:
        return None
    major, minor, patch, pre = match.groups()
    if pre:
        parts = tuple((0, int(x), "") if x.isdigit() else (1, 0, x) for x in pre.split("."))
        key: tuple = (0, parts)
    else:
        key = (1, ())  # релиз старше любого предрелиза той же версии
    return int(major), int(minor), int(patch or 0), key


def compare(a: str, b: str) -> int:
    """-1, 0 или 1. Непонятная версия считается самой старой."""
    left, right = parse(a), parse(b)
    if left is None and right is None:
        return 0
    if left is None:
        return -1
    if right is None:
        return 1
    if left == right:
        return 0
    return -1 if left < right else 1


def is_newer(candidate: str, current: str) -> bool:
    return parse(candidate) is not None and compare(candidate, current) > 0


def normalize(text: str) -> str:
    parsed = parse(text)
    if parsed is None:
        return ""
    return text.strip().lstrip("vV")
