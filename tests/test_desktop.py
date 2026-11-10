import base64
import sys
import types
from pathlib import Path

from cascade_panel.desktop import FILE_TYPES, SaveApi, configure_webview, export_bytes


def _webview(monkeypatch, dialog):
    fake = types.SimpleNamespace(SAVE_DIALOG=dialog, windows=[], active_window=lambda: None)
    monkeypatch.setitem(sys.modules, "webview", fake)
    return fake


def test_conf_bytes_stay_identical_and_dialog_opens(tmp_path, monkeypatch):
    _webview(monkeypatch, "SAVE")
    seen = {}

    class Window:
        def create_file_dialog(self, dialog, save_filename="", file_types=()):
            seen["dialog"] = dialog
            seen["name"] = save_filename
            seen["types"] = file_types
            return str(tmp_path / save_filename)

    text = "[Interface]\r\nPrivateKey = abc\nJc = 4\r\n"
    path = SaveApi(Window()).save_file("../phone.conf", text)
    raw = Path(path).read_bytes()
    assert raw == text.encode("utf-8")
    assert raw == export_bytes(text)
    assert not raw.startswith(b"\xef\xbb\xbf")
    assert b"\r\n" in raw and b"\nJc" in raw
    assert seen == {
        "dialog": "SAVE",
        "name": "phone.conf",
        "types": FILE_TYPES,
    }


def test_cancel_does_not_write(tmp_path, monkeypatch):
    _webview(monkeypatch, "SAVE")

    class Window:
        def create_file_dialog(self, dialog, save_filename="", file_types=()):
            return None

    assert SaveApi(Window()).save_file("phone.conf", "Secret = 1\n") == ""
    assert list(tmp_path.iterdir()) == []


def test_png_data_url_is_decoded(tmp_path, monkeypatch):
    _webview(monkeypatch, "SAVE")
    payload = b"\x89PNG\r\n\x1a\n"

    class Window:
        def create_file_dialog(self, dialog, save_filename="", file_types=()):
            assert file_types[0] == "PNG (*.png)"
            return str(tmp_path / save_filename)

    text = "data:image/png;base64," + base64.b64encode(payload).decode("ascii")
    path = SaveApi(Window()).save_file("phone.png", text)
    assert Path(path).read_bytes() == payload


def test_webview_downloads_are_allowed():
    host = types.SimpleNamespace(settings={})
    configure_webview(host)
    assert host.settings["ALLOW_DOWNLOADS"] is True
