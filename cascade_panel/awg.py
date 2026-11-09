"""Генерация AmneziaWG 3.1.

Границы сверены с официальными исходниками, а не с примером из макета:

* amnezia-client ``awgInstaller::generateAwgParameters`` (ветка dev) —
  Jc случайный 4..6, Jmin=10, Jmax=50, S1..S4 = 12, H1..H4 = 1..4,
  HeaderProtectionKey = ключ ``awg genkey``, RandomTrailers=on, DisableCookies=on.
* ``protocolConstants.h`` — окна таймингов и ``defaultContentPaddingAddition = 10-100``.
* amneziawg-go README — Header Protection требует S1..S4 >= 12; nonce берётся
  из этого padding. Диапазоны (``lo-hi``) — тип uint, значение выбирается на каждой попытке.
* Обсуждение amnezia-client#3073 и документация AmneziaWG: при включённом
  HeaderProtectionKey заголовки H1..H4 должны оставаться 1, 2, 3, 4.
  Диапазоны H вместе с RandomTrailers=on дают тихую потерю пакетов.
* ``AwgConstant``: размеры сообщений 148 / 92 / 64 / 32. Одинаковый размер
  на проводе (base+S) недопустим. При равных S размеры различаются сами.
* RandomTrailers должен совпадать на обеих сторонах; при нём S1..S4
  рекомендуется держать равными.

Установщик клиента сейчас не записывает ContentPaddingAddition (пустая строка
вырезается ``sed``). Поле есть в ``template.conf`` и в константе ``10-100``,
и оно нужно роутеру владельца, поэтому мы его генерируем.
"""

from __future__ import annotations

import base64
import os
import random
from dataclasses import asdict, dataclass

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

# Размеры сообщений WireGuard/AWG без junk-padding (AwgConstant в клиенте).
MSG_INIT = 148
MSG_RESP = 92
MSG_COOKIE = 64
MSG_TRANSPORT = 32

# Официальные окна из protocolConstants.h (AWG 3.1).
REKEY_AFTER = (100, 120)
REKEY_TIMEOUT = (3, 7)
REJECT_AFTER = (150, 180)
KEEPALIVE_TIMEOUT = (5, 15)
MAX_HANDSHAKE = (15, 20)
PERSISTENT_KEEPALIVE = (25, 35)
CONTENT_PADDING = (10, 100)

S_MIN = 12  # минимум для nonce Header Protection
S_MAX = 32  # верх для S4, чтобы не раздувать transport относительно MTU


@dataclass(frozen=True)
class AwgParams:
    jc: int
    jmin: int
    jmax: int
    s1: int
    s2: int
    s3: int
    s4: int
    h1: str
    h2: str
    h3: str
    h4: str
    header_protection_key: str
    content_padding_addition: str
    rekey_after_time: str
    rekey_timeout: str
    reject_after_time: str
    keepalive_timeout: str
    max_handshake_attempts: str
    random_trailers: str
    disable_cookies: str
    persistent_keepalive: str

    def to_json(self) -> dict:
        return asdict(self)

    @classmethod
    def from_json(cls, data: dict) -> "AwgParams":
        return cls(**{k: data[k] for k in cls.__dataclass_fields__})


@dataclass(frozen=True)
class WgKeyPair:
    private_b64: str
    public_b64: str


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def generate_wg_keypair() -> WgKeyPair:
    """Ключ WireGuard: 32 байта с clamping, публичная часть — X25519."""
    private = bytearray(os.urandom(32))
    private[0] &= 248
    private[31] &= 127
    private[31] |= 64
    priv = bytes(private)
    pub = (
        X25519PrivateKey.from_private_bytes(priv)
        .public_key()
        .public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    )
    return WgKeyPair(_b64(priv), _b64(pub))


def generate_psk() -> str:
    return _b64(os.urandom(32))


def _range(rng: random.Random, lo_bound: int, hi_bound: int, min_width: int) -> str:
    if hi_bound - lo_bound < min_width:
        return f"{lo_bound}-{hi_bound}"
    lo = rng.randint(lo_bound, hi_bound - min_width)
    hi = rng.randint(lo + min_width, hi_bound)
    return f"{lo}-{hi}"


