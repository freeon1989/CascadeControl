import time

from cascade_panel.editor import rewrite_geo_node
from cascade_panel.service import deploy_order, set_connector
from cascade_panel.ssh_client import FakeSession, _repair_cp866


def _wait(client, job_id: str) -> dict:
    body = {}
    for _ in range(80):
        body = client.get(f"/api/jobs/{job_id}").json()
        if body["status"] != "running":
            return body
        time.sleep(0.05)
    return body


def test_cp866_mojibake_repairs_utf8_and_keeps_real_russian():
    original = "проверка пройдена"
    broken = original.encode("utf-8").decode("cp866")
    assert broken != original
    assert _repair_cp866(broken) == original
    assert _repair_cp866(original) == original


def test_geosite_ru_alias_becomes_category_ru():
    assert rewrite_geo_node({"type": "geo", "value": "geosite:ru"})["value"] == "geosite:category-ru"
    assert rewrite_geo_node({"any_of": [{"geosite": "ru"}, {"geoip": "ru"}]}) == {
        "any_of": [{"geosite": "category-ru"}, {"geoip": "ru"}]
    }


def test_client_sync_pushes_conf_without_restarting_the_interface(client):
    client.post("/api/settings/clear-demo")
    for name, role in (("entry-1", "entry"), ("exit-a", "exit")):
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
    holder = {}

    def factory(_server):
        holder["ssh"] = FakeSession()
        return holder["ssh"]

    set_connector(factory)
    try:
        created = client.post("/api/clients", json={"name": "Телефон", "kind": "phone", "protocol": "awg"})
        assert created.status_code == 200, created.text
        body = _wait(client, created.json()["sync_job"])
        assert body["status"] == "done", body
        script = holder["ssh"].files["/opt/cascade/awg/sync.sh"].decode()
        assert "awg syncconf cawg0" in script
        assert "awg-quick down" not in script
        assert b"[Peer]" in holder["ssh"].files["/opt/cascade/awg/cawg0.conf"]
    finally:
        set_connector(None)


def test_deploy_all_continues_after_the_first_failure(client, monkeypatch):
    client.post("/api/settings/clear-demo")
    ids = []
    for name, role in (("exit-a", "exit"), ("entry-1", "entry")):
        response = client.post(
            "/api/servers",
            json={
                "name": name,
                "host": "203.0.113.20",
                "ssh_port": 22,
                "ssh_user": "root",
                "auth_type": "password",
                "secret": "secret",
                "role": role,
                "country": "NL",
            },
        )
        assert response.status_code == 200, response.text
        ids.append(response.json()["id"])
    calls = []

    def fake(_job_id, server_id, finish=True):
        calls.append(server_id)
        if server_id == ids[0]:
            raise RuntimeError("выход не встал")

    monkeypatch.setattr("cascade_panel.service.install_worker", fake)
    job = client.post("/api/cascade/deploy").json()
    body = _wait(client, job["job_id"])
    assert calls == ids
    assert body["status"] == "error"
    joined = "\n".join(line["message"] for line in body["lines"])
    assert "следующему" in joined
    assert "не установлены" in joined
    assert deploy_order([]) == []
