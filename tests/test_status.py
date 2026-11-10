import base64
import json
import time

from cascade_panel.db import session
from cascade_panel.models import Client
from cascade_panel.service import set_connector
from cascade_panel.ssh_client import FakeSession
from cascade_panel.status import parse_awg_dump, parse_stats_output, parse_xray_stats, roll_counter


def test_parser_reads_stats_line():
    payload = {
        "cpu": 11.5,
        "ram": "1.2 / 4.0 ГБ",
        "disk": "8 / 40 ГБ",
        "uptime": "2 д 1 ч",
        "docker": "active",
        "agent": "active",
        "xray": "active",
        "awg": {"up": True, "handshakes": 3},
    }
    text = "шум\nCASCADE_STATS " + json.dumps(payload) + "\n"
    assert parse_stats_output(text)["awg"]["handshakes"] == 3


def test_status_refresh_fills_live_columns(client, monkeypatch):
    monkeypatch.setattr("cascade_panel.status.tcp_ping", lambda *_args, **_kwargs: 17)
    client.post("/api/settings/clear-demo")
    created = client.post(
        "/api/servers",
        json={
            "name": "probe",
            "host": "203.0.113.50",
            "ssh_port": 22,
            "ssh_user": "root",
            "auth_type": "password",
            "secret": "secret",
            "role": "entry",
            "country": "DE",
        },
    )
    assert created.status_code == 200, created.text
    server_id = created.json()["id"]
    stats = (
        'CASCADE_STATS {"cpu": 14.2, "ram": "2.0 / 8.0 ГБ", "disk": "9 / 40 ГБ", '
        '"uptime": "3 ч 5 мин", "docker": "active", "agent": "inactive", '
        '"xray": "active", "awg": {"up": true, "handshakes": 2}}'
    )
    holder = {}

    def factory(_server):
        holder["ssh"] = FakeSession(stats_output=stats)
        return holder["ssh"]

    set_connector(factory)
    try:
        response = client.post(f"/api/servers/{server_id}/status")
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["status"] == "online"
        assert body["cpu"] == 14.2
        assert body["ram"] == "2.0 / 8.0 ГБ"
        assert body["disk"] == "9 / 40 ГБ"
        assert body["uptime"] == "3 ч 5 мин"
        assert body["ping_ms"] == 17
        assert body["checked_at"]
        assert body["live"]["docker"] == "active"
        assert body["live"]["xray"] == "active"
        assert body["live"]["awg"]["handshakes"] == 2
        assert "/opt/cascade/stats.sh" in holder["ssh"].files
    finally:
        set_connector(None)


def test_status_marks_offline_when_ssh_fails(client, monkeypatch):
    monkeypatch.setattr("cascade_panel.status.tcp_ping", lambda *_args, **_kwargs: None)
    client.post("/api/settings/clear-demo")
    created = client.post(
        "/api/servers",
        json={
            "name": "down",
            "host": "203.0.113.51",
            "ssh_port": 22,
            "ssh_user": "root",
            "auth_type": "password",
            "secret": "secret",
            "role": "exit",
            "country": "NL",
        },
    )
    assert created.status_code == 200, created.text

    def factory(_server):
        return FakeSession(fail_connect=True)

    set_connector(factory)
    try:
        body = client.post(f"/api/servers/{created.json()['id']}/status")
        assert body.status_code == 200, body.text
        assert body.json()["status"] == "offline"
        assert body.json()["live"]["online"] is False
        assert body.json()["ping_ms"] is None
    finally:
        set_connector(None)


def _key(label: bytes) -> str:
    return base64.b64encode(label).decode("ascii")


def test_awg_dump_skips_the_interface_line():
    client_key = _key(b"k" * 32)
    dump = "\n".join(
        [
            f"{_key(b'p' * 32)}\t{_key(b's' * 32)}\t51820\toff\t4\t10\t50",
            f"{client_key}\t(none)\t203.0.113.8:4242\t10.10.1.2/32\t1700000000\t1000\t2000\toff",
        ]
    )
    peers = parse_awg_dump(dump)
    assert peers == [{"public_key": client_key, "handshake": 1700000000, "rx": 1000, "tx": 2000}]
    assert roll_counter(0, None, 1000) == (1000, 1000, 1000)
    assert roll_counter(1000, 1000, 1400) == (1400, 1400, 400)
    assert roll_counter(1400, 1400, 15) == (1415, 15, 15)


