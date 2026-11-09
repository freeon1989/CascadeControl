"""Ссылка vpn:// в формате импорта Amnezia.

Клиент делает qCompress(JSON, 8) и base64url без ``=``.
``last_config`` — строка с повторно закодированным JSON, не вложенный объект
(AwgProtocolConfig::toJson в amnezia-client).
"""

from __future__ import annotations

import base64
import json
import zlib

from .awg import AwgParams


def qcompress(data: bytes, level: int = 8) -> bytes:
    # Qt: 4 байта big-endian длины исходника, затем zlib.
    compressed = zlib.compress(data, level)
    return len(data).to_bytes(4, "big") + compressed


def quncompress(blob: bytes) -> bytes:
    if len(blob) < 5:
        raise ValueError("qCompress blob слишком короткий")
    expected = int.from_bytes(blob[:4], "big")
    data = zlib.decompress(blob[4:])
    if len(data) != expected:
        raise ValueError("длина после qUncompress не совпала с заголовком")
    return data


def build_last_config(
    *,
    native_conf: str,
    host: str,
    port: int,
    client_ip: str,
    client_private: str,
    client_public: str,
    server_public: str,
    psk: str,
    params: AwgParams,
    dns1: str,
    dns2: str,
    mtu: str = "1376",
) -> dict:
    return {
        "config": native_conf,
        "hostName": host,
        "port": port,
        "client_ip": client_ip,
        "client_priv_key": client_private,
        "client_pub_key": client_public,
        "server_pub_key": server_public,
        "psk_key": psk,
        "allowed_ips": ["0.0.0.0/0", "::/0"],
        "persistent_keep_alive": params.persistent_keepalive,
        "mtu": mtu,
        "isObfuscationEnabled": True,
        "Jc": str(params.jc),
        "Jmin": str(params.jmin),
        "Jmax": str(params.jmax),
        "S1": str(params.s1),
        "S2": str(params.s2),
        "S3": str(params.s3),
        "S4": str(params.s4),
        "H1": params.h1,
        "H2": params.h2,
        "H3": params.h3,
        "H4": params.h4,
        "HeaderProtectionKey": params.header_protection_key,
        "ContentPaddingAddition": params.content_padding_addition,
        "RekeyAfterTime": params.rekey_after_time,
        "RekeyTimeout": params.rekey_timeout,
        "RejectAfterTime": params.reject_after_time,
        "KeepaliveTimeout": params.keepalive_timeout,
        "MaxHandshakeAttempts": params.max_handshake_attempts,
        "RandomTrailers": params.random_trailers,
        "DisableCookies": params.disable_cookies,
        "dns1": dns1,
        "dns2": dns2,
    }


def build_envelope(
    *,
    host: str,
    description: str,
    port: int,
    last_config: dict,
    dns1: str,
    dns2: str,
) -> dict:
    last_raw = json.dumps(last_config, ensure_ascii=False, separators=(",", ":"))
    return {
        "hostName": host,
        "description": description,
        "dns1": dns1,
        "dns2": dns2,
        "defaultContainer": "amnezia-awg",
        "containers": [
            {
                "container": "amnezia-awg",
                "awg": {
                    "port": str(port),
                    "transport_proto": "udp",
                    "protocol_version": "3.1",
                    "last_config": last_raw,
                    "isObfuscationEnabled": True,
                },
            }
        ],
    }


def encode_vpn_uri(envelope: dict) -> str:
    raw = json.dumps(envelope, ensure_ascii=False).encode("utf-8")
    blob = qcompress(raw, 8)
    token = base64.urlsafe_b64encode(blob).decode("ascii").rstrip("=")
    return "vpn://" + token


def decode_vpn_uri(uri: str) -> dict:
    if not uri.startswith("vpn://"):
        raise ValueError("ссылка должна начинаться с vpn://")
    token = uri[len("vpn://") :]
    pad = "=" * (-len(token) % 4)
    blob = base64.urlsafe_b64decode(token + pad)
    return json.loads(quncompress(blob))
