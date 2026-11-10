"""Сохранение файлов из окна WebView2.

Браузерная ссылка с Blob в этом окне не скачивается. Диалог пишет байты сам:
текст в UTF-8 без BOM, переводы строк как в исходной строке.
"""

from __future__ import annotations

import base64
from pathlib import Path

FILE_TYPES = ("Config (*.conf)", "Text (*.txt)", "All files (*.*)")


def configure_webview(webview) -> None:
    webview.settings["ALLOW_DOWNLOADS"] = True


def safe_filename(name: str) -> str:
    cleaned = Path(str(name or "")).name.replace("\x00", "").strip()
    return cleaned or "cascade.txt"


def file_types_for(name: str) -> tuple[str, ...]:
    if safe_filename(name).lower().endswith(".png"):
        return ("PNG (*.png)",) + FILE_TYPES
    return FILE_TYPES


def export_bytes(text: str) -> bytes:
    """UTF-8 без BOM. data:image/...;base64, пишется как файл картинки."""
    if text.startswith("data:") and ";base64," in text[:128]:
        payload = text.split(",", 1)[1]
        return base64.b64decode(payload)
    return text.encode("utf-8")


def write_export(path: str, text: str) -> None:
    Path(path).write_bytes(export_bytes(text))


class SaveApi:
    """js_api окна: save_file(name, text) -> путь или пустая строка, если отменили."""

    def __init__(self, window=None) -> None:
        self._window = window

    def save_file(self, name: str, text: str) -> str:
        import webview

        window = self._window
        if window is None:
            window = webview.active_window() if hasattr(webview, "active_window") else None
        if window is None and getattr(webview, "windows", None):
            window = webview.windows[0]
        if window is None:
            return ""
        chosen = window.create_file_dialog(
            webview.SAVE_DIALOG,
            save_filename=safe_filename(name),
            file_types=file_types_for(name),
        )
        if not chosen:
            return ""
        path = chosen if isinstance(chosen, str) else chosen[0]
        if not path:
            return ""
        write_export(path, text if isinstance(text, str) else str(text))
        return path
