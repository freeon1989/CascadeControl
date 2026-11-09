"""Пути и параметры процесса. Панель слушает только loopback."""

from __future__ import annotations

import os
import sys
from pathlib import Path

HOST = "127.0.0.1"
DEFAULT_PORT = 47123

# Контейнеры Amnezia, которые панель никогда не останавливает и не удаляет.
AMNEZIA_PREFIX = "amnezia-"
OWN_PREFIX = "cascade-"

AWG_SUBNET = "10.10.1.0"
AWG_CIDR = 24
AWG_SERVER_IP = "10.10.1.1"
CLIENT_IP_START = 2

MIN_DISK_GB = 2.0
WARN_DISK_GB = 10.0
MIN_KERNEL = (5, 4)

ALLOWED_OS = {
    ("ubuntu", "22.04"),
    ("ubuntu", "24.04"),
    ("debian", "12"),
}


def data_dir() -> Path:
    raw = os.environ.get("CASCADE_DATA_DIR")
    path = Path(raw).expanduser() if raw else Path.home() / ".cascade-control"
    path.mkdir(parents=True, exist_ok=True)
    return path


def db_path() -> Path:
    return data_dir() / "cascade.db"


def master_key_path() -> Path:
    return data_dir() / "master.key"


def port() -> int:
    raw = os.environ.get("CASCADE_PORT", str(DEFAULT_PORT))
    return int(raw)


def seed_enabled() -> bool:
    return os.environ.get("CASCADE_SEED", "1") != "0"


def web_dist() -> Path:
    env = os.environ.get("CASCADE_WEB_DIST")
    if env:
        return Path(env)
    bundled = getattr(sys, "_MEIPASS", None)
    if bundled:
        return Path(bundled) / "web" / "dist"
    return Path(__file__).resolve().parent.parent / "web" / "dist"


def agent_binary() -> Path:
    env = os.environ.get("CASCADE_AGENT_BIN")
    if env:
        return Path(env)
    return Path(__file__).resolve().parent.parent / "agent" / "dist" / "cascade-agent"
