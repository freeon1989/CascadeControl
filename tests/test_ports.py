import json
import time

from cascade_panel.bundle import firewall_lines, render_install_script
from cascade_panel.ports import WELL_KNOWN, ensure_listen_ports, random_high_port, validate_port_map
from cascade_panel.service import set_connector
from cascade_panel.ssh_client import FakeSession


def _wait(client, job_id: str) -> dict:
    body = {}
    for _ in range(80):
        body = client.get(f"/api/jobs/{job_id}").json()
        if body["status"] != "running":
            return body
        time.sleep(0.05)
    return body


def test_new_ports_avoid_well_known_and_busy_numbers():
    used = {22, 443, 51820, 10808}
    first = random_high_port(used)
    second = random_high_port(used)
    assert first != second
    assert first not in WELL_KNOWN and second not in WELL_KNOWN
    assert 20000 <= first <= 60000

    class Node:
        role = "entry"
        awg_port = None
        reality_port = None
        hysteria_port = None
        sub_port = None

    node = Node()
    ensure_listen_ports(node, set(used))
    assert node.awg_port not in WELL_KNOWN
    assert node.reality_port not in WELL_KNOWN
    assert len({node.awg_port, node.reality_port, node.hysteria_port, node.sub_port}) == 4

    class Old:
        role = "entry"
        awg_port = 51820
        reality_port = 443
        hysteria_port = None
        sub_port = None

    old = Old()
    ensure_listen_ports(old, {51820, 443, 22})
    assert old.awg_port == 51820
    assert old.reality_port == 443
    assert old.sub_port is None


def test_validate_rejects_low_duplicate_and_tproxy():
    try:
        validate_port_map({"reality_port": 80}, {}, set())
        raise AssertionError("low port")
    except ValueError:
        pass
    try:
        validate_port_map({"reality_port": 10808}, {}, set())
        raise AssertionError("tproxy")
    except ValueError:
        pass
    try:
        validate_port_map({"reality_port": 443}, {}, {443})
        raise AssertionError("busy")
    except ValueError:
        pass
    validate_port_map({"reality_port": 443}, {"reality_port": 443}, {443})
    validate_port_map({"reality_port": 25000}, {}, set())


def test_firewall_does_not_publish_tproxy():
    script = "\n".join(firewall_lines([(41234, "udp"), (10808, "tcp"), (25001, "tcp")]))
    assert "ufw allow 41234/udp" in script
    assert "ufw allow 25001/tcp" in script
    assert "ufw allow 10808" not in script
    assert "--dport 10808" not in script
    assert "cascade-public" in script
    assert "inet cascade input" not in script
    installed = render_install_script(role="entry", awg=True, xray=True, agent=True, xray_port=25001, public_ports=[(41234, "udp"), (25001, "tcp")])
    assert "ufw allow 41234/udp" in installed
    assert "ufw allow 10808" not in installed


def test_precheck_assigns_high_ports_and_keeps_them(client):
    client.post("/api/settings/clear-demo")
    created = client.post(
        "/api/servers",
        json={
            "name": "entry",
            "host": "203.0.113.80",
            "ssh_port": 22,
            "ssh_user": "root",
            "auth_type": "password",
            "secret": "secret",
            "role": "entry",
            "country": "RU",
        },
    )
    assert created.status_code == 200, created.text
    assert created.json()["reality_port"] is None
    assert created.json()["awg_port"] is None
    server_id = created.json()["id"]
    report = {
        "virt": "kvm",
        "os_id": "ubuntu",
        "os_version": "24.04",
        "os_pretty": "Ubuntu 24.04",
        "kernel": "6.8.0",
        "disk_free_gb": 20,
        "docker": True,
        "port_443_tcp_free": True,
        "used_ports": [22, 443, 51820, 8443],
        "containers": [],
    }
    set_connector(lambda _server: FakeSession("CASCADE_JSON " + json.dumps(report)))
    try:
        job = client.post(f"/api/servers/{server_id}/precheck").json()
        assert _wait(client, job["job_id"])["status"] == "done"
    finally:
        set_connector(None)
    body = next(item for item in client.get("/api/servers").json() if item["id"] == server_id)
    assert body["awg_port"] not in WELL_KNOWN
    assert body["reality_port"] not in WELL_KNOWN
    assert body["reality_port"] != 443
    saved_awg = body["awg_port"]
    report["used_ports"] = [22, 443, saved_awg, body["reality_port"]]
    set_connector(lambda _server: FakeSession("CASCADE_JSON " + json.dumps(report)))
    try:
        job = client.post(f"/api/servers/{server_id}/precheck").json()
        assert _wait(client, job["job_id"])["status"] == "done"
    finally:
        set_connector(None)
    again = next(item for item in client.get("/api/servers").json() if item["id"] == server_id)
    assert again["awg_port"] == saved_awg
    assert again["reality_port"] == body["reality_port"]


def test_patch_ports_validates_and_reconfigures(client):
    client.post("/api/settings/clear-demo")
    client.post(
        "/api/servers",
        json={
            "name": "entry",
            "host": "203.0.113.80",
            "ssh_port": 22,
            "ssh_user": "root",
            "auth_type": "password",
            "secret": "secret",
            "role": "entry",
            "country": "RU",
        },
    )
    created = client.post(
        "/api/servers",
        json={
            "name": "exit",
            "host": "203.0.113.81",
            "ssh_port": 22,
            "ssh_user": "root",
            "auth_type": "password",
            "secret": "secret",
            "role": "exit",
            "country": "DE",
        },
    )
    server_id = created.json()["id"]
    from cascade_panel.db import session
    from cascade_panel.models import Server

    raw = {
        "virt": "kvm",
        "os_id": "ubuntu",
        "os_version": "24.04",
        "os_pretty": "Ubuntu 24.04",
        "kernel": "6.8.0",
        "kernel_name": "Linux",
        "disk_free_gb": 20,
        "docker": True,
        "port_443_tcp_free": False,
        "used_ports": [22, 443, 25010],
        "containers": [],
        "uid": 0,
        "sudo_nopass": True,
        "tun": True,
    }
    with session() as db:
        row = db.get(Server, server_id)
        row.reality_port = 25010
        row.hysteria_port = 25011
        row.status = "online"
        row.precheck_json = json.dumps({"ok": True, "checks": [], "raw": raw})
    refused = client.patch(f"/api/servers/{server_id}/ports", json={"reality_port": 443})
    assert refused.status_code == 400
    duplicate = client.patch(f"/api/servers/{server_id}/ports", json={"reality_port": 25011})
    assert duplicate.status_code == 400
    set_connector(lambda _server: FakeSession())
    try:
        accepted = client.patch(f"/api/servers/{server_id}/ports", json={"reality_port": 25111})
        assert accepted.status_code == 200, accepted.text
        assert accepted.json()["server"]["reality_port"] == 25111
        assert accepted.json()["clients_need_refresh"] is False
        job_id = accepted.json()["job_id"]
        assert job_id
        body = _wait(client, job_id)
        assert body["status"] == "done", body
        assert any("перенастройка" in line["message"] for line in body["lines"])
    finally:
        set_connector(None)
