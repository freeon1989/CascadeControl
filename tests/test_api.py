import json
import time

from cascade_panel.awg import INTERFACE_FIELDS, parse_conf, validate_params
from cascade_panel.bundle import render_install_script, render_remove_script
from cascade_panel.service import files_for, load_params, set_connector
from cascade_panel.ssh_client import FakeSession
from cascade_panel.vpnuri import decode_vpn_uri


def _wait(client, job_id: str) -> dict:
    body = {}
    for _ in range(80):
        body = client.get(f"/api/jobs/{job_id}").json()
        if body["status"] != "running":
            return body
        time.sleep(0.05)
    return body


def test_overview_and_exports_hide_secrets(client):
    health = client.get("/api/health")
    assert health.json()["ok"] is True
    overview = client.get("/api/overview").json()
    assert overview["entry"]["name"] == "ru-entry"
    assert overview["direct_share"] == 38
    assert overview["cascade_share"] == 62
    servers = client.get("/api/servers").json()
    blob = json.dumps(servers)
    assert "ssh_secret" not in blob
    assert "PrivateKey" not in blob
    assert "reality_private" not in blob
    clients = client.get("/api/clients").json()
    router = next(item for item in clients if item["name"] == "Роутер дом")
    detail = client.get(f"/api/clients/{router['id']}").json()
    conf = parse_conf(detail["export"]["conf"])
    assert list(conf["Interface"]) == INTERFACE_FIELDS
    validate_params(load_params(_entry_from_api(client)))
    uri = decode_vpn_uri(detail["export"]["vpn_uri"])
    assert uri["defaultContainer"] == "amnezia-awg"
    assert detail["export"]["qr_conf"].startswith("data:image/png;base64,")
    phone = next(item for item in clients if item["protocol"] == "vless")
    phone_detail = client.get(f"/api/clients/{phone['id']}").json()
    assert phone_detail["export"]["vless_uri"].startswith("vless://")
    sub = client.get("/sub/" + _token(client, phone["id"]))
    assert sub.status_code == 200
    assert sub.text


def _entry_from_api(client):
    from cascade_panel.db import session
    from cascade_panel.models import Server
    from sqlalchemy import select

    with session() as db:
        return db.scalar(select(Server).where(Server.role == "entry"))


def _token(client, client_id: str) -> str:
    from cascade_panel.db import session
    from cascade_panel.models import Client

    with session() as db:
        return db.get(Client, client_id).sub_token


def test_rules_reorder_balance_and_detection(client):
    rules = client.get("/api/rules").json()
    assert rules[0]["action"] == "direct"
    assert rules[-1]["name"] == "Всё остальное"
    flipped = list(reversed([row["id"] for row in rules]))
    moved = client.put("/api/rules/order", json={"ids": flipped}).json()
    assert [row["id"] for row in moved] == flipped
    # вернуть исходный порядок, чтобы каскад снова собирался предсказуемо
    client.put("/api/rules/order", json={"ids": [row["id"] for row in rules]})
    decision = client.post("/api/route/decide", params={"host": "www.youtube.com"}).json()
    assert decision["action"] == "cascade"
    banks = client.post("/api/route/decide", params={"host": "lk.gosuslugi.ru"}).json()
    assert banks["action"] == "direct"
    saved = client.post(
        "/api/detect/classify",
        json={"domain": "newly-blocked.example", "probe_status": "timeout"},
    ).json()
    assert saved["saved"] is True
    assert saved["action"] == "cascade"
    addresses = client.get("/api/addresses").json()
    row = next(item for item in addresses if item["value"] == "newly-blocked.example")
    client.post(f"/api/addresses/{row['id']}/exclude")
    again = client.post(
        "/api/detect/classify",
        json={"domain": "newly-blocked.example", "probe_status": "reset"},
    ).json()
    assert again["saved"] is False
    balance = client.put(
        "/api/balance",
        json={"strategy": "weighted", "auto_failover": True, "interval_sec": 15, "weights": {}},
    )
    assert balance.json()["strategy"] == "weighted"


