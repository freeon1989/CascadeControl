import base64

import pytest
from fastapi.testclient import TestClient

from cascade_panel.app import create_app
from cascade_panel.crypto import reset_vault_cache
from cascade_panel.db import reset_db
from cascade_panel.lists import parse_geosite_dat


@pytest.fixture
def empty(tmp_path, monkeypatch):
    monkeypatch.setenv("CASCADE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("CASCADE_SEED", "0")
    monkeypatch.setenv("CASCADE_SCHEDULER", "0")
    reset_vault_cache()
    reset_db()
    application = create_app(tmp_path / "empty.db", do_seed=False)
    with TestClient(application) as test_client:
        yield test_client
    reset_db()
    reset_vault_cache()


def _varint(number: int) -> bytes:
    out = bytearray()
    while number > 127:
        out.append((number & 0x7F) | 0x80)
        number >>= 7
    out.append(number)
    return bytes(out)


def _bytes_field(field: int, payload: bytes) -> bytes:
    return _varint((field << 3) | 2) + _varint(len(payload)) + payload


def _var_field(field: int, number: int) -> bytes:
    return _varint((field << 3) | 0) + _varint(number)


def test_geosite_dat_parser_reads_category():
    domain = _var_field(1, 2) + _bytes_field(2, b"blocked.example")
    site = _bytes_field(1, b"RU") + _bytes_field(2, domain)
    parsed = parse_geosite_dat(_bytes_field(1, site))
    assert parsed["RU"] == ["blocked.example"]


def test_empty_database_can_create_rule_without_servers(empty: TestClient):
    assert empty.get("/api/rules").json() == []
    assert empty.get("/api/servers").json() == []
    created = empty.post(
        "/api/rules",
        json={
            "name": "Всё через каскад",
            "match": {"type": "any", "value": ""},
            "action": "cascade",
            "enabled": True,
        },
    )
    assert created.status_code == 200, created.text
    body = created.json()
    assert body["action"] == "cascade"
    assert body["chain_id"] is None
    listed = empty.get("/api/rules").json()
    assert len(listed) == 1
    edited = empty.patch(
        f"/api/rules/{body['id']}",
        json={"name": "Домен напрямую", "match": {"type": "suffix", "value": "example.com"}, "action": "direct"},
    )
    assert edited.status_code == 200
    assert edited.json()["name"] == "Домен напрямую"
    assert empty.delete(f"/api/rules/{body['id']}").status_code == 200
    assert empty.get("/api/rules").json() == []


def test_empty_database_rejects_bad_match_and_chain_without_exit(empty: TestClient):
    bad = empty.post("/api/rules", json={"name": "x", "match": {"type": "cidr", "value": "not-a-net"}, "action": "block"})
    assert bad.status_code == 400
    missing = empty.post(
        "/api/chains",
        json={"name": "пустой", "entry_id": "missing", "transit_ids": [], "exit_id": None, "group_id": None},
    )
    assert missing.status_code == 400


def test_address_enrichment_on_empty_database(empty: TestClient):
    source = empty.post(
        "/api/sources",
        json={
            "id": "local-list",
            "title": "Локальный список",
            "url": "https://example.test/list.txt",
            "kind": "domain",
            "action": "cascade",
            "enabled": True,
            "interval_hours": 12,
            "selector": "",
        },
    )
    assert source.status_code == 200, source.text
    ingested = empty.post("/api/sources/local-list/ingest", json={"text": "alpha.example\n# comment\nbeta.example\n"})
    assert ingested.status_code == 200
    assert ingested.json()["count"] == 2
    rows = empty.get("/api/addresses").json()
    assert {row["value"] for row in rows} >= {"alpha.example", "beta.example"}
    assert all(row["source"] == "list:local-list" and row["source_date"] for row in rows)
    manual = empty.post("/api/addresses", json={"kind": "domain", "value": "manual.example", "action": "direct"})
    assert manual.status_code == 200
    preset = empty.post("/api/presets/youtube/enabled", json={"enabled": True})
    assert preset.status_code == 200
    values = {row["value"] for row in empty.get("/api/addresses").json()}
    assert "youtube.com" in values
    assert "manual.example" in values
    detect = empty.put("/api/detect", json={"enabled": True})
    assert detect.json()["enabled"] is True
    marked = empty.post("/api/detect/classify", json={"domain": "blocked.example", "probe_status": "timeout"})
    assert marked.json()["saved"] is True
    detected = [row for row in empty.get("/api/addresses").json() if row["value"] == "blocked.example"]
    assert detected and detected[0]["source"] == "detect"
    client = empty.post("/api/clients", json={"name": "router", "kind": "router", "protocol": "awg"})
    assert client.status_code == 400


def test_quick_setup_and_source_without_id(empty):
    ready = empty.post("/api/routing/quick-setup")
    assert ready.status_code == 200, ready.text
    names = [row["name"] for row in empty.get("/api/rules").json()]
    assert names[0] == "Российские сайты"
    assert names[-1] == "Всё остальное"
    again = empty.post("/api/routing/quick-setup").json()
    assert again["rules"] == []
    assert len(empty.get("/api/rules").json()) == 2
    created = empty.post(
        "/api/sources",
        json={"title": "Свой список", "url": "https://example.test/list.txt", "kind": "domain"},
    )
    assert created.status_code == 200, created.text
    assert created.json()["id"] == "list"
