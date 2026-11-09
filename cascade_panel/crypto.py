"""Шифрование секретов на диске. Ключ живёт только в файле master.key (0600)."""

from __future__ import annotations

import base64
import os
import stat
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .settings import master_key_path


class Vault:
    def __init__(self, key: bytes) -> None:
        if len(key) != 32:
            raise ValueError("master key must be 32 bytes")
        self._aes = AESGCM(key)

    def encrypt(self, plaintext: str) -> str:
        nonce = os.urandom(12)
        blob = nonce + self._aes.encrypt(nonce, plaintext.encode("utf-8"), None)
        return base64.b64encode(blob).decode("ascii")

    def decrypt(self, token: str) -> str:
        raw = base64.b64decode(token)
        if len(raw) < 13:
            raise ValueError("cipher blob is too short")
        return self._aes.decrypt(raw[:12], raw[12:], None).decode("utf-8")


def load_or_create_key(path: Path | None = None) -> bytes:
    path = path or master_key_path()
    if path.exists():
        key = path.read_bytes()
        if len(key) != 32:
            raise RuntimeError(f"повреждён master key: {path}")
        return key
    path.parent.mkdir(parents=True, exist_ok=True)
    key = os.urandom(32)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    fd = os.open(path, flags, 0o600)
    try:
        os.write(fd, key)
    finally:
        os.close(fd)
    try:
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass
    return key


_VAULT: Vault | None = None


def vault() -> Vault:
    global _VAULT
    if _VAULT is None:
        _VAULT = Vault(load_or_create_key())
    return _VAULT


def reset_vault_cache() -> None:
    global _VAULT
    _VAULT = None
