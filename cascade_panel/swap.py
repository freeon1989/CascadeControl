"""Замена файлов программы после выхода панели. Только стандартная библиотека.

Эту логику запускает маленький CascadeUpdater.exe, скопированный во временный
каталог: пока панель работает, её файлы заняты и сами себя заменить не могут.

Порядок: дождаться выхода панели, собрать новую папку рядом со старой, перенести
в неё то, что пользователь положил рядом с программой, переименовать старую в
резервную, поставить новую, запустить и дождаться ответа /api/health с новой
версией. Если что-то пошло не так, резервная папка возвращается на место.
Данные панели (база и ключи) лежат в профиле пользователя и не затрагиваются.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

EXE_NAME = "CascadeControl.exe"
UPDATER_NAME = "CascadeUpdater.exe"
STAGE_ROOT = "CascadeControl"
# Эти имена никогда не переносятся из новой сборки поверх пользовательских.
USER_DATA_NAMES = {"data", "master.key", "cascade.db", "settings.json"}


def process_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes

        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        handle = kernel32.OpenProcess(0x100000, False, pid)  # SYNCHRONIZE
        if not handle:
            return False
        try:
            return kernel32.WaitForSingleObject(handle, 0) == 0x102  # WAIT_TIMEOUT
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    # Зомби считаем завершённым.
    try:
        with open(f"/proc/{pid}/stat", encoding="utf-8") as handle:
            return handle.read().rsplit(")", 1)[1].split()[0] != "Z"
    except OSError:
        return True


def wait_exit(pid: int, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while process_alive(pid):
        if time.monotonic() > deadline:
            return False
        time.sleep(0.2)
    return True


def _retry(action: Callable[[], None], timeout: float, pause: float = 0.5) -> None:
    """Антивирус и индексатор Windows на секунды занимают свежие файлы."""
    deadline = time.monotonic() + timeout
    while True:
        try:
            action()
            return
        except OSError:
            if time.monotonic() > deadline:
                raise
            time.sleep(pause)


def _health_version(port: int) -> str | None:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=2) as response:
            return str(json.loads(response.read().decode("utf-8")).get("version") or "")
    except Exception:  # noqa: BLE001
        return None


def write_result(path: Path | None, **fields) -> None:
    if path is None:
        return
    fields["at"] = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(fields, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


def _launch(exe: Path, cwd: Path, args: list[str]) -> subprocess.Popen:
    flags = 0
    if os.name == "nt":
        flags = 0x00000010 | 0x00000200  # CREATE_NEW_CONSOLE | CREATE_NEW_PROCESS_GROUP: окно журнала, как при ручном запуске
    return subprocess.Popen(  # noqa: S603 — путь к своей программе, без shell
        [str(exe), *args],
        cwd=str(cwd),
        close_fds=True,
        creationflags=flags,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def apply_update(
    *,
    install_dir: Path,
    stage_dir: Path,
    old_version: str,
    new_version: str,
    wait_pid: int = 0,
    port: int = 47123,
    exe_name: str = EXE_NAME,
    launch_args: list[str] | None = None,
    result_path: Path | None = None,
    log: Callable[[str], None] = lambda _m: None,
    exit_timeout: float = 90.0,
    lock_timeout: float = 30.0,
    health_timeout: float = 40.0,
    launcher: Callable[[Path, Path, list[str]], subprocess.Popen] = _launch,
    health: Callable[[int], str | None] = _health_version,
) -> bool:
    """True, если новая версия установлена и ответила. False после отката."""
    install_dir = install_dir.resolve()
    source = stage_dir / STAGE_ROOT
    args = launch_args if launch_args is not None else ["--webview"]
    parent = install_dir.parent
    fresh = parent / (install_dir.name + ".new")
    backup = parent / f"{install_dir.name}.backup-{old_version or 'old'}"

    def restart_old(reason: str) -> bool:
        log(f"откат: {reason}")
        write_result(result_path, ok=False, rolled_back=True, version=old_version, wanted=new_version, error=reason)
        try:
            launcher(install_dir / exe_name, install_dir, args)
        except Exception as exc:  # noqa: BLE001
            log(f"старая версия не запустилась сама: {exc}")
        return False

    if not (source / exe_name).is_file():
        return restart_old("в подготовленной папке нет " + exe_name)
    if wait_pid and not wait_exit(wait_pid, exit_timeout):
        return restart_old("панель не закрылась за отведённое время")
    log("панель закрыта, собираю новую папку")
    try:
        if fresh.exists():
            shutil.rmtree(fresh)
        shutil.copytree(source, fresh)
        # Всё, что пользователь положил рядом с программой, переезжает вместе с ней.
        for item in install_dir.iterdir():
            if item.name in {"_internal", exe_name, UPDATER_NAME}:
                continue
            target = fresh / item.name
            if target.exists() and item.name not in USER_DATA_NAMES:
                continue  # файл есть в новой сборке, берём новый
            if target.exists():
                shutil.rmtree(target) if target.is_dir() else target.unlink()
            if item.is_dir():
                shutil.copytree(item, target)
            else:
                shutil.copy2(item, target)
    except OSError as exc:
        shutil.rmtree(fresh, ignore_errors=True)
        return restart_old(f"не удалось подготовить новую папку: {exc}")
    # Старые резервные копии удаляются только после успеха, см. ниже.
    if backup.exists():
        shutil.rmtree(backup, ignore_errors=True)
    try:
        _retry(lambda: os.rename(install_dir, backup), lock_timeout)
    except OSError as exc:
        shutil.rmtree(fresh, ignore_errors=True)
        return restart_old(f"файлы программы заняты: {exc}")
    try:
        _retry(lambda: os.rename(fresh, install_dir), lock_timeout)
    except OSError as exc:
        try:
            os.rename(backup, install_dir)
        except OSError as inner:
            log(f"не удалось вернуть старую папку: {inner}")
        shutil.rmtree(fresh, ignore_errors=True)
        return restart_old(f"не удалось поставить новую папку: {exc}")
    log("новая папка на месте, запускаю")
    try:
        process = launcher(install_dir / exe_name, install_dir, args)
    except Exception as exc:  # noqa: BLE001
        return _rollback(install_dir, backup, exe_name, args, launcher, result_path, old_version, new_version, f"не запустилась: {exc}", log)
    deadline = time.monotonic() + health_timeout
    while time.monotonic() < deadline:
        got = health(port)
        if got:
            if got == new_version:
                log(f"версия {got} отвечает")
                write_result(result_path, ok=True, rolled_back=False, version=new_version, previous=old_version)
                _prune_backups(parent, install_dir.name, backup)
                shutil.rmtree(stage_dir, ignore_errors=True)
                return True
            break
        code = process.poll()
        if code is not None and code != 0:
            break
        time.sleep(0.5)
    return _rollback(
        install_dir, backup, exe_name, args, launcher, result_path, old_version, new_version,
        "новая версия не ответила или вернула другую версию", log, process,
    )


def _rollback(install_dir, backup, exe_name, args, launcher, result_path, old_version, new_version, reason, log, process=None) -> bool:
    log(f"откат: {reason}")
    try:
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(10)
            except Exception:  # noqa: BLE001
                process.kill()
    except Exception:  # noqa: BLE001
        pass
    broken = install_dir.parent / (install_dir.name + ".failed")
    try:
        if broken.exists():
            shutil.rmtree(broken, ignore_errors=True)
        _retry(lambda: os.rename(install_dir, broken), 20)
        _retry(lambda: os.rename(backup, install_dir), 20)
        shutil.rmtree(broken, ignore_errors=True)
    except OSError as exc:
        write_result(result_path, ok=False, rolled_back=False, version=new_version, error=f"{reason}; откат не удался: {exc}")
        log(f"откат не удался: {exc}")
        return False
    write_result(result_path, ok=False, rolled_back=True, version=old_version, wanted=new_version, error=reason)
    try:
        launcher(install_dir / exe_name, install_dir, args)
    except Exception as exc:  # noqa: BLE001
        log(f"старая версия не запустилась сама: {exc}")
    return False


def _prune_backups(parent: Path, name: str, keep: Path) -> None:
    """Остаётся одна резервная копия: предыдущая версия, на случай ручного отката."""
    for path in parent.glob(name + ".backup-*"):
        if path != keep and path.is_dir():
            shutil.rmtree(path, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Cascade Control updater")
    parser.add_argument("--install-dir", required=True)
    parser.add_argument("--stage-dir", required=True)
    parser.add_argument("--pid", type=int, default=0)
    parser.add_argument("--old-version", default="")
    parser.add_argument("--new-version", required=True)
    parser.add_argument("--port", type=int, default=47123)
    parser.add_argument("--result", default="")
    parser.add_argument("--log", default="")
    parser.add_argument("--arg", action="append", default=None, help="аргумент для запуска панели")
    ns = parser.parse_args(argv)
    log_path = Path(ns.log) if ns.log else None

    def log(message: str) -> None:
        line = f"{datetime.now().isoformat(timespec='seconds')} updater: {message}\n"
        if log_path:
            try:
                with log_path.open("a", encoding="utf-8") as handle:
                    handle.write(line)
            except OSError:
                pass
        try:
            sys.stdout.write(line)
        except Exception:  # noqa: BLE001
            pass

    ok = apply_update(
        install_dir=Path(ns.install_dir),
        stage_dir=Path(ns.stage_dir),
        old_version=ns.old_version,
        new_version=ns.new_version,
        wait_pid=ns.pid,
        port=ns.port,
        launch_args=ns.arg,
        result_path=Path(ns.result) if ns.result else None,
        log=log,
    )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
