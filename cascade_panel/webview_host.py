"""Обёртка для PyInstaller: то же, что `cascade-panel --webview`."""

from __future__ import annotations

import multiprocessing


def run() -> None:
    # Абсолютный импорт: PyInstaller запускает этот файл как скрипт, и
    # относительный импорт там не имеет пакета-родителя.
    from cascade_panel.__main__ import main

    main(["--webview"])


if __name__ == "__main__":
    multiprocessing.freeze_support()
    run()
