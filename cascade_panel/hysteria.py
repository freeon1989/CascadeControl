"""Hysteria2 сознательно не разворачивается в этой версии.

Интерфейс оставлен, чтобы установка и карточка сервера могли сказать
«выключено», а не молча пропустить протокол.
"""

from __future__ import annotations


def plan() -> dict:
    return {
        "protocol": "hysteria2",
        "enabled": False,
        "reason": "не входит в первую версию: клиентский доступ закрывают AWG 3.1 и VLESS Reality",
    }
