"""Обновление с GitHub: проверка версии, скачивание, сверка SHA-256, замена файлов с откатом.

Настоящий GitHub не нужен: тесты поднимают локальный сервер «релизов».
"""

import hashlib
import io
import json
import threading
import time
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from cascade_panel import __version__, semver, swap, updater
from cascade_panel.db import session
from cascade_panel.models import Job
from cascade_panel.service import set_connector
from cascade_panel.ssh_client import FakeSession


def make_zip(version: str, extra: dict[str, bytes] | None = None, with_exe: bool = True) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as bundle:
        if with_exe:
            bundle.writestr("CascadeControl/CascadeControl.exe", b"EXE " + version.encode())
        bundle.writestr("CascadeControl/_internal/lib.dll", b"LIB " + version.encode())
        bundle.writestr("CascadeControl/README_RU.txt", b"readme " + version.encode())
        for name, data in (extra or {}).items():
            bundle.writestr(name, data)
    return buffer.getvalue()


class Release:
    """Состояние фальшивого GitHub. Меняется из тестов."""

    def __init__(self) -> None:
        self.version = "9.9.9"
        self.archive = make_zip(self.version)
        self.hash_override: str | None = None
        self.hash_text: str | None = None
        self.notes = "## Что нового\n- обновление из программы"
        self.redirect_to: str | None = None
        self.hits: list[str] = []
        self.with_assets = True

    @property
    def digest(self) -> str:
        return hashlib.sha256(self.archive).hexdigest()


@pytest.fixture
def github(monkeypatch, tmp_path):
    rel = Release()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):  # noqa: D401
            return

        def do_GET(self):  # noqa: N802
            rel.hits.append(self.path)
            base = f"http://127.0.0.1:{self.server.server_port}"
            if self.path == "/latest":
                assets = []
                if rel.with_assets:
                    assets = [
                        {"name": updater.ASSET_NAME, "size": len(rel.archive), "browser_download_url": base + "/dl/" + updater.ASSET_NAME},
                        {"name": updater.HASH_NAME, "size": 100, "browser_download_url": base + "/dl/" + updater.HASH_NAME},
                    ]
                body = json.dumps(
                    {"tag_name": "v" + rel.version, "name": "Cascade Control", "body": rel.notes,
                     "html_url": "https://github.com/freeon1989/CascadeControl/releases/tag/v" + rel.version,
                     "published_at": "2026-12-01T10:00:00Z", "assets": assets}
                ).encode()
                self._send(200, body, "application/json")
            elif self.path == "/dl/" + updater.ASSET_NAME:
                if rel.redirect_to:
                    self.send_response(302)
                    self.send_header("Location", rel.redirect_to)
                    self.end_headers()
                    return
                self._send(200, rel.archive, "application/zip")
            elif self.path == "/dl/" + updater.HASH_NAME:
                text = rel.hash_text or f"{rel.hash_override or rel.digest}  {updater.ASSET_NAME}\n"
                self._send(200, text.encode(), "text/plain")
            else:
                self._send(404, b"{}", "application/json")

        def _send(self, code, body, kind):
            self.send_response(code)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setenv("CASCADE_UPDATE_API", f"http://127.0.0.1:{server.server_port}/latest")
    monkeypatch.setenv("CASCADE_UPDATE_ALLOW_LOCAL", "1")
    monkeypatch.setenv("CASCADE_DATA_DIR", str(tmp_path / "data"))
    updater._job.update(state="idle", progress=0.0, message="", error="")
    updater._last.clear()
    yield rel
    server.shutdown()


# --- версии ------------------------------------------------------------------

def test_semver_ordering():
    assert semver.is_newer("0.2.0", "0.1.1")
    assert semver.is_newer("v1.0.0", "0.9.9")
    assert semver.is_newer("0.10.0", "0.9.0")
    assert not semver.is_newer("0.2.0", "0.2.0")
    assert not semver.is_newer("0.1.9", "0.2.0")
    assert semver.is_newer("1.0.0", "1.0.0-rc1")
    assert not semver.is_newer("1.0.0-rc1", "1.0.0")
    assert not semver.is_newer("мусор", "0.1.0")
    assert semver.compare("", "0.0.1") < 0


