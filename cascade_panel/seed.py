"""Демонстрационная топология с настоящими ключами и фиктивными адресами.

Цифры и IP повторяют макеты и не являются боевыми серверами.
"""

from __future__ import annotations

import json
import secrets
import uuid
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from .awg import generate_params, generate_psk, generate_wg_keypair
from .crypto import vault
from .lists import DEFAULT_SOURCES, now_iso
from .models import AddressEntry, Balance, Client, Job, JobLine, ListSource, RoutingRule, Server, Setting
from .reality import generate_reality
from .settings import AWG_SERVER_IP

SPARK = [3, 4, 3, 5, 6, 4, 7, 5, 6, 8, 7, 9]


def _stats(**kwargs) -> str:
    base = {"cpu": 0, "ram": "—", "ping_ms": None, "share": 0, "speed_mbps": 0, "spark": SPARK}
    base.update(kwargs)
    return json.dumps(base)


def seed_if_empty(db: Session) -> None:
    if db.scalar(select(Setting).where(Setting.key == "bootstrapped")):
        return
    box = vault()
    db.add(Setting(key="bootstrapped", value="1"))
    db.add(Setting(key="dns1", value="1.1.1.1"))
    db.add(Setting(key="dns2", value="1.0.0.1"))
    db.add(Setting(key="demo", value="1"))
    db.add(
        Balance(
            id=1,
            strategy="least_ping",
            auto_failover=True,
            interval_sec=30,
            weights_json=json.dumps({}),
        )
    )
    for source in DEFAULT_SOURCES:
        db.add(ListSource(**source))

    entry_keys = generate_wg_keypair()
    params = generate_params()
    entry_reality = generate_reality(443)
    transit_reality = generate_reality(8443)
    de_reality = generate_reality(443)
    nl_reality = generate_reality(443)
    fi_reality = generate_reality(2053)

    def reality_fields(mat) -> dict:
        return {
            "reality_public": mat.public_key,
            "reality_private_enc": box.encrypt(mat.private_key),
            "reality_short_enc": box.encrypt(mat.short_id),
            "reality_sni": mat.sni,
            "node_uuid": mat.uuid,
            "reality_port": mat.port,
        }

    entry_id, transit_id, de_id, nl_id, fi_id = (str(uuid.uuid4()) for _ in range(5))
    servers = [
        Server(
            id=entry_id,
            name="ru-entry",
            host="185.199.108.1",
            ssh_port=22,
            ssh_user="root",
            auth_type="key",
            ssh_secret_enc=box.encrypt("demo-not-a-real-key"),
            role="entry",
            country="RU",
            status="online",
            demo=True,
            awg_port=51820,
            link_protocol="vless",
            awg_public=entry_keys.public_b64,
            awg_private_enc=box.encrypt(entry_keys.private_b64),
            awg_params_json=json.dumps({**params.to_json(), "header_protection_key": ""}),
            hpk_enc=box.encrypt(params.header_protection_key),
            config_dirty=False,
            stats_json=_stats(cpu=18, ram="2.6 / 8 ГБ", ping_ms=None, share=100, speed_mbps=412, direct_share=38, cascade_share=62),
            **reality_fields(entry_reality),
        ),
        Server(
            id=transit_id,
            name="tr-1",
            host="45.77.12.34",
            ssh_user="root",
            auth_type="password",
            ssh_secret_enc=box.encrypt("demo"),
            role="transit",
            country="TR",
            status="online",
            demo=True,
            link_protocol="vless",
            config_dirty=False,
            stats_json=_stats(cpu=12, ram="1.8 / 8 ГБ", ping_ms=23, share=62),
            **reality_fields(transit_reality),
        ),
        Server(
            id=de_id,
            name="de-exit",
            host="88.198.64.11",
            ssh_user="root",
            auth_type="key",
            ssh_secret_enc=box.encrypt("demo"),
            role="exit",
            country="DE",
            status="online",
            demo=True,
            link_protocol="vless",
            config_dirty=False,
            stats_json=_stats(cpu=26, ram="3.2 / 8 ГБ", ping_ms=21, share=35),
            **reality_fields(de_reality),
        ),
        Server(
            id=nl_id,
            name="nl-exit",
            host="185.220.101.55",
            ssh_user="root",
            auth_type="key",
            ssh_secret_enc=box.encrypt("demo"),
            role="exit",
            country="NL",
            status="online",
            demo=True,
            link_protocol="vless",
            config_dirty=False,
            stats_json=_stats(cpu=15, ram="2.1 / 8 ГБ", ping_ms=35, share=27),
            **reality_fields(nl_reality),
        ),
        Server(
            id=fi_id,
            name="fi-exit",
            host="95.217.88.77",
            ssh_user="root",
            auth_type="password",
            ssh_secret_enc=box.encrypt("demo"),
            role="standby_exit",
            country="FI",
            status="offline",
            demo=True,
            link_protocol="vless",
            config_dirty=False,
            stats_json=_stats(cpu=9, ram="1.2 / 8 ГБ", ping_ms=88, share=0),
            **reality_fields(fi_reality),
        ),
    ]
    db.add_all(servers)
    db.flush()
    balance = db.get(Balance, 1)
    assert balance
    balance.weights_json = json.dumps({de_id: 50, nl_id: 50, fi_id: 0})

    clients_spec = [
        ("Роутер дом", "router", "awg", True, 52, f"{AWG_SERVER_IP.rsplit('.', 1)[0]}.14", "09.11.2026 14:32:18", [2, 3, 2, 4, 5, 4, 5, 3]),
        ("Xiaomi 15T Pro", "phone", "vless", True, 18, "", "09.11.2026 13:02:11", [1, 2, 1, 3, 2, 2, 1, 2]),
        ("ПК NAKO", "pc", "awg", True, 31, f"{AWG_SERVER_IP.rsplit('.', 1)[0]}.15", "09.11.2026 12:40:02", [4, 3, 5, 4, 6, 5, 4, 3]),
        ("Ноутбук Kali", "laptop", "awg", False, 4, f"{AWG_SERVER_IP.rsplit('.', 1)[0]}.16", "", [0, 1, 0, 0, 2, 0, 0, 0]),
    ]
    for name, kind, proto, online, gb, address, seen, series in clients_spec:
        keys = generate_wg_keypair()
        db.add(
            Client(
                id=str(uuid.uuid4()),
                name=name,
                kind=kind,
                protocol=proto,
                server_id=entry_id,
                address=address if proto == "awg" else "",
                public_key=keys.public_b64 if proto == "awg" else "",
                private_enc=box.encrypt(keys.private_b64) if proto == "awg" else "",
                psk_enc=box.encrypt(generate_psk()) if proto == "awg" else "",
                vless_uuid=str(uuid.uuid4()) if proto == "vless" else "",
                enabled=True,
                online=online,
                rx_bytes=gb * 1024**3,
                tx_bytes=int(gb * 0.08 * 1024**3),
                last_seen=seen,
                traffic_json=json.dumps(series),
                sub_token=secrets.token_urlsafe(18),
                demo=True,
            )
        )

    rules = [
        ("geosite:ru, geoip:ru", {"any_of": [{"geosite": "ru"}, {"geoip": "ru"}]}, "direct", None, 12842),
        ("Банки и Госуслуги", {"preset": "banks-gov"}, "direct", None, 3671),
        ("youtube.com, googlevideo.com", {"preset": "youtube"}, "cascade", None, 8905),
        ("openai.com, gemini.google.com", {"any_of": [{"preset": "openai"}, {"preset": "google-ai"}]}, "exit", nl_id, 2134),
        ("Реклама", {"preset": "ads"}, "block", None, 15298),
        ("Всё остальное", {"any": True}, "cascade", None, 24736),
    ]
    for index, (name, match, action, exit_id, hits) in enumerate(rules, start=1):
        db.add(
            RoutingRule(
                id=str(uuid.uuid4()),
                position=index,
                name=name,
                match_json=json.dumps(match, ensure_ascii=False),
                action=action,
                exit_id=exit_id,
                enabled=True,
                hits=hits,
            )
        )

    stamp = now_iso()
    samples = [
        ("domain", "youtube.com", "cascade", "preset:youtube", False, False),
        ("domain", "gosuslugi.ru", "direct", "preset:banks-gov", True, False),
        ("domain", "example-blocked.test", "cascade", "detect", False, False),
        ("domain", "ads.example", "block", "manual", False, True),
    ]
    for kind, value, action, source, pinned, excluded in samples:
        db.add(
            AddressEntry(
                id=str(uuid.uuid4()),
                kind=kind,
                value=value,
                action=action,
                source=source,
                source_date=stamp,
                pinned=pinned,
                excluded=excluded,
            )
        )

    job = Job(id=str(uuid.uuid4()), server_id=entry_id, kind="install", status="done")
    db.add(job)
    db.flush()
    for text in (
        "демо-данные загружены, SSH не вызывался",
        "ключи AWG 3.1 и Reality сгенерированы локально",
        "контейнеры amnezia-* в планах удаления отсутствуют",
    ):
        db.add(JobLine(job_id=job.id, step="seed", level="info", message=text))
    job.finished_at = job.created_at + timedelta(seconds=1)