def test_xray_stats_parser_reads_user_counters():
    raw = json.dumps(
        {
            "stat": [
                {"name": "user>>>abc>>>traffic>>>downlink", "value": 9},
                {"name": "user>>>abc>>>traffic>>>uplink", "value": 4},
                {"name": "inbound>>>tproxy-in>>>traffic>>>downlink", "value": 100},
            ]
        }
    )
    assert parse_xray_stats(raw) == [{"user": "abc", "rx": 9, "tx": 4}]
    assert parse_xray_stats("") == []


def _dump(public: str, handshake: int, rx: int, tx: int) -> str:
    return "\n".join(
        [
            f"{_key(b'p' * 32)}\t{_key(b's' * 32)}\t51820\toff\t4\t10\t50",
            f"{public}\t(none)\t203.0.113.8:4242\t10.10.1.2/32\t{handshake}\t{rx}\t{tx}\toff",
        ]
    )


def _stats(dump: str) -> str:
    payload = {
        "cpu": 3,
        "ram": "1 / 2 ГБ",
        "disk": "1 / 10 ГБ",
        "uptime": "1 ч",
        "docker": "active",
        "agent": "active",
        "xray": "active",
        "awg": {"up": True, "handshakes": 1},
        "awg_dump": dump,
        "xray_stats": None,
    }
    return "CASCADE_STATS " + json.dumps(payload)


def test_entry_poll_updates_client_from_awg_dump(client, monkeypatch):
    monkeypatch.setattr("cascade_panel.status.tcp_ping", lambda *_args, **_kwargs: 12)
    client.post("/api/settings/clear-demo")
    created = client.post(
        "/api/servers",
        json={
            "name": "entry",
            "host": "203.0.113.60",
            "ssh_port": 22,
            "ssh_user": "root",
            "auth_type": "password",
            "secret": "secret",
            "role": "entry",
            "country": "RU",
        },
    )
    assert created.status_code == 200, created.text
    server_id = created.json()["id"]
    phone = client.post("/api/clients", json={"name": "Phone", "kind": "phone", "protocol": "awg"})
    assert phone.status_code == 200, phone.text
    other = client.post("/api/clients", json={"name": "Laptop", "kind": "laptop", "protocol": "awg"})
    assert other.status_code == 200, other.text
    vless = client.post("/api/clients", json={"name": "PC", "kind": "pc", "protocol": "vless"})
    assert vless.status_code == 200, vless.text
    with session() as db:
        row = db.get(Client, phone.json()["id"])
        assert row
        public = row.public_key
    now = int(time.time())

    def poll(dump: str):
        set_connector(lambda _server: FakeSession(stats_output=_stats(dump)))
        response = client.post(f"/api/servers/{server_id}/status")
        assert response.status_code == 200, response.text
        return {item["name"]: item for item in client.get("/api/clients").json()}

    try:
        rows = poll(_dump(public, now, 1000, 2000))
        assert rows["Phone"]["online"] is True
        assert rows["Phone"]["rx_bytes"] == 1000
        assert rows["Phone"]["tx_bytes"] == 2000
        assert rows["Phone"]["last_seen"].endswith("Z")
        assert rows["Phone"]["traffic"] == [3000]
        assert rows["Laptop"]["online"] is False
        assert rows["PC"]["online"] is False
        assert rows["PC"]["rx_bytes"] == 0
        overview = client.get("/api/overview").json()
        assert overview["clients_online"] == 1
        assert overview["sparks"]["clients"][-1] == 1
        assert overview["sparks"]["traffic"][-1] == 3000

        rows = poll(_dump(public, now, 1500, 2200))
        assert rows["Phone"]["rx_bytes"] == 1500
        assert rows["Phone"]["tx_bytes"] == 2200
        assert rows["Phone"]["traffic"] == [3000, 700]

        rows = poll(_dump(public, now, 10, 5))
        assert rows["Phone"]["rx_bytes"] == 1510
        assert rows["Phone"]["tx_bytes"] == 2205
        assert rows["Phone"]["traffic"][-1] == 15
        assert rows["Phone"]["online"] is True

        rows = poll(_dump(public, now - 1000, 10, 5))
        assert rows["Phone"]["online"] is False
        assert rows["Phone"]["rx_bytes"] == 1510
        assert rows["Phone"]["traffic"][-1] == 0
        assert client.get("/api/overview").json()["clients_online"] == 0
    finally:
        set_connector(None)