def test_ingest_and_chain(client):
    text = "alpha.example\n# comment\nbeta.example\n"
    result = client.post("/api/sources/antifilter-domains/ingest", json={"text": text})
    assert result.status_code == 200
    assert result.json()["count"] == 2
    chain = client.get("/api/chain").json()
    assert chain["ok"] is True
    entry = next(node for node in chain["nodes"] if node["role"] == "entry")
    assert entry["has_xray"] is True
    assert any(node["role"] == "transit" for node in chain["nodes"])
    assert any(node["role"] == "exit" for node in chain["nodes"])


def test_fake_ssh_pipeline_keeps_amnezia_containers(client):
    client.post("/api/settings/clear-demo")
    created = []
    for name, role in (("entry-1", "entry"), ("tr-a", "transit"), ("exit-a", "exit")):
        response = client.post(
            "/api/servers",
            json={
                "name": name,
                "host": "203.0.113.10",
                "ssh_port": 22,
                "ssh_user": "root",
                "auth_type": "password",
                "secret": "not-logged",
                "role": role,
                "country": "DE",
            },
        )
        assert response.status_code == 200, response.text
        created.append(response.json())
    report = {
        "virt": "kvm",
        "os_id": "ubuntu",
        "os_version": "24.04",
        "os_pretty": "Ubuntu 24.04",
        "kernel": "6.8.0",
        "disk_free_gb": 30,
        "docker": True,
        "port_443_tcp_free": False,
        "used_ports": [22, 443, 39743],
        "containers": [{"name": "amnezia-awg2", "image": "amnezia-awg2", "ports": "39743/udp", "status": "Up"}],
    }
    text = "CASCADE_JSON " + json.dumps(report)
    holder: dict = {}

    def factory(_server):
        holder["ssh"] = FakeSession(text)
        return holder["ssh"]

    set_connector(factory)
    try:
        for server in created:
            job = client.post(f"/api/servers/{server['id']}/precheck").json()
            body = _wait(client, job["job_id"])
            assert body["status"] == "done", body
        entry = next(item for item in client.get("/api/servers").json() if item["role"] == "entry")
        assert entry["reality_port"] != 443
        assert entry["awg_port"] != 39743
        assert "amnezia-awg2" in json.dumps(entry["precheck"])
        job = client.post(f"/api/servers/{entry['id']}/install").json()
        body = _wait(client, job["job_id"])
        assert body["status"] == "done", body
        script = holder["ssh"].files["/opt/cascade/install.sh"].decode()
        assert "docker rm -f" in script
        assert "amnezia-" not in script
        assert "cascade-awg" in script
        assert b"PrivateKey" in holder["ssh"].files["/opt/cascade/awg/cawg0.conf"]
        joined = "\n".join(line["message"] for line in body["lines"])
        assert "not-logged" not in joined
        assert "PrivateKey" not in joined
    finally:
        set_connector(None)


def test_json_uses_utf8_charset(client):
    health = client.get("/api/health")
    assert "charset=utf-8" in health.headers["content-type"].lower()
    missing = client.get("/api/no-such")
    assert missing.status_code == 404
    assert "charset=utf-8" in missing.headers["content-type"].lower()


