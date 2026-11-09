"""Точка входа: локальный сервер и, по флагу, окно WebView2."""

from __future__ import annotations

import argparse
import sys
import threading

import uvicorn

from .app import create_app
from .redact import install_redaction
from .settings import HOST, port


def _serve(application, host: str, listen_port: int) -> None:
    uvicorn.run(application, host=host, port=listen_port, log_level="info", access_log=False)


def main(argv: list[str] | None = None) -> None:
    install_redaction()
    parser = argparse.ArgumentParser(description="Cascade Control")
    parser.add_argument("--port", type=int, default=port())
    parser.add_argument("--webview", action="store_true", help="окно Edge WebView2 (Windows)")
    args = parser.parse_args(argv)
    if args.port != port():
        # порт из флага важнее переменной окружения для этого процесса
        pass
    application = create_app()
    url = f"http://{HOST}:{args.port}"
    if args.webview:
        thread = threading.Thread(target=_serve, args=(application, HOST, args.port), daemon=True)
        thread.start()
        try:
            import webview
        except ImportError:
            print("Пакет pywebview не установлен. Откройте панель в браузере: " + url)
            thread.join()
            return
        webview.create_window("Cascade Control", url, width=1440, height=920)
        webview.start()
        return
    print(f"Cascade Control слушает {url}")
    _serve(application, HOST, args.port)


if __name__ == "__main__":
    main(sys.argv[1:])
