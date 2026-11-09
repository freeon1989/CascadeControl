"""Журналы не должны содержать ключи и пароли."""

from __future__ import annotations

import logging
import re

_SECRET = re.compile(
    r"(?i)(private\s*key|preshared\s*key|pre-shared\s*key|password|passwd|"
    r"headerprotectionkey|header_protection_key|ssh_secret|secret|"
    r"client_priv_key|server_priv_key|psk_key|reality_private)"
    r"(\s*[=:]\s*)(\S+)"
)


def redact(text: str) -> str:
    return _SECRET.sub(lambda m: f"{m.group(1)}{m.group(2)}***", text)


class RedactFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = redact(record.msg)
        if record.args:
            record.args = tuple(redact(a) if isinstance(a, str) else a for a in record.args)
        return True


def install_redaction() -> None:
    root = logging.getLogger()
    filt = RedactFilter()
    root.addFilter(filt)
    for handler in root.handlers:
        handler.addFilter(filt)
