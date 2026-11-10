"""Ключи и ссылка VLESS + Reality.

Xray (в том числе v26.10.31) ждёт ключи x25519 в base64.RawURLEncoding:
32 байта, 43 символа, без ``=``, алфавит ``A-Za-z0-9-_``.
Стандартный base64 с ``+``, ``/`` и ``=`` конфиг Reality отклоняет.
Short ID — 8 байт hex, как ``openssl rand -hex 8`` в configure_container.sh Amnezia.
"""

from __future__ import annotations

import base64
import os
import uuid
from dataclasses import asdict, dataclass
from urllib.parse import quote

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey


@dataclass(frozen=True)
class RealityMaterial:
    uuid: str
    private_key: str
    public_key: str
    short_id: str
    sni: str
    dest: str
    fingerprint: str
    flow: str
    port: int

    def to_json(self) -> dict:
        return asdict(self)

    @classmethod
    def from_json(cls, data: dict) -> "RealityMaterial":
        return cls(**{k: data[k] for k in cls.__dataclass_fields__})


def reality_key_b64(raw: bytes) -> str:
    """32 байта x25519 в base64.RawURLEncoding, как ``xray x25519``."""
    if len(raw) != 32:
        raise ValueError("ключ Reality должен быть 32 байта")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def normalize_reality_key(value: str) -> str:
    """Приводит стандартный base64 или уже rawurl к rawurl без padding."""
    text = (value or "").strip()
    if not text:
        return text
    padded = text + ("=" * ((4 - len(text) % 4) % 4))
    try:
        raw = base64.urlsafe_b64decode(padded)
    except Exception as exc:  # noqa: BLE001
        raise ValueError("ключ Reality не разобран") from exc
    return reality_key_b64(raw)


def generate_reality(
    port: int,
    sni: str = "www.googletagmanager.com",
    fingerprint: str = "chrome",
) -> RealityMaterial:
    private = X25519PrivateKey.generate()
    priv = private.private_bytes(
        serialization.Encoding.Raw,
        serialization.PrivateFormat.Raw,
        serialization.NoEncryption(),
    )
    pub = private.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return RealityMaterial(
        uuid=str(uuid.uuid4()),
        private_key=reality_key_b64(priv),
        public_key=reality_key_b64(pub),
        short_id=os.urandom(8).hex(),
        sni=sni,
        dest=f"{sni}:443",
        fingerprint=fingerprint,
        flow="xtls-rprx-vision",
        port=port,
    )


def vless_uri(material: RealityMaterial, host: str, name: str, client_uuid: str | None = None) -> str:
    uid = client_uuid or material.uuid
    query = (
        "encryption=none"
        f"&flow={quote(material.flow)}"
        "&security=reality"
        f"&sni={quote(material.sni)}"
        f"&fp={quote(material.fingerprint)}"
        f"&pbk={quote(material.public_key)}"
        f"&sid={material.short_id}"
        "&type=tcp"
    )
    return f"vless://{uid}@{host}:{material.port}?{query}#{quote(name)}"


def subscription_body(uris: list[str]) -> str:
    payload = "\n".join(uris) + "\n"
    return base64.b64encode(payload.encode("utf-8")).decode("ascii")