# --- адреса ------------------------------------------------------------------

def test_download_hosts_are_github_only():
    assert updater.host_allowed("https://api.github.com/repos/x/y/releases/latest")
    assert updater.host_allowed("https://github.com/freeon1989/CascadeControl/releases/download/v1/a.zip")
    assert updater.host_allowed("https://objects.githubusercontent.com/x")
    assert not updater.host_allowed("http://github.com/x")
    assert not updater.host_allowed("https://github.com.evil.example/x")
    assert not updater.host_allowed("https://evil.example/github.com")
    assert not updater.host_allowed("https://user:pw@github.com/x")
    assert not updater.host_allowed("https://github.com:8443/x")
    assert not updater.host_allowed("http://127.0.0.1:1/x")  # локальный адрес только в тестовом режиме
    assert updater.host_allowed("http://127.0.0.1:1/x", allow_local=True)


def test_api_override_ignored_without_both_flags(monkeypatch):
    monkeypatch.setenv("CASCADE_UPDATE_API", "http://127.0.0.1:1/latest")
    monkeypatch.delenv("CASCADE_UPDATE_ALLOW_LOCAL", raising=False)
    assert updater._test_overrides()[0] == updater.API_URL


def test_redirect_to_foreign_host_is_rejected(github):
    github.redirect_to = "https://evil.example/payload.zip"
    release = updater.fetch_latest()
    with pytest.raises(updater.UpdateError, match="недопустим|перенаправлен"):
        updater.download_verified(release)
    assert not (updater.stage_dir() / updater.ASSET_NAME).exists()


# --- проверка обновлений -----------------------------------------------------

def test_check_reports_newer_release_with_notes(github):
    state = updater.check()
    assert state["available"] is True
    assert state["latest"]["version"] == "9.9.9"
    assert "обновление из программы" in state["latest"]["notes"]
    assert state["latest"]["has_assets"] is True
    assert state["current"] == __version__
    assert state["check_error"] == ""


def test_check_no_update_when_same_or_older(github):
    github.version = __version__
    assert updater.check()["available"] is False
    github.version = "0.0.1"
    assert updater.check()["available"] is False


def test_skip_version_and_auto_toggle_persist(github):
    updater.check()
    updater.set_prefs(skipped_version="9.9.9", auto_check=False)
    state = updater.status()
    assert state["skipped"] is True and state["auto_check"] is False
    github.version = "10.0.0"
    state = updater.check()
    assert state["available"] is True and state["skipped"] is False  # новая версия снова предлагается
    updater.set_prefs(skipped_version="")
    assert updater.load_prefs()["skipped_version"] == ""
    with pytest.raises(updater.UpdateError):
        updater.set_prefs(skipped_version="не версия")


def test_check_network_error_is_reported(monkeypatch, tmp_path):
    monkeypatch.setenv("CASCADE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CASCADE_UPDATE_API", "http://127.0.0.1:9/latest")
    monkeypatch.setenv("CASCADE_UPDATE_ALLOW_LOCAL", "1")
    state = updater.check()
    assert state["available"] is False
    assert state["check_error"]


def test_release_without_assets_is_flagged(github):
    github.with_assets = False
    assert updater.check()["latest"]["has_assets"] is False


# --- скачивание и сверка ------------------------------------------------------

def test_download_verifies_sha256(github):
    path = updater.download_verified(updater.fetch_latest())
    assert path.read_bytes() == github.archive


def test_tampered_hash_is_rejected_and_file_removed(github):
    github.hash_override = "0" * 64
    with pytest.raises(updater.UpdateError, match="не совпала"):
        updater.download_verified(updater.fetch_latest())
    assert not (updater.stage_dir() / updater.ASSET_NAME).exists()


