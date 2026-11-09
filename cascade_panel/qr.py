"""QR-код конфига. Длинные ссылки vpn:// кодируются с уровнем L."""

from __future__ import annotations

import base64
import io

import qrcode
from qrcode.constants import ERROR_CORRECT_L


def png_data_url(text: str) -> str:
    image = qrcode.make(text, error_correction=ERROR_CORRECT_L, border=2)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return "data:image/png;base64," + encoded