def generate_params(rng: random.Random | None = None) -> AwgParams:
    rng = rng or random.Random()
    s = rng.randint(S_MIN, S_MAX)
    params = AwgParams(
        jc=rng.randint(4, 6),
        jmin=10,
        jmax=50,
        s1=s,
        s2=s,
        s3=s,
        s4=s,
        h1="1",
        h2="2",
        h3="3",
        h4="4",
        header_protection_key=generate_wg_keypair().private_b64,
        content_padding_addition=_range(rng, *CONTENT_PADDING, 20),
        rekey_after_time=_range(rng, *REKEY_AFTER, 5),
        rekey_timeout=_range(rng, *REKEY_TIMEOUT, 2),
        reject_after_time=_range(rng, *REJECT_AFTER, 8),
        keepalive_timeout=_range(rng, *KEEPALIVE_TIMEOUT, 3),
        max_handshake_attempts=_range(rng, *MAX_HANDSHAKE, 2),
        random_trailers="on",
        disable_cookies="on",
        persistent_keepalive=_range(rng, *PERSISTENT_KEEPALIVE, 4),
    )
    validate_params(params)
    return params


def parse_range(value: str) -> tuple[int, int]:
    text = value.strip()
    if "-" in text:
        left, right = text.split("-", 1)
        lo, hi = int(left), int(right)
    else:
        lo = hi = int(text)
    if lo < 0 or hi < lo or hi > 65535:
        raise ValueError(f"недопустимый диапазон: {value}")
    return lo, hi


def _packet_sizes(p: AwgParams) -> list[int]:
    return [
        MSG_INIT + p.s1,
        MSG_RESP + p.s2,
        MSG_COOKIE + p.s3,
        MSG_TRANSPORT + p.s4,
    ]


def validate_params(p: AwgParams) -> None:
    if not 1 <= p.jc <= 12:
        raise ValueError("Jc вне диапазона 1..12 (рекомендация протокола 4..12)")
    if p.jmin < 0 or p.jmin > p.jmax:
        raise ValueError("Jmin должен быть <= Jmax")
    if p.jmax >= 1280:
        raise ValueError("Jmax не должен достигать MTU: junk-пакет фрагментируется")
    for name, value in (("S1", p.s1), ("S2", p.s2), ("S3", p.s3), ("S4", p.s4)):
        if value < S_MIN:
            raise ValueError(f"{name}={value}: для Header Protection нужно >= {S_MIN}")
    if p.random_trailers == "on" and not (p.s1 == p.s2 == p.s3 == p.s4):
        raise ValueError("при RandomTrailers=on S1..S4 должны быть равны")
    if len(set(_packet_sizes(p))) != 4:
        raise ValueError("размеры пакетов handshake/cookie/transport совпали")
    if (p.h1, p.h2, p.h3, p.h4) != ("1", "2", "3", "4"):
        raise ValueError("в режиме 3.1 с HeaderProtectionKey допустимы только H1=1..H4=4")
    raw = base64.b64decode(p.header_protection_key)
    if len(raw) != 32:
        raise ValueError("HeaderProtectionKey должен быть base64 от 32 байт")
    pad = parse_range(p.content_padding_addition)
    if pad[1] > 200:
        raise ValueError("ContentPaddingAddition слишком велик и режет MTU")
    rekey = parse_range(p.rekey_after_time)
    reject = parse_range(p.reject_after_time)
    if rekey[1] >= reject[0]:
        raise ValueError("любое RekeyAfterTime должно быть меньше любого RejectAfterTime")
    for field in (
        p.rekey_timeout,
        p.keepalive_timeout,
        p.max_handshake_attempts,
        p.persistent_keepalive,
    ):
        parse_range(field)
    if p.random_trailers not in {"on", "off"} or p.disable_cookies not in {"on", "off"}:
        raise ValueError("RandomTrailers и DisableCookies принимают on или off")


