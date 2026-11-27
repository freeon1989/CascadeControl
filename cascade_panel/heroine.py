"""Героиня карточки и схемы: по коду страны, иначе общая."""

from __future__ import annotations

# Есть отдельные портреты, как в AmneziaMonitor. Остальные страны получают общую.
KNOWN = {"RU", "NL", "AT", "DE", "FI", "US", "FR", "GB", "SE", "PL"}


def heroine_path(country: str | None) -> str:
    code = (country or "").strip().upper()
    if code in KNOWN:
        return f"/heroines/heroine_{code.lower()}.webp"
    return "/heroines/heroine_default.webp"