def test_tampered_archive_is_rejected(github):
    release = updater.fetch_latest()
    good = github.digest
    github.archive = github.archive + b"tampered"
    github.hash_override = good  # сумма от прежнего архива
    with pytest.raises(updater.UpdateError, match="не совпала"):
        updater.download_verified(release)
    assert not list(updater.stage_dir().glob("*.zip"))


def test_bad_checksum_file_is_rejected(github):
    github.hash_text = "это не контрольная сумма\n"
    with pytest.raises(updater.UpdateError, match="нет контрольной суммы"):
        updater.download_verified(updater.fetch_latest())
    github.hash_text = f"{github.digest}  другой-файл.zip\n"
    with pytest.raises(updater.UpdateError, match="нет контрольной суммы"):
        updater.download_verified(updater.fetch_latest())


def test_checksum_parser_formats():
    h = "a" * 64
    assert updater.parse_checksum(f"{h}  {updater.ASSET_NAME}\n") == h
    assert updater.parse_checksum(f"{h} *{updater.ASSET_NAME}") == h
    assert updater.parse_checksum(h.upper()) == h
    assert updater.parse_checksum(f"{h}  C:\\build\\{updater.ASSET_NAME}") == h


def test_github_digest_mismatch_blocks(github):
    release = updater.fetch_latest()
    release["assets"][updater.ASSET_NAME]["digest"] = "sha256:" + "1" * 64
    with pytest.raises(updater.UpdateError, match="не совпадает"):
        updater.download_verified(release)


def test_unpack_rejects_path_traversal(tmp_path):
    archive = tmp_path / "a.zip"
    archive.write_bytes(make_zip("1", {"../evil.txt": b"x"}))
    with pytest.raises(updater.UpdateError, match="за пределами"):
        updater.unpack_safely(archive, tmp_path / "out")
    assert not (tmp_path / "evil.txt").exists()


def test_unpack_requires_exe(tmp_path):
    archive = tmp_path / "a.zip"
    archive.write_bytes(make_zip("1", with_exe=False))
    with pytest.raises(updater.UpdateError, match="нет CascadeControl"):
        updater.unpack_safely(archive, tmp_path / "out")


def test_unpack_ok(tmp_path):
    archive = tmp_path / "a.zip"
    archive.write_bytes(make_zip("2"))
    root = updater.unpack_safely(archive, tmp_path / "out")
    assert (root / "CascadeControl.exe").read_bytes() == b"EXE 2"


# --- установка только по нажатию ----------------------------------------------

def test_install_requires_confirm_and_never_runs_unasked(github, monkeypatch):
    spawned = []
    monkeypatch.setattr(updater, "spawn_updater", lambda *a, **k: spawned.append(a))
    monkeypatch.setattr(updater, "can_apply", lambda: True)
    updater.check()
    with pytest.raises(updater.UpdateError, match="подтверждение"):
        updater.start_install("9.9.9", 47123, None, confirm=False)
    time.sleep(0.3)
    assert spawned == []
    assert github.hits == ["/latest"]  # скачивания не было, пока не нажали кнопку


def test_auto_check_never_downloads(github):
    updater.check(manual=False)
    assert github.hits == ["/latest"]


def test_install_flow_downloads_verifies_and_spawns(github, monkeypatch, client):
    spawned, exited = [], []
    monkeypatch.setattr(updater, "spawn_updater", lambda stage, version, port: spawned.append((stage, version, port)))
    monkeypatch.setattr(updater, "can_apply", lambda: True)
    updater.check()
    updater.start_install("9.9.9", 4711, lambda: exited.append(True), confirm=True)
    for _ in range(100):
        if exited:
            break
        time.sleep(0.1)
    assert exited
    stage, version, port = spawned[0]
    assert version == "9.9.9" and port == 4711
    assert (Path(stage) / "CascadeControl" / "CascadeControl.exe").is_file()


