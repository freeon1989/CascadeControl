from cascade_panel.heroine import heroine_path
from cascade_panel.lists import parse_geoip_dat
from cascade_panel.service import set_connector
from cascade_panel.ssh_client import FakeSession


def test_heroine_by_country_and_fallback():
    from cascade_panel.heroine import heroine_for, heroine_path, resolve_country

    assert heroine_path("RU").endswith("heroine_ru.webp")
    assert heroine_path("at").endswith("heroine_at.webp")
    assert heroine_path("XX").endswith("heroine_default.webp")
    assert heroine_path("").endswith("heroine_default.webp")
    assert resolve_country("", "Piter") == "RU"
    assert resolve_country("", "Amsterdam") == "NL"
    assert resolve_country("", "Vena") == "AT"
    assert resolve_country("", "Вена") == "AT"
    assert heroine_path("", name="Amsterdam").endswith("heroine_nl.webp")
    assert heroine_path("", name="Vena").endswith("heroine_at.webp")
    assert heroine_path("", name="Amsterdam") != heroine_path("", name="Vena")
    assert heroine_path("", name="Amsterdam") != heroine_path("")

    class Fake:
        country = ""
        name = "Amsterdam"
        host = "192.0.2.10"
        heroine_key = ""

    assert heroine_for(Fake()).endswith("heroine_nl.webp")
    Fake.heroine_key = "at"
    assert heroine_for(Fake()).endswith("heroine_at.webp")


def test_settings_ssh_via_entry_default_off(client):
    data = client.get("/api/settings").json()
    assert data["ssh_via_entry"] is False
    saved = client.put("/api/settings/ssh-via-entry", json={"enabled": True}).json()
    assert saved["ssh_via_entry"] is True
    assert client.get("/api/settings").json()["ssh_via_entry"] is True
    client.put("/api/settings/ssh-via-entry", json={"enabled": False})


def test_warp_sites_defaults_and_edit(client):
    rows = client.get("/api/warp/sites").json()
    values = {(row["kind"], row["value"]) for row in rows}
    assert ("geosite", "openai") in values
    added = client.post("/api/warp/sites", json={"value": "accounts.google.com"}).json()
    assert added["kind"] == "domain"
    assert added["value"] == "accounts.google.com"
    patched = client.patch(f"/api/warp/sites/{added['id']}", json={"value": "geoip:google"}).json()
    assert patched["kind"] == "geoip"
    assert patched["value"] == "google"
    client.post(f"/api/addresses/{added['id']}/pin")
    pinned = next(row for row in client.get("/api/warp/sites").json() if row["id"] == added["id"])
    assert pinned["pinned"] is True


def test_flow_and_decide_include_path(client):
    flow = client.get("/api/flow").json()
    assert flow["entry"]["heroine"].endswith("heroine_ru.webp")
    assert any(item["heroine"].endswith(".webp") for item in flow["exits"])
    kinds = {edge["kind"] for edge in flow["edges"]}
    ru = next(item for item in flow["specials"] if item["id"] == "direct-ru")
    assert ru["heroine"] == ""
    assert "direct" in kinds
    assert "cascade" in kinds
    decision = client.post("/api/route/decide", params={"host": "www.youtube.com"}).json()
    assert decision["action"] == "cascade"
    assert "path" in decision
    assert decision["label"]


def test_warp_rejected_on_entry(client):
    entry = next(item for item in client.get("/api/servers").json() if item["role"] == "entry")
    assert entry["warp"] is None
    res = client.post(f"/api/servers/{entry['id']}/warp", json={"enabled": True, "mode": "sites"})
    assert res.status_code == 400


def test_warp_job_on_exit_with_fake_ssh(client):
    client.post("/api/settings/clear-demo")
    created = []
    for item in (
        {"name": "вход", "host": "192.0.2.10", "role": "entry", "auth_type": "password", "secret": "x", "country": "RU"},
        {"name": "выход", "host": "192.0.2.20", "role": "exit", "auth_type": "password", "secret": "x", "country": "NL"},
    ):
        created.append(client.post("/api/servers", json=item).json())
    exit_id = created[1]["id"]
    set_connector(lambda _server: FakeSession())
    try:
        from cascade_panel import options as options_mod
        options_mod.detect_panel_public_ip = lambda timeout=8: "198.51.100.8"
        job = client.post(f"/api/servers/{exit_id}/warp", json={"enabled": True, "mode": "all"}).json()
        body = {}
        import time
        for _ in range(80):
            body = client.get(f"/api/jobs/{job['job_id']}").json()
            if body["status"] != "running":
                break
            time.sleep(0.05)
        assert body["status"] == "done", body
        view = next(item for item in client.get("/api/servers").json() if item["id"] == exit_id)
        assert view["warp"]["enabled"] is True
        assert view["heroine"].endswith("heroine_nl.webp")
        prot = client.put(f"/api/servers/{exit_id}/protect", json={"bittorrent": True, "private": True}).json()
        assert prot["server"]["protect"]["bittorrent"] is True
    finally:
        set_connector(None)


def test_parse_geoip_dat_roundtrip():
    def varint(number: int) -> bytes:
        out = bytearray()
        while number > 127:
            out.append((number & 0x7F) | 0x80)
            number >>= 7
        out.append(number)
        return bytes(out)

    def bytes_field(field: int, payload: bytes) -> bytes:
        return varint((field << 3) | 2) + varint(len(payload)) + payload

    def var_field(field: int, number: int) -> bytes:
        return varint((field << 3) | 0) + varint(number)

    cidr = bytes_field(1, bytes([203, 0, 113, 0])) + var_field(2, 24)
    geo = bytes_field(1, b"RU") + bytes_field(2, cidr)
    blob = bytes_field(1, geo)
    parsed = parse_geoip_dat(blob)
    assert "RU" in parsed
    assert "203.0.113.0/24" in parsed["RU"]
