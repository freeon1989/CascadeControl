"""Пресеты сервисов: домены и подсети, которые правило раскрывает целиком.

Списки рабочие, но не исчерпывающие. Источник пометки в журнале адресов —
``preset:<id>``. Их можно закрепить или исключить в интерфейсе.
"""

from __future__ import annotations

PRESETS: dict[str, dict] = {
    "banks-gov": {
        "title": "Банки и Госуслуги",
        "action": "direct",
        "domains": [
            "gosuslugi.ru",
            "nalog.ru",
            "mos.ru",
            "cbr.ru",
            "sberbank.ru",
            "online.sberbank.ru",
            "sber.ru",
            "vtb.ru",
            "tbank.ru",
            "tinkoff.ru",
            "alfabank.ru",
            "gazprombank.ru",
            "raiffeisen.ru",
            "open.ru",
            "psbank.ru",
            "sovcombank.ru",
            "rshb.ru",
            "mtsbank.ru",
            "pochtabank.ru",
            "nspk.ru",
            "mironline.ru",
            "sbp.nspk.ru",
            "esia.gosuslugi.ru",
            "lk.gosuslugi.ru",
            "gu-st.ru",
            "rosreestr.gov.ru",
            "pfr.gov.ru",
            "sfr.gov.ru",
        ],
        "cidrs": [],
    },
    "youtube": {
        "title": "YouTube",
        "action": "cascade",
        "domains": [
            "youtube.com",
            "youtu.be",
            "googlevideo.com",
            "ytimg.com",
            "ggpht.com",
            "youtubei.googleapis.com",
            "youtubekids.com",
            "youtubemobilesupport.com",
            "youtube-nocookie.com",
            "yt3.ggpht.com",
            "jnn-pa.googleapis.com",
        ],
        "cidrs": [],
    },
    "google-ai": {
        "title": "Gemini / Google AI",
        "action": "cascade",
        "domains": [
            "gemini.google.com",
            "aistudio.google.com",
            "ai.google.dev",
            "generativelanguage.googleapis.com",
            "alkalimakersuite-pa.clients6.google.com",
            "proactivebackend-pa.googleapis.com",
            "bard.google.com",
            "makersuite.google.com",
        ],
        "cidrs": [],
    },
    "openai": {
        "title": "ChatGPT / OpenAI",
        "action": "cascade",
        "domains": [
            "openai.com",
            "chatgpt.com",
            "chat.openai.com",
            "oaistatic.com",
            "oaiusercontent.com",
            "auth0.openai.com",
            "api.openai.com",
            "cdn.openai.com",
            "ab.chatgpt.com",
            "sora.com",
        ],
        "cidrs": [],
    },
    "telegram": {
        "title": "Telegram",
        "action": "cascade",
        "domains": [
            "telegram.org",
            "t.me",
            "telegram.me",
            "tdesktop.com",
            "telegra.ph",
            "telesco.pe",
            "cdn-telegram.org",
        ],
        "cidrs": [
            "91.108.56.0/22",
            "91.108.4.0/22",
            "91.108.8.0/22",
            "91.108.12.0/22",
            "91.108.16.0/22",
            "91.108.20.0/22",
            "91.105.192.0/23",
            "149.154.160.0/20",
            "185.76.151.0/24",
        ],
    },
    "ads": {
        "title": "Реклама",
        "action": "block",
        "domains": [
            "doubleclick.net",
            "googlesyndication.com",
            "googleadservices.com",
            "adservice.google.com",
            "adnxs.com",
            "adsrvr.org",
            "criteo.com",
            "taboola.com",
            "outbrain.com",
            "yandexadexchange.net",
            "an.yandex.ru",
        ],
        "cidrs": [],
    },
}


def list_presets() -> list[dict]:
    rows = []
    for key, item in PRESETS.items():
        rows.append(
            {
                "id": key,
                "title": item["title"],
                "action": item["action"],
                "domains": len(item["domains"]),
                "cidrs": len(item["cidrs"]),
            }
        )
    return rows


def domain_in_preset(preset_id: str, domain: str) -> bool:
    item = PRESETS.get(preset_id)
    if not item:
        return False
    host = domain.lower().rstrip(".")
    for name in item["domains"]:
        if host == name or host.endswith("." + name):
            return True
    return False