def test_install_stops_on_tampered_hash(github, monkeypatch, client):
    spawned, exited = [], []
    monkeypatch.setattr(updater, "spawn_updater", lambda *a: spawned.append(a))
    monkeypatch.setattr(updater, "can_apply", lambda: True)
    github.hash_override = "f" * 64
    updater.check()
    updater.start_install("9.9.9", 4711, lambda: exited.append(True), confirm=True)
    for _ in range(100):
        if updater.status()["job"]["state"] == "error":
            break
        time.sleep(0.1)
    job = updater.status()["job"]
    assert job["state"] == "error" and "контрольная сумма" in job["error"]
    assert spawned == [] and exited == []
    assert not updater.stage_dir().exists()


def test_install_refused_when_not_packaged_or_jobs_running(github, client):
    updater.check()
    with pytest.raises(updater.UpdateError, match="собранной версии"):
        updater.start_install("9.9.9", 1, None, confirm=True)


def test_install_refused_during_server_job(github, monkeypatch, client):
    monkeypatch.setattr(updater, "can_apply", lambda: True)
    with session() as db:
        db.add(Job(id="j1", kind="install", status="running"))
    updater.check()
    with pytest.raises(updater.UpdateError, match="идёт установка на сервере"):
        updater.start_install("9.9.9", 1, None, confirm=True)


def test_install_refuses_other_or_stale_version(github, monkeypatch, client):
    monkeypatch.setattr(updater, "can_apply", lambda: True)
    updater.check()
    with pytest.raises(updater.UpdateError, match="Проверить обновления"):
        updater.start_install("9.9.8", 1, None, confirm=True)
    with pytest.raises(updater.UpdateError, match="более новая"):
        updater.start_install(__version__, 1, None, confirm=True)


# --- HTTP API -----------------------------------------------------------------

def test_update_api_endpoints(client, github):
    body = client.get("/api/update").json()
    assert body["current"] == __version__ and body["latest"] is None
    checked = client.post("/api/update/check").json()
    assert checked["available"] is True and checked["latest"]["version"] == "9.9.9"
    saved = client.put("/api/update/settings", json={"auto_check": False, "skipped_version": "9.9.9"}).json()
    assert saved["auto_check"] is False and saved["skipped"] is True
    assert client.put("/api/update/settings", json={"skipped_version": "ерунда"}).status_code == 400
    refuse = client.post("/api/update/install", json={"version": "9.9.9"})
    assert refuse.status_code == 400
    refuse = client.post("/api/update/install", json={"version": "9.9.9", "confirm": True})
    assert refuse.status_code == 400 and "собранной" in refuse.json()["detail"]
    assert client.post("/api/update/ack").json() == {"ok": True}


def test_unknown_api_route_stays_json_404_utf8(client):
    missing = client.get("/api/no-such")
    assert missing.status_code == 404
    assert "charset=utf-8" in missing.headers["content-type"].lower()


# --- замена файлов и откат ------------------------------------------------------

class FakeProc:
    def __init__(self, code=None):
        self.code = code

    def poll(self):
        return self.code

    def terminate(self):
        self.code = -15

    def wait(self, _t=None):
        return self.code


def layout(tmp_path, old="0.1.1", new="0.2.0", with_new_exe=True):
    install = tmp_path / "apps" / "CascadeControl"
    (install / "_internal").mkdir(parents=True)
    (install / "CascadeControl.exe").write_bytes(b"EXE " + old.encode())
    (install / "_internal" / "lib.dll").write_bytes(b"LIB " + old.encode())
    (install / "README_RU.txt").write_text("readme " + old)
    (install / "my-notes.txt").write_text("мои заметки")
    (install / "data").mkdir()
    (install / "data" / "keep.bin").write_bytes(b"keep")
    stage = tmp_path / "stage"
    (stage / "CascadeControl" / "_internal").mkdir(parents=True)
    if with_new_exe:
        (stage / "CascadeControl" / "CascadeControl.exe").write_bytes(b"EXE " + new.encode())
    (stage / "CascadeControl" / "_internal" / "lib.dll").write_bytes(b"LIB " + new.encode())
    (stage / "CascadeControl" / "README_RU.txt").write_text("readme " + new)
    return install, stage