def test_old_precheck_text_is_recalculated_and_autowire(client):
    from cascade_panel.db import session
    from cascade_panel.models import Server

    client.post("/api/settings/clear-demo")
    created = []
    for name, role in (("entry-1", "entry"), ("exit-a", "exit"), ("exit-b", "exit")):
        response = client.post(
            "/api/servers",
            json={
                "name": name,
                "host": "203.0.113.10",
                "ssh_port": 22,
                "ssh_user": "root",
                "auth_type": "password",
                "secret": "secret",
                "role": role,
                "country": "DE",
            },
        )
        assert response.status_code == 200, response.text
        created.append(response.json())
    chains = client.get("/api/chains").json()
    groups = client.get("/api/groups").json()
    assert len(chains) == 1
    assert len(groups) == 1
    assert len(groups[0]["members"]) == 2
    entry = next(item for item in created if item["role"] == "entry")
    stale = {
        "ok": False,
        "checks": [
            {
                "id": "os",
                "title": "Система",
                "level": "fail",
                "detail": "Ubuntu 26.04 LTS: нужна Ubuntu 22.04/24.04 или Debian 12",
            }
        ],
        "raw": {
            "virt": "kvm",
            "os_id": "ubuntu",
            "os_version": "26.04",
            "os_pretty": "Ubuntu 26.04 LTS",
            "kernel": "7.0.0",
            "disk_free_gb": 20,
            "docker": False,
            "port_443_tcp_free": True,
            "used_ports": [22],
            "containers": [],
            "uid": 0,
            "sudo_nopass": True,
        },
    }
    with session() as db:
        row = db.get(Server, entry["id"])
        row.precheck_json = json.dumps(stale, ensure_ascii=False)
        standard = __import__("base64").b64encode(bytes(range(32))).decode("ascii")
        row.reality_public = standard
    set_connector(lambda _server: FakeSession())
    try:
        job = client.post(f"/api/servers/{entry['id']}/install").json()
        body = _wait(client, job["job_id"])
        assert body["status"] == "done", body
        joined = "\n".join(line["message"] for line in body["lines"])
        assert "22.04/24.04" not in joined
    finally:
        set_connector(None)
    with session() as db:
        row = db.get(Server, entry["id"])
        assert "=" not in row.reality_public
        assert len(row.reality_public) == 43
        report = json.loads(row.precheck_json)
        assert report["ok"] is True


def test_install_and_remove_scripts_refuse_foreign_containers():
    script = render_install_script(role="entry", awg=True, xray=True, agent=True)
    assert "cascade-awg" in script
    assert "amnezia-" not in script
    assert "docker.io" in script
    assert "https://get.docker.com" in script
    assert "dnf install" in script
    assert "firewall-cmd" in script
    assert "xray run -test" in script
    assert "journalctl -u cascade-xray -n 50" in script
    assert "is-active --quiet cascade-xray" in script
    assert "docker-27.5.1.tgz" in script
    assert script.index("https://get.docker.com") < script.rindex("docker.io")
    remove = render_remove_script()
    assert "amnezia-" not in remove
    assert "cascade-awg" in remove


