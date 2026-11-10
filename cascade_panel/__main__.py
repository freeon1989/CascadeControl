"""Точка входа: локальный сервер и, по флагу, окно WebView2."""

from __future__ import annotations

import argparse
import socket
import sys
import threading
import time

import uvicorn

from .app import create_app
from .redact import install_redaction
from .settings import HOST, data_dir, port
from .tls import install_tls


def _say(message: str) -> None:
    line = message.rstrip() + "\n"
    try:
        sys.stdout.write(line)
        sys.stdout.flush()
    except Exception:
        pass
    try:
        path = data_dir() / "panel.log"
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line)
    except Exception:
        pass


def _serve(application, host: str, listen_port: int) -> None:
    uvicorn.run(application, host=host, port=listen_port, log_level="info", access_log=False)


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
    install_tls()
    install_redaction()
    parser = argparse.ArgumentParser(description="Cascade Control")
    parser.add_argument("--port", type=int, default=port())
    parser.add_argument("--webview", action="store_true", help="окно Edge WebView2 (Windows)")
    args = parser.parse_args(argv)
    application = create_app()
    url = f"http://{HOST}:{args.port}"
    if args.webview:
        thread = threading.Thread(
            target=_serve, args=(application, HOST, args.port), name="cascade-http", daemon=True
        )
        thread.start()
        if not _wait_until_listening(HOST, args.port):
            _say(f"Сервер не открыл {url}. Смотрите журнал рядом с базой.")
        else:
            _say(f"Cascade Control слушает {url}")
        if _open_webview(url):
            return
        _say("Сервер продолжает работать. Закройте это окно, чтобы остановить панель.")
        thread.join()
        return
    _say(f"Cascade Control слушает {url}")
    _serve(application, HOST, args.port)


if __name__ == "__main__":
    main(sys.argv[1:])