def run_apply(install, stage, tmp_path, *, health, launches, **kw):
    def launcher(exe, cwd, args):
        launches.append((Path(exe).read_bytes(), args))
        return FakeProc()

    return swap.apply_update(
        install_dir=install, stage_dir=stage, old_version="0.1.1", new_version="0.2.0",
        result_path=tmp_path / "result.json", launcher=launcher, health=health,
        health_timeout=1.5, lock_timeout=1.0, exit_timeout=2.0, **kw,
    )


def test_swap_success_preserves_user_files_and_keeps_backup(tmp_path):
    install, stage = layout(tmp_path)
    launches = []
    ok = run_apply(install, stage, tmp_path, health=lambda _p: "0.2.0", launches=launches)
    assert ok
    assert (install / "CascadeControl.exe").read_bytes() == b"EXE 0.2.0"
    assert (install / "_internal" / "lib.dll").read_bytes() == b"LIB 0.2.0"
    assert (install / "README_RU.txt").read_text() == "readme 0.2.0"
    assert (install / "my-notes.txt").read_text() == "мои заметки"
    assert (install / "data" / "keep.bin").read_bytes() == b"keep"
    backup = install.parent / "CascadeControl.backup-0.1.1"
    assert (backup / "CascadeControl.exe").read_bytes() == b"EXE 0.1.1"
    assert launches == [(b"EXE 0.2.0", ["--webview"])]
    result = json.loads((tmp_path / "result.json").read_text())
    assert result["ok"] is True and result["version"] == "0.2.0"
    assert not stage.exists()
    assert not (install.parent / "CascadeControl.new").exists()


def test_swap_rolls_back_when_new_version_does_not_answer(tmp_path):
    install, stage = layout(tmp_path)
    launches = []
    ok = run_apply(install, stage, tmp_path, health=lambda _p: None, launches=launches)
    assert not ok
    assert (install / "CascadeControl.exe").read_bytes() == b"EXE 0.1.1"
    assert (install / "_internal" / "lib.dll").read_bytes() == b"LIB 0.1.1"
    assert (install / "my-notes.txt").read_text() == "мои заметки"
    assert [item[0] for item in launches] == [b"EXE 0.2.0", b"EXE 0.1.1"]  # затем снова запущена старая
    result = json.loads((tmp_path / "result.json").read_text())
    assert result["ok"] is False and result["rolled_back"] is True and result["version"] == "0.1.1"
    assert not (install.parent / "CascadeControl.failed").exists()


def test_swap_rolls_back_on_wrong_version_answer(tmp_path):
    install, stage = layout(tmp_path)
    ok = run_apply(install, stage, tmp_path, health=lambda _p: "0.1.1", launches=[])
    assert not ok and (install / "CascadeControl.exe").read_bytes() == b"EXE 0.1.1"


def test_swap_rolls_back_when_new_process_crashes(tmp_path):
    install, stage = layout(tmp_path)

    def launcher(exe, cwd, args):
        return FakeProc(code=1 if Path(exe).read_bytes() == b"EXE 0.2.0" else None)

    ok = swap.apply_update(
        install_dir=install, stage_dir=stage, old_version="0.1.1", new_version="0.2.0",
        result_path=tmp_path / "r.json", launcher=launcher, health=lambda _p: None,
        health_timeout=5, lock_timeout=1, exit_timeout=1,
    )
    assert not ok and (install / "CascadeControl.exe").read_bytes() == b"EXE 0.1.1"


def test_swap_missing_new_exe_changes_nothing(tmp_path):
    install, stage = layout(tmp_path, with_new_exe=False)
    launches = []
    ok = run_apply(install, stage, tmp_path, health=lambda _p: "0.2.0", launches=launches)
    assert not ok
    assert (install / "CascadeControl.exe").read_bytes() == b"EXE 0.1.1"
    assert not (install.parent / "CascadeControl.backup-0.1.1").exists()


