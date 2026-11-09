"""Обёртка для PyInstaller: то же, что `cascade-panel --webview`."""

from __future__ import annotations

from .__main__ import main


def run() -> None:
    main(["--webview"])


if __name__ == "__main__":
    run()