def test_edit_keeps_keys_recheck_and_reconfigure(client):
    from cascade_panel.crypto import vault
    from cascade_panel.db import session
    from cascade_panel.models import Server

    client.post("/api/settings/clear-demo")
    created = []
    for name, role in (("entry-1", "entry"), ("tr-a", "transit"), ("exit-a", "exit")):
        response = client.post(
            "/api/servers",
            json={
                "name": name,
                "host": "203.0.113.10",
                "ssh_port": 22,
                "ssh_user": "root",
                "auth_type": "password",
                "secret": "secret-value\n",
                "role": role,
                "country": "de",
            },
        )
        assert response.status_code == 200, response.text
        created.append(response.json())
    entry = next(item for item in created if item["role"] == "entry")
    transit = next(item for item in created if item["role"] == "transit")
    with session() as db:
        row = db.get(Server, entry["id"])
        awg_public = row.awg_public
        reality_public = row.reality_public
        assert awg_public and reality_public
    patched = client.patch(
        f"/api/servers/{entry['id']}",
        json={
            "name": "entry-renamed",
            "host": "203.0.113.20",
            "ssh_port": 2222,
            "ssh_user": "root",
            "auth_type": "password",
            "secret": "  newpass \n",
            "role": "entry",
            "country": "de",
            "protocols": ["awg", "vless"],
        },
    )
    assert patched.status_code == 200, patched.text
    body = patched.json()
    assert body["name"] == "entry-renamed"
    assert body["host"] == "203.0.113.20"
    assert body["ssh_port"] == 2222
    assert body["country"] == "DE"
    assert body["protocol_ids"] == ["awg", "vless"]
    assert body["config_dirty"] is True
    with session() as db:
        row = db.get(Server, entry["id"])
        assert row.awg_public == awg_public
        assert row.reality_public == reality_public
        assert vault().decrypt(row.ssh_secret_enc) == "newpass"
    denied = client.patch(
        f"/api/servers/{created[2]['id']}",
        json={
            "name": "exit-a",
            "host": "203.0.113.10",
            "ssh_port": 22,
            "ssh_user": "root",
            "auth_type": "password",
            "secret": "",
            "role": "exit",
            "country": "DE",
            "protocols": ["awg"],
        },
    )
    assert denied.status_code == 400

    good = {
        "virt": "kvm",
        "os_id": "ubuntu",
        "os_version": "22.04",
        "os_pretty": "Ubuntu 22.04.5 LTS",
        "kernel": "5.15.0-119-generic",
        "disk_free_gb": 18,
        "docker": True,
        "port_443_tcp_free": False,
        "used_ports": [22, 443],
        "containers": [{"name": "amnezia-awg", "image": "amnezia-awg", "ports": "443/tcp", "status": "Up"}],
        "uid": 0,
        "sudo_nopass": True,
    }
    state = {"text": "CASCADE_JSON " + json.dumps(good)}
    holder: dict = {}

    def factory(_server):
        holder["ssh"] = FakeSession(state["text"])
        return holder["ssh"]

    set_connector(factory)
    try:
        job = client.post(f"/api/servers/{entry['id']}/precheck").json()
        assert _wait(client, job["job_id"])["status"] == "done"
        fresh = next(item for item in client.get("/api/servers").json() if item["id"] == entry["id"])
        assert fresh["precheck"]["ok"] is True
        assert any(item["id"] == "port443" and item["level"] == "warn" for item in fresh["precheck"]["checks"])
        assert fresh["config_dirty"] is True
        job = client.post(f"/api/servers/{entry['id']}/reconfigure").json()
        done = _wait(client, job["job_id"])
        assert done["status"] == "done", done
        joined = "\n".join(line["message"] for line in done["lines"])
        assert "не удаляется" in joined
        assert "newpass" not in joined
        assert "/opt/cascade/install.sh" in holder["ssh"].files
        still = client.get("/api/servers").json()
        assert len(still) == 3
        assert any(item["id"] == entry["id"] and item["name"] == "entry-renamed" for item in still)

        state["text"] = "CASCADE_JSON " + json.dumps({**good, "virt": "openvz"})
        job = client.post(f"/api/servers/{transit['id']}/precheck").json()
        failed = _wait(client, job["job_id"])
        assert failed["status"] == "error"
        transit_view = next(item for item in client.get("/api/servers").json() if item["id"] == transit["id"])
        assert "Виртуализация" in transit_view["last_error"]
        assert transit_view["precheck"]["ok"] is False
        assert any(item["hint"] for item in transit_view["precheck"]["checks"] if item["level"] == "fail")
        job = client.post(f"/api/servers/{transit['id']}/reconfigure").json()
        blocked = _wait(client, job["job_id"])
        assert blocked["status"] == "error"
        assert any(item["id"] == transit["id"] for item in client.get("/api/servers").json())
    finally:
        set_connector(None)


def test_reissue_changes_client_key(client):
    router = next(item for item in client.get("/api/clients").json() if item["name"] == "Роутер дом")
    before = client.get(f"/api/clients/{router['id']}").json()["export"]["conf"]
    assert client.post(f"/api/clients/{router['id']}/reissue").status_code == 200
    after = client.get(f"/api/clients/{router['id']}").json()["export"]["conf"]
    assert parse_conf(before)["Interface"]["PrivateKey"] != parse_conf(after)["Interface"]["PrivateKey"]
    # обфускация узла та же
    assert parse_conf(before)["Interface"]["HeaderProtectionKey"] == parse_conf(after)["Interface"]["HeaderProtectionKey"]


def test_files_for_entry_chain(client):
    from cascade_panel.db import session
    from cascade_panel.models import Server
    from sqlalchemy import select

    with session() as db:
        entry = db.scalar(select(Server).where(Server.role == "entry"))
        files = files_for(db, entry)
    xray = json.loads(files["/opt/cascade/xray/config.json"])
    tags = [item["tag"] for item in xray["outbounds"]]
    assert "direct" in tags and "block" in tags
    assert any(tag.startswith("exit-") for tag in tags)
    assert xray["routing"]["balancers"][0]["tag"] == "cascade"
    cascaded = [item for item in xray["outbounds"] if str(item.get("tag", "")).startswith("exit-")]
    assert cascaded
    assert cascaded[0]["settings"]["vnext"][0]["address"] == "45.77.12.34"
    params = load_params(entry)
    validate_params(params)