def test_swap_waits_for_panel_to_exit(tmp_path):
    import subprocess
    import sys

    install, stage = layout(tmp_path)
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(1.2)"])
    started = time.monotonic()
    ok = run_apply(install, stage, tmp_path, health=lambda _p: "0.2.0", launches=[], wait_pid=child.pid)
    child.wait()
    assert ok and time.monotonic() - started >= 1.0


def test_swap_prunes_older_backups(tmp_path):
    install, stage = layout(tmp_path)
    older = install.parent / "CascadeControl.backup-0.0.9"
    older.mkdir()
    (older / "x").write_text("x")
    assert run_apply(install, stage, tmp_path, health=lambda _p: "0.2.0", launches=[])
    assert not older.exists()
    assert (install.parent / "CascadeControl.backup-0.1.1").exists()


# --- агент на входе --------------------------------------------------------------

def _entry(client):
    client.post("/api/settings/clear-demo")
    created = client.post(
        "/api/servers",
        json={"name": "entry-test", "host": "203.0.113.60", "ssh_port": 22, "ssh_user": "root",
              "auth_type": "password", "secret": "s", "role": "entry", "country": "DE"},
    )
    assert created.status_code == 200, created.text
    return created.json()["id"]


def _stats(version: str, agent="active") -> str:
    version_part = f'"agent_version": "{version}", ' if version is not None else ""
    return (
        'CASCADE_STATS {"cpu": 1, "ram": "1 / 2 ГБ", "disk": "1 / 9 ГБ", "uptime": "1 ч", '
        f'"docker": "active", "agent": "{agent}", {version_part}"xray": "active", "awg": null}}'
    )


def _refresh(client, server_id, text, monkeypatch):
    monkeypatch.setattr("cascade_panel.status.tcp_ping", lambda *_a, **_k: 5)
    set_connector(lambda _s: FakeSession(stats_output=text))
    try:
        response = client.post(f"/api/servers/{server_id}/status")
        assert response.status_code == 200, response.text
        return response.json()
    finally:
        set_connector(None)


def test_agent_outdated_when_panel_is_newer(client, monkeypatch):
    sid = _entry(client)
    body = _refresh(client, sid, _stats("0.1.1"), monkeypatch)
    assert body["live"]["agent_version"] == "0.1.1"
    assert body["agent_update"]["outdated"] is True and body["agent_update"]["panel"] == __version__


def test_agent_with_old_unknown_version_counts_as_outdated(client, monkeypatch):
    sid = _entry(client)
    body = _refresh(client, sid, _stats(None), monkeypatch)
    assert body["agent_update"]["outdated"] is True and body["agent_update"]["known"] is False


def test_agent_current_is_not_offered(client, monkeypatch):
    sid = _entry(client)
    body = _refresh(client, sid, _stats(__version__), monkeypatch)
    assert body["agent_update"]["outdated"] is False
    body = _refresh(client, sid, _stats("99.0.0"), monkeypatch)
    assert body["agent_update"]["outdated"] is False


def test_agent_update_needs_confirmation(client, monkeypatch, tmp_path):
    sid = _entry(client)
    sessions = []

    def factory(_s):
        sessions.append(FakeSession())
        return sessions[-1]

    set_connector(factory)
    try:
        assert client.post(f"/api/servers/{sid}/agent-update", json={}).status_code == 400
        assert client.post(f"/api/servers/{sid}/agent-update", json={"confirm": False}).status_code == 400
        assert sessions == []  # без подтверждения к серверу никто не подключался
    finally:
        set_connector(None)


