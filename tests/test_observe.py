import json
import time

from cascade_panel.observe import (
    apply_dns_logging,
    archives_to_delete,
    bucketize,
    classify_sites,
    parse_containers,
    parse_dns_log,
    parse_xray_access,
    patch_unbound_conf,
    patch_xray_access,
    root_domain,
)
from cascade_panel.service import set_connector
from cascade_panel.ssh_client import FakeSession
from cascade_panel.status import STATS_SCRIPT


CONF = """server:
    interface: 0.0.0.0
    verbosity: 0
    port: 53
remote-control:
    control-enable: no
"""


def test_dns_parser_skips_ptr_and_groups_sites():
    text = "\n".join(
        [
            '{"T":"2026-10-31T19:00:00+00:00","QH":"cdn.example.com","QT":"A","IP":"10.10.1.2"}',
            "[1800000000] unbound[1:0] info: 10.10.1.3 www.example.co.uk. A IN",
            "[1800000000] unbound[1:0] info: 10.10.1.3 1.0.8.10.in-addr.arpa. PTR IN",
            "[1800000000] unbound[1:0] info: start of service",
            "garbage",
        ]
    )
    rows = parse_dns_log(text)
    assert [row["domain"] for row in rows] == ["cdn.example.com", "www.example.co.uk"]
    assert rows[0]["ip"] == "10.10.1.2"
    assert rows[1]["ts"] == 1800000000.0
    assert root_domain("cdn.example.com") == "example.com"
    assert root_domain("www.example.co.uk") == "example.co.uk"
    access = "2026/11/10 12:00:00 from 10.10.1.2:1 accepted tcp:www.example.net:443 [tproxy-in >> direct]"
    assert parse_xray_access(access)[0]["domain"] == "www.example.net"
    assert parse_xray_access("from 10.10.1.2:1 accepted tcp:1.2.3.4:443") == []


def test_site_status_names():
    assert classify_sites(role="exit", has_dns_container=False, container_up=False, log_queries="missing", dns_error="", xray_enabled=False, xray_error="", parsed=0) == "no_container"
    assert classify_sites(role="entry", has_dns_container=False, container_up=False, log_queries="missing", dns_error="", xray_enabled=False, xray_error="", parsed=0) == "not_enabled"
    assert classify_sites(role="entry", has_dns_container=True, container_up=True, log_queries="no", dns_error="", xray_enabled=False, xray_error="", parsed=0) == "not_enabled"
    assert classify_sites(role="entry", has_dns_container=True, container_up=True, log_queries="yes", dns_error="", xray_enabled=False, xray_error="", parsed=0) == "empty"
    assert classify_sites(role="entry", has_dns_container=True, container_up=False, log_queries="yes", dns_error="unreadable", xray_enabled=False, xray_error="", parsed=0) == "unreadable"
    assert classify_sites(role="entry", has_dns_container=True, container_up=True, log_queries="yes", dns_error="", xray_enabled=False, xray_error="", parsed=2) == "ok"


def test_unbound_and_xray_patches_keep_the_rest():
    enabled = patch_unbound_conf(CONF, True)
    assert "log-queries: yes" in enabled
    assert "logfile: unbound.log" in enabled
    assert "use-syslog: no" in enabled
    assert "remote-control:" in enabled
    disabled = patch_unbound_conf(enabled, False)
    assert "log-queries: no" in disabled
    assert 'logfile: ""' in disabled
    original = {"log": {"loglevel": "warning"}, "routing": {"rules": [{"network": "tcp,udp"}]}, "inbounds": [{"port": 443}]}
    patched = patch_xray_access(original, True)
    assert patched["log"]["access"].endswith("access.log")
    assert patched["routing"] == original["routing"]
    assert patched["inbounds"] == original["inbounds"]
    assert "access" not in patch_xray_access(patched, False)["log"]


def test_archive_cap_drops_oldest_first():
    files = [("a", 30, 10.0), ("b", 30, 20.0), ("c", 30, 30.0)]
    assert archives_to_delete(files, now=100, retention_hours=1, cap_bytes=50) == ["a", "b"]
    assert archives_to_delete([("old", 1, 1.0)], now=10_000, retention_hours=1, cap_bytes=100) == ["old"]
    assert bucketize([(0, 1), (10, 3), (55, 5)], 0, 100, 2) == [2.0, 5.0]


def test_containers_ignore_foreign_names():
    text = "amnezia-dns|Up 3 hours|amneziavpn/dns\ncascade-xray|Exited|xray\nnginx|Up|nginx"
    rows = parse_containers(text)
    assert [row["name"] for row in rows] == ["amnezia-dns", "cascade-xray"]
    assert rows[1]["up"] is False


class MemSSH:
    def __init__(self, replies: dict[str, tuple[int, str]]):
        self.replies = replies
        self.files: dict[str, bytes] = {}
        self.commands: list[str] = []

    def run(self, command: str, on_line) -> int:
        self.commands.append(command)
        for key, (code, text) in self.replies.items():
            if key in command:
                for line in text.splitlines():
                    on_line(line)
                return code
        return 0

    def write_file(self, path: str, content: bytes, mode: int = 0o600) -> None:
        self.files[path] = content


