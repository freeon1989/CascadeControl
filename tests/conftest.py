import pytest
from fastapi.testclient import TestClient

from cascade_panel.app import create_app
from cascade_panel.crypto import reset_vault_cache
from cascade_panel.db import reset_db


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("CASCADE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CASCADE_SEED", "1")
    monkeypatch.setenv("CASCADE_SCHEDULER", "0")
    reset_vault_cache()
    reset_db()
    application = create_app(tmp_path / "cascade.db", do_seed=True)
    with TestClient(application) as test_client:
        yield test_client
    reset_db()
    reset_vault_cache()
