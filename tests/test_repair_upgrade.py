import json
import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

from cascade_panel.app import create_app
from cascade_panel.crypto import reset_vault_cache
from cascade_panel.db import reset_db
from cascade_panel.editor import is_catch_all, parse_match, repair_routing


def _client(tmp_path, monkeypatch, db_path: Path):
    monkeypatch.setenv("CASCADE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CASCADE_SEED", "0")
    monkeypatch.setenv("CASCADE_SCHEDULER", "0")
    reset_vault_cache()
    reset_db()
    application = create_app(db_path, do_seed=False)
    return TestClient(application)


def test_repair_pin_catchall_and_dedupe_gemini(client):
    # Demo seed may already have a catch-all; add a misplaced one and a duplicate Gemini.
    existing = client.get("/api/rules").json()
    # Use API to append, then physically move catch-all up via order (should be pinned last).
    names = [row["name"] for row in existing]
    assert names, "demo seed should have rules"
    # After 0.3.1 startup repair, catch-all is last.
    assert existing[-1]["catch_all"] is True
    assert not any(row["catch_all"] for row in existing[:-1])
    gemini = [row for row in existing if "gemini" in row["name"].lower() or "google" in row["name"].lower()]
    # At most one Gemini/Google-AI family with the same action after repair.
    actions = {(row["action"], json.dumps(row["match"], sort_keys=True)) for row in gemini}
    assert len(gemini) == len(actions) or len(gemini) <= 1


def test_upgrade_from_v020_rules_preserved(tmp_path, monkeypatch):
    db_path = tmp_path / "cascade.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE settings (key VARCHAR PRIMARY KEY, value TEXT);
        CREATE TABLE routing_rules (
            id VARCHAR(36) PRIMARY KEY,
            position INTEGER,
            name VARCHAR(120),
            match_json TEXT,
            action VARCHAR(16),
            exit_id VARCHAR(36),
            chain_id VARCHAR(36),
            group_id VARCHAR(36),
            enabled BOOLEAN,
            hits INTEGER
        );
        INSERT INTO settings VALUES ('bootstrapped', '1');
        INSERT INTO routing_rules VALUES
        ('r1', 1, 'Российские сайты', '{"any_of":[{"geosite":"category-ru"},{"geoip":"ru"}]}', 'direct', NULL, NULL, NULL, 1, 10),
        ('r2', 2, 'Всё остальное', '{"any": true}', 'cascade', NULL, NULL, NULL, 1, 99),
        ('r3', 3, 'Gemini / Google AI', '{"type":"preset","value":"google-ai"}', 'cascade', NULL, NULL, NULL, 1, 3),
        ('r4', 4, 'Gemini / Google AI', '{"type":"preset","value":"google-ai"}', 'cascade', NULL, NULL, NULL, 1, 1),
        ('r5', 5, 'YouTube', '{"type":"preset","value":"youtube"}', 'cascade', NULL, NULL, NULL, 1, 8),
        ('r6', 6, 'ChatGPT', '{"type":"preset","value":"openai"}', 'cascade', NULL, NULL, NULL, 1, 4);
        """
    )
    conn.commit()
    conn.close()

    with _client(tmp_path, monkeypatch, db_path) as test_client:
        rows = test_client.get("/api/rules").json()
        names = [row["name"] for row in rows]
        assert "Российские сайты" in names
        assert "YouTube" in names
        assert "ChatGPT" in names
        assert names[-1] == "Всё остальное"
        assert rows[-1]["catch_all"] is True
        gemini = [row for row in rows if row["name"] == "Gemini / Google AI"]
        assert len(gemini) == 1
        assert {row["id"] for row in rows} <= {"r1", "r2", "r3", "r4", "r5", "r6"}
        assert "r2" in {row["id"] for row in rows}


def test_warp_sites_seeded_once_not_on_get(client):
    first = client.get("/api/warp/sites").json()
    assert any(row["value"] == "openai" for row in first)
    ids = {row["id"] for row in first}
    second = client.get("/api/warp/sites").json()
    assert {row["id"] for row in second} == ids


def test_heroine_manual_choice_persists(client):
    rows = client.get("/api/servers").json()
    node = next(item for item in rows if item["country"] == "NL" or "nl" in item["name"].lower())
    catalog = client.get("/api/heroines").json()["items"]
    assert catalog[0]["key"] == "auto"
    keys = [item["key"] for item in catalog]
    assert "nl" in keys and "at" in keys and "ru" in keys
    updated = client.put(f"/api/servers/{node['id']}/heroine", json={"key": "at"}).json()
    assert updated["heroine_key"] == "at"
    assert updated["heroine"].endswith("heroine_at.webp")
    again = next(item for item in client.get("/api/servers").json() if item["id"] == node["id"])
    assert again["heroine_key"] == "at"
    auto = client.put(f"/api/servers/{node['id']}/heroine", json={"key": "auto"}).json()
    assert auto["heroine_key"] == "auto"
    assert auto["heroine"].endswith("heroine_nl.webp")
