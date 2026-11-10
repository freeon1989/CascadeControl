"""Живой опрос узла: SSH-метрики и TCP-пинг с панели.

Скрипт ничего не устанавливает. Он нужен и до развёртывания каскада:
CPU, память, диск, аптайм и состояние служб читаются с любой Linux-машины,
куда открыт SSH.
"""

from __future__ import annotations

import json
import re
import socket
import time

_JSON_RE = re.compile(r"CASCADE_STATS\s*(\{.*\})")


def tcp_ping(host: str, port: int, timeout: float = 3.0) -> int | None:
    started = time.perf_counter()
    try:
        sock = socket.create_connection((host, int(port)), timeout)
    except OSError:
        return None
    try:
        sock.close()
    except OSError:
        pass
    return max(1, int((time.perf_counter() - started) * 1000))


def parse_stats_output(text: str) -> dict:
    match = None
    for line in text.splitlines():
        found = _JSON_RE.search(line.strip())
        if found:
            match = found
    if not match:
        raise ValueError("в выводе статуса нет строки CASCADE_STATS")
    data = json.loads(match.group(1))
    if not isinstance(data, dict):
        raise ValueError("статус сервера не разобран")
    return data


STATS_SCRIPT = r"""#!/bin/bash
# Снимок состояния. Ничего не меняет на сервере.
if ! command -v python3 >/dev/null 2>&1; then
  printf '%s\n' 'CASCADE_STATS {"cpu":null,"ram":"","disk":"","uptime":"","docker":"missing","agent":"missing","xray":"missing","awg":null}'
  exit 0
fi
exec python3 - << 'PY'
import json
import os
import shutil
import subprocess
import time

def run(cmd, timeout=8):
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return proc.returncode, proc.stdout or ""
    except (OSError, subprocess.SubprocessError):
        return 1, ""

prefix = [] if os.geteuid() == 0 else ["sudo", "-n"]

def cpu_percent():
    def snap():
        parts = open("/proc/stat", encoding="utf-8", errors="replace").readline().split()[1:]
        nums = [int(item) for item in parts if item.isdigit()]
        idle = nums[3] + (nums[4] if len(nums) > 4 else 0)
        return idle, sum(nums)
    try:
        first = snap()
        time.sleep(0.25)
        second = snap()
    except (OSError, ValueError, IndexError):
        return None
    idle = second[0] - first[0]
    total = second[1] - first[1]
    if total <= 0:
        return None
    return round(100.0 * (1.0 - idle / total), 1)

def ram_text():
    info = {}
    try:
        for line in open("/proc/meminfo", encoding="utf-8", errors="replace"):
            if ":" not in line:
                continue
            key, rest = line.split(":", 1)
            info[key] = int(rest.strip().split()[0])
    except (OSError, ValueError):
        return ""
    total = info.get("MemTotal") or 0
    avail = info.get("MemAvailable", info.get("MemFree", 0))
    if total <= 0:
        return ""
    used = max(0, total - avail)
    return f"{used / 1048576:.1f} / {total / 1048576:.1f} ГБ"

def disk_text():
    try:
        st = os.statvfs("/")
    except OSError:
        return ""
    total = st.f_blocks * st.f_frsize
    free = st.f_bavail * st.f_frsize
    if total <= 0:
        return ""
    used = max(0, total - free)
    return f"{used / 1024 ** 3:.0f} / {total / 1024 ** 3:.0f} ГБ"

def uptime_text():
    try:
        seconds = float(open("/proc/uptime", encoding="utf-8").read().split()[0])
    except (OSError, ValueError, IndexError):
        return ""
    days = int(seconds // 86400)
    hours = int((seconds % 86400) // 3600)
    minutes = int((seconds % 3600) // 60)
    if days:
        return f"{days} д {hours} ч"
    if hours:
        return f"{hours} ч {minutes} мин"
    return f"{minutes} мин"

def unit(name):
    code, out = run(prefix + ["systemctl", "is-active", name])
    text = out.strip().splitlines()[-1].strip() if out.strip() else ""
    if code != 0 and not text:
        return "missing"
    return text or "missing"

docker = "missing"
if shutil.which("docker"):
    code, _out = run(prefix + ["docker", "info"], timeout=12)
    docker = "active" if code == 0 else "inactive"

awg = None
code, out = run(prefix + ["docker", "exec", "cascade-awg", "awg", "show", "cawg0"], timeout=12)
if code == 0:
    awg = {"up": True, "handshakes": out.count("latest handshake:")}

payload = {
    "cpu": cpu_percent(),
    "ram": ram_text(),
    "disk": disk_text(),
    "uptime": uptime_text(),
    "docker": docker,
    "agent": unit("cascade-agent"),
    "xray": unit("cascade-xray"),
    "awg": awg,
}
print("CASCADE_STATS " + json.dumps(payload, ensure_ascii=False))
PY
"""
