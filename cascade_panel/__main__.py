"""Точка входа: локальный сервер и, по флагу, окно WebView2."""

from __future__ import annotations

import argparse
import logging
import os
import socket
import sys
import threading
import time
from logging.handlers import RotatingFileHandler

import uvicorn

from .app import create_app
from .redact import install_redaction
from .settings import HOST, data_dir, port
from .tls import install_tls


def _frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def _attach_console() -> None:
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.kernel32.AllocConsole()
        sys.stdout = open("CONOUT$", "w", encoding="utf-8", errors="replace")  # noqa: SIM115
        sys.stderr = open("CONOUT$", "w", encoding="utf-8", errors="replace")  # noqa: SIM115
        sys.stdin = open("CONIN$", "r", encoding="utf-8", errors="replace")  # noqa: SIM115
    except Exception:
        pass


def _setup_logging(show_console: bool) -> logging.Logger:
    log = logging.getLogger("cascade")
    log.setLevel(logging.INFO)
    log.handlers.clear()
    log_path = data_dir() / "panel.log"
    handler = RotatingFileHandler(log_path, maxBytes=2_000_000, backupCount=5, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    log.addHandler(handler)
    if show_console:
        stream = logging.StreamHandler(sys.stdout)
        stream.setFormatter(logging.Formatter("%(message)s"))
        log.addHandler(stream)
    elif sys.stdout is None or (hasattr(sys.stdout, "closed") and sys.stdout.closed):
        class _LogStream:
            encoding = "utf-8"

            def write(self, text: str) -> int:
                if text and text.strip():
                    log.info(text.rstrip())
                return len(text or "")

            def flush(self) -> None:
                return None

            def isatty(self) -> bool:
                return False

        sys.stdout = _LogStream()  # type: ignore[assignment]
        sys.stderr = _LogStream()  # type: ignore[assignment]
    else:
        class _Tee:
            encoding = "utf-8"

            def __init__(self, original):
                self._original = original

            def write(self, text: str) -> int:
                if text and text.strip():
                    log.info(text.rstrip())
                try:
                    if self._original is not None:
                        self._original.write(text)
                except Exception:
                    pass
                return len(text or "")

            def flush(self) -> None:
                try:
                    if self._original is not None:
                        self._original.flush()
                except Exception:
                    pass

            def isatty(self) -> bool:
                return False

        sys.stdout = _Tee(sys.stdout)  # type: ignore[assignment]
        sys.stderr = _Tee(sys.stderr)  # type: ignore[assignment]
    return log


def _say(message: str) -> None:
    line = message.rstrip()
    logging.getLogger("cascade").info(line)


def _serve(application, host: str, listen_port: int) -> None:
    uvicorn.run(application, host=host, port=listen_port, log_level="info", access_log=False, log_config=None)


def _wait_until_listening(host: str, listen_port: int, timeout: float = 20.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, listen_port), timeout=0.3):
                return True
        except OSError:
            time.sleep(0.15)
    return False


def _open_webview(url: str) -> bool:
    try:
        import webview
    except Exception as exc:  # noqa: BLE001 — окно необязательно, сервер уже поднят
        _say(f"Окно WebView2 не загрузилось ({exc.__class__.__name__}). Откройте панель в браузере: {url}")
        return False
    try:
        from .desktop import SaveApi, configure_webview

        configure_webview(webview)
        webview.create_window("Cascade Control", url, width=1440, height=920, js_api=SaveApi())
        webview.start()
    except Exception as exc:  # noqa: BLE001
        _say(f"Окно WebView2 не открылось ({exc.__class__.__name__}: {exc}). Адрес панели: {url}")
        return False
    return True


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Cascade Control")
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--webview", action="store_true", help="окно Edge WebView2 (Windows)")
    parser.add_argument("--no-webview", action="store_true", help="только сервер, без окна")
    parser.add_argument("--console", action="store_true", help="показать консоль и писать туда журнал")
    args, _unknown = parser.parse_known_args(argv)
    show_console = bool(args.console) or not _frozen()
    if args.console and _frozen():
        _attach_console()
    _setup_logging(show_console)
    install_tls()
    install_redaction()
    listen = args.port if args.port is not None else port()
    os.environ["CASCADE_PORT"] = str(listen)  # им пользуется установщик обновлений
    application = create_app()
    url = f"http://{HOST}:{listen}"
    use_webview = bool(args.webview)
    if _frozen() and sys.platform == "win32" and not args.no_webview:
        use_webview = True
    if use_webview:
        thread = threading.Thread(
            target=_serve, args=(application, HOST, listen), name="cascade-http", daemon=True
        )
        thread.start()
        if not _wait_until_listening(HOST, listen):
            _say(f"Сервер не открыл {url}. Смотрите журнал {data_dir() / 'panel.log'}.")
        else:
            _say(f"Cascade Control слушает {url}")
        if _open_webview(url):
            return
        _say("Сервер продолжает работать. Закройте программу, чтобы остановить панель.")
        thread.join()
        return
    _say(f"Cascade Control слушает {url}")
    _serve(application, HOST, listen)


if __name__ == "__main__":
    main(sys.argv[1:])