def test_agent_update_uploads_binary_and_restarts_only_agent(client, monkeypatch, tmp_path):
    sid = _entry(client)
    blob = tmp_path / "cascade-agent"
    blob.write_bytes(b"\x7fELF-new-agent")
    monkeypatch.setenv("CASCADE_AGENT_BIN", str(blob))
    sessions = []

    def factory(_s):
        sessions.append(FakeSession())
        return sessions[-1]

    set_connector(factory)
    try:
        response = client.post(f"/api/servers/{sid}/agent-update", json={"confirm": True})
        assert response.status_code == 200, response.text
        job_id = response.json()["job_id"]
        for _ in range(100):
            job = client.get(f"/api/jobs/{job_id}").json()
            if job["status"] != "running":
                break
            time.sleep(0.05)
        assert job["status"] == "done", job
    finally:
        set_connector(None)
    ssh = sessions[0]
    assert ssh.files["/opt/cascade/bin/cascade-agent.new"] == b"\x7fELF-new-agent"
    script = ssh.files["/opt/cascade/agent/update.sh"].decode()
    assert "systemctl restart cascade-agent" in script and "cascade-xray" not in script
    assert "cascade-agent.prev" in script  # путь отката
    assert "/opt/cascade/xray/config.json" not in ssh.files  # конфиг Xray не перезаписывался
    assert ssh.commands == ["bash /opt/cascade/agent/update.sh"]


def test_agent_update_rejects_non_entry(client):
    client.post("/api/settings/clear-demo")
    created = client.post(
        "/api/servers",
        json={"name": "exit-test", "host": "203.0.113.61", "ssh_port": 22, "ssh_user": "root",
              "auth_type": "password", "secret": "s", "role": "exit", "country": "NL"},
    )
    sid = created.json()["id"]
    assert client.post(f"/api/servers/{sid}/agent-update", json={"confirm": True}).status_code == 400


@pytest.mark.skipif(__import__("os").name == "nt", reason="скрипт-заглушка вместо exe только для POSIX")
def test_swap_cli_end_to_end_with_real_processes(tmp_path):
    """Настоящий запуск CascadeUpdater (swap.main) с процессом-заглушкой, который отвечает на /api/health."""
    import socket
    import subprocess
    import sys

    def fake_exe(version: str) -> bytes:
        return (
            f"#!{sys.executable}\n"
            "import json, sys\n"
            "from http.server import BaseHTTPRequestHandler, HTTPServer\n"
            "class H(BaseHTTPRequestHandler):\n"
            "    def log_message(self, *a): pass\n"
            "    def do_GET(self):\n"
            f"        b = json.dumps({{'ok': True, 'version': '{version}'}}).encode()\n"
            "        self.send_response(200); self.send_header('Content-Length', str(len(b))); self.end_headers(); self.wfile.write(b)\n"
            "HTTPServer(('127.0.0.1', PORT), H).serve_forever()\n"
        ).encode()

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    install, stage = layout(tmp_path)
    (install / "CascadeControl.exe").write_bytes(fake_exe("0.1.1").replace(b"PORT", str(port).encode()))
    new_exe = stage / "CascadeControl" / "CascadeControl.exe"
    new_exe.write_bytes(fake_exe("0.2.0").replace(b"PORT", str(port).encode()))
    for path in (install / "CascadeControl.exe", new_exe):
        path.chmod(0o755)
    result = tmp_path / "result.json"
    code = subprocess.run(
        [sys.executable, "-m", "cascade_panel.swap", "--install-dir", str(install), "--stage-dir", str(stage),
         "--old-version", "0.1.1", "--new-version", "0.2.0", "--port", str(port), "--result", str(result),
         "--arg=--webview"],
        cwd=Path(__file__).resolve().parent.parent, timeout=90,
    ).returncode
    try:
        assert code == 0
        assert json.loads(result.read_text())["ok"] is True
        assert b"'0.2.0'" in (install / "CascadeControl.exe").read_bytes()
        assert b"'0.1.1'" in (install.parent / "CascadeControl.backup-0.1.1" / "CascadeControl.exe").read_bytes()
    finally:
        subprocess.run(["pkill", "-f", str(install / "CascadeControl.exe")], check=False)
