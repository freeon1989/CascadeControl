import json

from cascade_panel.service import set_connector
from cascade_panel.ssh_client import FakeSession
from cascade_panel.status import parse_stats_output


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