def test_enable_unbound_does_not_touch_awg():
    ssh = MemSSH(
        {
            "docker ps": (0, "amnezia-dns|Up 2 hours|amneziavpn/dns"),
            "unbound.conf": (0, CONF),
            "cat /etc/cascade/log-rotate.json": (1, ""),
        }
    )
    lines = apply_dns_logging(ssh, enable=True, role="exit", retention_hours=24, cap_mb=50, disk_free=10**10)
    assert any("amnezia-dns" in line for line in lines)
    saved = ssh.files["/tmp/cascade-unbound.conf"].decode()
    assert "log-queries: yes" in saved
    assert "logfile: unbound.log" in saved
    spec = json.loads(ssh.files["/tmp/cascade-log-rotate.json"])
    assert spec["jobs"][0]["prefix"] == "unbound-"
    assert spec["jobs"][0]["cap_bytes"] == 50 * 1024 * 1024
    assert any("docker restart amnezia-dns" in command for command in ssh.commands)
    joined = "\n".join(ssh.commands)
    assert "amnezia-awg" not in joined
    assert "cascade-awg" not in joined


def test_enable_xray_on_entry_keeps_routing():
    config = {"log": {"loglevel": "warning"}, "routing": {"rules": [{"type": "field"}]}, "inbounds": [{"port": 443}]}
    ssh = MemSSH(
        {
            "docker ps": (0, "cascade-xray|Up 1 hour|cascade"),
            "config.json": (0, json.dumps(config)),
            "cat /etc/cascade/log-rotate.json": (1, ""),
        }
    )
    apply_dns_logging(ssh, enable=True, role="entry", retention_hours=24, cap_mb=50, disk_free=10**10)
    written = json.loads(ssh.files["/tmp/cascade-xray.json"])
    assert written["routing"] == config["routing"]
    assert written["inbounds"] == config["inbounds"]
    assert written["log"]["access"].endswith("access.log")
    assert any("systemctl restart cascade-xray" in command for command in ssh.commands)


def test_stats_script_collects_dns_paths():
    start = STATS_SCRIPT.split("<< 'PY'\n", 1)[1].rsplit("\nPY", 1)[0]
    compile(start, "stats.sh", "exec")
    assert "/opt/unbound/etc/unbound/unbound.log" in start
    assert "log-archive/unbound-" in start
    assert "querylog.json" in start


def test_poll_fills_sites_and_journal_requires_confirm(client, monkeypatch):
    monkeypatch.setattr("cascade_panel.status.tcp_ping", lambda *_a, **_k: 9)
    client.post("/api/settings/clear-demo")
    created = client.post(
        "/api/servers",
        json={
            "name": "entry",
            "host": "203.0.113.70",
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
    from cascade_panel.db import session
    from cascade_panel.models import Client

    with session() as db:
        row = db.get(Client, phone.json()["id"])
        assert row
        row.address = "10.10.1.2"
    stamp = int(time.time())
    line = f"[{stamp}] unbound[1:0] info: 10.10.1.2 www.example.com. A IN"
    payload = {
        "cpu": 91,
        "ram": "1 / 2 ГБ",
        "disk": "3 / 20 ГБ",
        "uptime": "1 ч",
        "docker": "active",
        "agent": "active",
        "xray": "active",
        "awg": {"up": True, "handshakes": 1},
        "mem_pct": 40,
        "disk_pct": 15,
        "disk_free": 8_000_000_000,
        "net_rx": 100,
        "net_tx": 200,
        "containers": ["amnezia-dns|Up 1 hour|amneziavpn/dns"],
        "dns_probe": {"has_container": True, "container_up": True, "log_queries": "yes", "text": line, "error": ""},
        "xray_access": {"enabled": False, "text": "", "error": ""},
    }
    stats = "CASCADE_STATS " + json.dumps(payload)

    def factory(_server):
        return FakeSession(stats_output=stats)

    set_connector(factory)
    try:
        assert client.post(f"/api/servers/{server_id}/status").status_code == 200
        detail = client.get(f"/api/monitor/{server_id}").json()
        assert detail["dns"]["status"] == "ok"
        assert detail["dns"]["top"][0]["site"] == "example.com"
        assert detail["dns"]["per_client"][0]["client"] == "Phone"
        assert any(item["name"] == "amnezia-dns" and item["up"] for item in detail["containers"])
        assert client.post(f"/api/servers/{server_id}/status").status_code == 200
        again = client.get(f"/api/monitor/{server_id}").json()
        assert again["dns"]["queries"] == 1
        refused = client.post(f"/api/servers/{server_id}/dns-log", json={"enable": True, "confirm": False, "retention_hours": 24, "cap_mb": 50})
        assert refused.status_code == 400
        board = client.get("/api/monitor").json()
        assert board["summary"]
    finally:
        set_connector(None)