def render_client_conf(
    *,
    address: str,
    dns1: str,
    dns2: str,
    private_key: str,
    params: AwgParams,
    server_public_key: str,
    preshared_key: str,
    endpoint_host: str,
    endpoint_port: int,
) -> str:
    """Текст .conf в порядке полей официального template.conf (без пустых I1..I5)."""
    lines = [
        "[Interface]",
        f"Address = {address}",
        f"DNS = {dns1}, {dns2}",
        f"PrivateKey = {private_key}",
        f"Jc = {params.jc}",
        f"Jmin = {params.jmin}",
        f"Jmax = {params.jmax}",
        f"S1 = {params.s1}",
        f"S2 = {params.s2}",
        f"S3 = {params.s3}",
        f"S4 = {params.s4}",
        f"H1 = {params.h1}",
        f"H2 = {params.h2}",
        f"H3 = {params.h3}",
        f"H4 = {params.h4}",
        f"HeaderProtectionKey = {params.header_protection_key}",
        f"ContentPaddingAddition = {params.content_padding_addition}",
        f"RekeyAfterTime = {params.rekey_after_time}",
        f"RekeyTimeout = {params.rekey_timeout}",
        f"RejectAfterTime = {params.reject_after_time}",
        f"KeepaliveTimeout = {params.keepalive_timeout}",
        f"MaxHandshakeAttempts = {params.max_handshake_attempts}",
        f"RandomTrailers = {params.random_trailers}",
        f"DisableCookies = {params.disable_cookies}",
        "",
        "[Peer]",
        f"PublicKey = {server_public_key}",
        f"PresharedKey = {preshared_key}",
        "AllowedIPs = 0.0.0.0/0, ::/0",
        f"Endpoint = {endpoint_host}:{endpoint_port}",
        f"PersistentKeepalive = {params.persistent_keepalive}",
        "",
    ]
    return "\n".join(lines)


def render_server_conf(
    *,
    server_private_key: str,
    address_cidr: str,
    listen_port: int,
    params: AwgParams,
    peers: list[dict],
) -> str:
    """Серверный awg0.conf. I1..I5 не пишем: генератор 3.1 их не задаёт."""
    lines = [
        "[Interface]",
        f"PrivateKey = {server_private_key}",
        f"Address = {address_cidr}",
        f"ListenPort = {listen_port}",
        f"Jc = {params.jc}",
        f"Jmin = {params.jmin}",
        f"Jmax = {params.jmax}",
        f"S1 = {params.s1}",
        f"S2 = {params.s2}",
        f"S3 = {params.s3}",
        f"S4 = {params.s4}",
        f"H1 = {params.h1}",
        f"H2 = {params.h2}",
        f"H3 = {params.h3}",
        f"H4 = {params.h4}",
        f"HeaderProtectionKey = {params.header_protection_key}",
        f"ContentPaddingAddition = {params.content_padding_addition}",
        f"RekeyAfterTime = {params.rekey_after_time}",
        f"RekeyTimeout = {params.rekey_timeout}",
        f"RejectAfterTime = {params.reject_after_time}",
        f"KeepaliveTimeout = {params.keepalive_timeout}",
        f"MaxHandshakeAttempts = {params.max_handshake_attempts}",
        f"RandomTrailers = {params.random_trailers}",
        f"DisableCookies = {params.disable_cookies}",
        "",
    ]
    for peer in peers:
        lines.extend(
            [
                "[Peer]",
                f"PublicKey = {peer['public_key']}",
                f"PresharedKey = {peer['preshared_key']}",
                f"AllowedIPs = {peer['address']}/32",
                "",
            ]
        )
    return "\n".join(lines)


INTERFACE_FIELDS = [
    "Address",
    "DNS",
    "PrivateKey",
    "Jc",
    "Jmin",
    "Jmax",
    "S1",
    "S2",
    "S3",
    "S4",
    "H1",
    "H2",
    "H3",
    "H4",
    "HeaderProtectionKey",
    "ContentPaddingAddition",
    "RekeyAfterTime",
    "RekeyTimeout",
    "RejectAfterTime",
    "KeepaliveTimeout",
    "MaxHandshakeAttempts",
    "RandomTrailers",
    "DisableCookies",
]

PEER_FIELDS = [
    "PublicKey",
    "PresharedKey",
    "AllowedIPs",
    "Endpoint",
    "PersistentKeepalive",
]


def parse_conf(text: str) -> dict[str, dict[str, str]]:
    section = ""
    out: dict[str, dict[str, str]] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1]
            out.setdefault(section, {})
            continue
        if "=" not in line or not section:
            continue
        key, value = line.split("=", 1)
        out[section][key.strip()] = value.strip()
    return out
