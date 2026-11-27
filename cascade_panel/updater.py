"""Обновление панели с GitHub Releases.

Ничего не ставится без явного нажатия «Обновить». Проверка читает только
публичный api.github.com (адрес и версия программы уходят туда, как у любого
запроса), автопроверку можно выключить. Архив скачивается только по HTTPS с
узлов GitHub, сверяется с файлом .sha256 из того же релиза, распаковывается в
служебную папку, а заменой файлов после выхода занимается CascadeUpdater.exe
(см. swap.py), который при неудаче возвращает прежнюю версию.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from . import __version__, semver
from .settings import data_dir
from .swap import EXE_NAME, STAGE_ROOT, UPDATER_NAME

REPO = "freeon1989/CascadeControl"
API_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
ASSET_NAME = "CascadeControl-windows-x64.zip"
HASH_NAME = ASSET_NAME + ".sha256"
# Скачивание допускается только отсюда. GitHub отдаёт файлы релиза через CDN-узлы.
ALLOWED_HOSTS = {
    "api.github.com",
    "github.com",
    "objects.githubusercontent.com",
    "release-assets.githubusercontent.com",
    "github-releases.githubusercontent.com",
}
MAX_ARCHIVE_BYTES = 600 * 1024 * 1024
MAX_UNPACKED_BYTES = 1500 * 1024 * 1024
MAX_NOTES_CHARS = 20000
TIMEOUT = 25
AUTO_CHECK_INTERVAL = 6 * 3600


class UpdateError(RuntimeError):
    """Сообщение предназначено для показа пользователю."""


def _test_overrides() -> tuple[str, bool]:
    """Подмена адреса API нужна только тестам с локальным сервером."""
    url = os.environ.get("CASCADE_UPDATE_API", "")
    local = os.environ.get("CASCADE_UPDATE_ALLOW_LOCAL", "") == "1"
    return (url, local) if url and local else (API_URL, False)


def host_allowed(url: str, allow_local: bool = False) -> bool:
    parts = urllib.parse.urlsplit(url)
    host = (parts.hostname or "").lower()
    if allow_local and parts.scheme == "http" and host in {"127.0.0.1", "localhost"}:
        return True
    if parts.scheme != "https" or parts.username or parts.password:
        return False
    if parts.port not in (None, 443):
        return False
    return host in ALLOWED_HOSTS


class _Redirects(urllib.request.HTTPRedirectHandler):
    def __init__(self, allow_local: bool) -> None:
        self.allow_local = allow_local

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        if not host_allowed(newurl, self.allow_local):
            raise UpdateError("перенаправление на недопустимый адрес отклонено")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _open(url: str, allow_local: bool, accept: str = "*/*"):
    if not host_allowed(url, allow_local):
        raise UpdateError("адрес скачивания не принадлежит GitHub")
    from .tls import system_context

    handlers: list = [_Redirects(allow_local)]
    if url.startswith("https"):
        handlers.append(urllib.request.HTTPSHandler(context=system_context()))
    opener = urllib.request.build_opener(*handlers)
    request = urllib.request.Request(
        url, headers={"User-Agent": f"CascadeControl/{__version__}", "Accept": accept}
    )
    try:
        return opener.open(request, timeout=TIMEOUT)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise UpdateError("релизов на GitHub пока нет") from exc
        if exc.code in (403, 429):
            raise UpdateError("GitHub временно ограничил запросы, повторите позже") from exc
        raise UpdateError(f"GitHub ответил кодом {exc.code}") from exc
    except UpdateError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise UpdateError("не удалось связаться с GitHub: " + exc.__class__.__name__) from exc


# --- состояние ---------------------------------------------------------------

_lock = threading.RLock()
_job: dict = {"state": "idle", "progress": 0.0, "message": "", "error": ""}
_last: dict = {}


def _state_path() -> Path:
    return data_dir() / "update.json"


def load_prefs() -> dict:
    try:
        raw = json.loads(_state_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raw = {}
    return {
        "auto_check": bool(raw.get("auto_check", True)),
        "skipped_version": str(raw.get("skipped_version") or ""),
        "last_check": str(raw.get("last_check") or ""),
        "latest": raw.get("latest") if isinstance(raw.get("latest"), dict) else None,
    }


def save_prefs(prefs: dict) -> None:
    keep = {k: prefs.get(k) for k in ("auto_check", "skipped_version", "last_check", "latest")}
    tmp = _state_path().with_suffix(".tmp")
    tmp.write_text(json.dumps(keep, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, _state_path())


def set_prefs(auto_check: bool | None = None, skipped_version: str | None = None) -> dict:
    with _lock:
        prefs = load_prefs()
        if auto_check is not None:
            prefs["auto_check"] = bool(auto_check)
        if skipped_version is not None:
            clean = semver.normalize(skipped_version)
            if skipped_version and not clean:
                raise UpdateError("неверный номер версии")
            prefs["skipped_version"] = clean
        save_prefs(prefs)
        return prefs


# --- проверка ----------------------------------------------------------------


def _release_view(doc: dict) -> dict:
    tag = str(doc.get("tag_name") or "")
    version = semver.normalize(tag)
    if not version:
        raise UpdateError("в ответе GitHub нет понятного номера версии")
    assets = {}
    for item in doc.get("assets") or []:
        if isinstance(item, dict) and isinstance(item.get("name"), str):
            assets[item["name"]] = {
                "url": str(item.get("browser_download_url") or ""),
                "size": int(item.get("size") or 0),
                "digest": str(item.get("digest") or ""),
            }
    return {
        "version": version,
        "tag": tag,
        "name": str(doc.get("name") or tag)[:200],
        "notes": str(doc.get("body") or "")[:MAX_NOTES_CHARS],
        "url": str(doc.get("html_url") or ""),
        "published_at": str(doc.get("published_at") or ""),
        "prerelease": bool(doc.get("prerelease")),
        "has_assets": ASSET_NAME in assets and HASH_NAME in assets,
        "assets": assets,
    }


def fetch_latest() -> dict:
    url, allow_local = _test_overrides()
    with _open(url, allow_local, "application/vnd.github+json") as response:
        raw = response.read(2_000_000)
    try:
        doc = json.loads(raw.decode("utf-8"))
    except ValueError as exc:
        raise UpdateError("GitHub вернул непонятный ответ") from exc
    if not isinstance(doc, dict):
        raise UpdateError("GitHub вернул непонятный ответ")
    return _release_view(doc)


def check(manual: bool = True) -> dict:
    """Опрашивает GitHub и запоминает результат. Ошибки сети возвращаются как error."""
    with _lock:
        prefs = load_prefs()
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    try:
        latest = fetch_latest()
        error = ""
    except UpdateError as exc:
        latest, error = None, str(exc)
    with _lock:
        prefs = load_prefs()
        prefs["last_check"] = now
        if latest is not None:
            prefs["latest"] = {k: v for k, v in latest.items() if k != "assets"}
        save_prefs(prefs)
        _last["error"] = error
        _last["latest_assets"] = latest["assets"] if latest else _last.get("latest_assets", {})
    return status()


def status() -> dict:
    with _lock:
        prefs = load_prefs()
        latest = prefs.get("latest")
        available = bool(latest and semver.is_newer(latest["version"], __version__))
        skipped = bool(available and latest["version"] == prefs["skipped_version"])
        return {
            "current": __version__,
            "latest": latest,
            "available": available,
            "skipped": skipped,
            "auto_check": prefs["auto_check"],
            "skipped_version": prefs["skipped_version"],
            "last_check": prefs["last_check"],
            "check_error": _last.get("error", ""),
            "can_apply": can_apply(),
            "job": dict(_job),
            "result": read_result(),
        }


def can_apply() -> bool:
    """Заменить файлы можно только у собранной программы с CascadeUpdater.exe рядом."""
    if not getattr(sys, "frozen", False):
        return False
    return (Path(sys.executable).parent / UPDATER_NAME).is_file()


def install_dir() -> Path:
    return Path(sys.executable).resolve().parent


# --- скачивание и проверка ---------------------------------------------------


def parse_checksum(text: str, name: str = ASSET_NAME) -> str:
    """Читает «<hex>  <имя>» из .sha256. Имя, если есть, должно совпасть с архивом."""
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        match = re.match(r"^([0-9a-fA-F]{64})(?:\s+\*?(.+))?$", line)
        if not match:
            continue
        target = (match.group(2) or "").strip()
        if target and Path(target.replace("\\", "/")).name != name:
            continue
        return match.group(1).lower()
    raise UpdateError("в файле .sha256 нет контрольной суммы архива")


def _set_job(**fields) -> None:
    with _lock:
        _job.update(fields)


def stage_dir() -> Path:
    return data_dir() / "update"


def download_verified(release: dict, progress=None) -> Path:
    """Скачивает архив и возвращает путь, только если SHA-256 совпала. Иначе файл удаляется."""
    _url, allow_local = _test_overrides()
    assets = release["assets"]
    if ASSET_NAME not in assets or HASH_NAME not in assets:
        raise UpdateError(f"в релизе нет файлов {ASSET_NAME} и {HASH_NAME}")
    with _open(assets[HASH_NAME]["url"], allow_local) as response:
        expected = parse_checksum(response.read(65536).decode("utf-8", "replace"))
    digest = assets[ASSET_NAME].get("digest", "")
    if digest.startswith("sha256:") and digest[7:].lower() != expected:
        raise UpdateError("контрольная сумма в .sha256 не совпадает с суммой, которую указал GitHub")
    root = stage_dir()
    shutil.rmtree(root, ignore_errors=True)
    root.mkdir(parents=True, exist_ok=True)
    target = root / ASSET_NAME
    hasher = hashlib.sha256()
    size = int(assets[ASSET_NAME].get("size") or 0)
    done = 0
    try:
        with _open(assets[ASSET_NAME]["url"], allow_local) as response, target.open("wb") as out:
            total = int(response.headers.get("Content-Length") or size or 0)
            if total > MAX_ARCHIVE_BYTES:
                raise UpdateError("архив слишком большой")
            while True:
                chunk = response.read(1024 * 256)
                if not chunk:
                    break
                done += len(chunk)
                if done > MAX_ARCHIVE_BYTES:
                    raise UpdateError("архив слишком большой")
                hasher.update(chunk)
                out.write(chunk)
                if progress and total:
                    progress(min(1.0, done / total))
    except Exception:
        target.unlink(missing_ok=True)
        raise
    if hasher.hexdigest() != expected:
        target.unlink(missing_ok=True)
        raise UpdateError("контрольная сумма архива не совпала, файл удалён и не будет установлен")
    return target


def unpack_safely(archive: Path, dest: Path) -> Path:
    """Распаковывает без выхода за пределы папки. Возвращает папку CascadeControl."""
    shutil.rmtree(dest, ignore_errors=True)
    dest.mkdir(parents=True)
    base = dest.resolve()
    total = 0
    try:
        with zipfile.ZipFile(archive) as bundle:
            members = bundle.infolist()
            for info in members:
                total += info.file_size
                if total > MAX_UNPACKED_BYTES:
                    raise UpdateError("архив слишком велик после распаковки")
                name = info.filename.replace("\\", "/")
                out = (base / name).resolve()
                if name.startswith("/") or ".." in name.split("/") or not str(out).startswith(str(base) + os.sep):
                    raise UpdateError("в архиве путь за пределами папки, обновление отклонено")
                if (info.external_attr >> 28) == 0xA:  # символьная ссылка
                    raise UpdateError("в архиве ссылка, обновление отклонено")
            bundle.extractall(base)
    except zipfile.BadZipFile as exc:
        raise UpdateError("скачанный архив повреждён") from exc
    root = base / STAGE_ROOT
    if not (root / EXE_NAME).is_file() or not (root / "_internal").is_dir():
        raise UpdateError(f"в архиве нет {STAGE_ROOT}/{EXE_NAME} и _internal")
    return root


# --- запуск замены -----------------------------------------------------------


def result_path() -> Path:
    return data_dir() / "update-result.json"


def read_result() -> dict | None:
    try:
        return json.loads(result_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def acknowledge_result() -> None:
    result_path().unlink(missing_ok=True)


def spawn_updater(stage: Path, new_version: str, port: int) -> None:
    """Копирует CascadeUpdater.exe во временную папку и запускает его. Панель после этого закрывается."""
    src = install_dir() / UPDATER_NAME
    run_dir = Path(tempfile.mkdtemp(prefix="cascade-upd-"))
    runner = run_dir / UPDATER_NAME
    shutil.copy2(src, runner)
    args = [
        str(runner),
        "--install-dir", str(install_dir()),
        "--stage-dir", str(stage),
        "--pid", str(os.getpid()),
        "--old-version", __version__,
        "--new-version", new_version,
        "--port", str(port),
        "--result", str(result_path()),
        "--log", str(data_dir() / "update.log"),
        "--arg=--webview",
    ]
    flags = 0x00000008 | 0x00000200 if os.name == "nt" else 0
    subprocess.Popen(  # noqa: S603 — собственный exe, без shell
        args, cwd=str(run_dir), close_fds=True, creationflags=flags,
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def jobs_running() -> bool:
    from sqlalchemy import select

    from .db import session
    from .models import Job

    with session() as db:
        return db.scalar(select(Job).where(Job.status == "running").limit(1)) is not None


def start_install(version: str, port: int, exit_hook=None, confirm: bool = False) -> dict:
    """Единственная точка установки. Требует confirm=True (нажатие кнопки) и ту же версию, что показана."""
    if not confirm:
        raise UpdateError("нужно подтверждение пользователя")
    wanted = semver.normalize(version)
    with _lock:
        if _job["state"] in {"downloading", "verifying", "installing"}:
            raise UpdateError("обновление уже идёт")
    if not wanted or not semver.is_newer(wanted, __version__):
        raise UpdateError("уже стоит эта или более новая версия")
    if not can_apply():
        raise UpdateError("установка из программы работает только в собранной версии для Windows")
    if jobs_running():
        raise UpdateError("сейчас идёт установка на сервере, дождитесь её конца")
    release = fetch_latest()  # свежие адреса файлов, а не запомненные
    if release["version"] != wanted:
        raise UpdateError(f"на GitHub теперь версия {release['version']}, нажмите «Проверить обновления»")
    if not release["has_assets"]:
        raise UpdateError(f"в релизе нет файлов {ASSET_NAME} и {HASH_NAME}")
    with _lock:
        if _job["state"] in {"downloading", "verifying", "installing"}:
            raise UpdateError("обновление уже идёт")
        _job.update(state="downloading", progress=0.0, message="Скачивание", error="")
    threading.Thread(target=_install_worker, args=(release, port, exit_hook), daemon=True).start()
    return status()


def _install_worker(release: dict, port: int, exit_hook) -> None:
    try:
        _set_job(state="downloading", message="Скачивание")
        archive = download_verified(release, lambda p: _set_job(progress=round(p, 3)))
        _set_job(state="verifying", message="Проверка и распаковка", progress=1.0)
        unpack_safely(archive, stage_dir() / "files")
        archive.unlink(missing_ok=True)
        _set_job(state="installing", message="Перезапуск")
        acknowledge_result()
        spawn_updater(stage_dir() / "files", release["version"], port)
    except UpdateError as exc:
        shutil.rmtree(stage_dir(), ignore_errors=True)
        _set_job(state="error", message="", error=str(exc))
        return
    except Exception as exc:  # noqa: BLE001
        shutil.rmtree(stage_dir(), ignore_errors=True)
        _set_job(state="error", message="", error=f"сбой обновления: {exc.__class__.__name__}")
        return
    time.sleep(1.0)  # UI успевает показать «Перезапуск»
    if exit_hook:
        exit_hook()


# --- автопроверка ------------------------------------------------------------


def start_auto_check() -> None:
    """Проверка при запуске и раз в несколько часов, если она включена."""

    def loop() -> None:
        time.sleep(4)
        while True:
            try:
                if load_prefs()["auto_check"]:
                    check(manual=False)
            except Exception:  # noqa: BLE001
                pass
            time.sleep(AUTO_CHECK_INTERVAL)

    threading.Thread(target=loop, name="cascade-update-check", daemon=True).start()
