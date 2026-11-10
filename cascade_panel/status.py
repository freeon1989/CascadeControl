"""Живой опрос узла: SSH-метрики, TCP-пинг и пиры AmneziaWG с входа.

Скрипт ничего не устанавливает. Он нужен и до развёртывания каскада:
CPU, память, диск, аптайм и состояние служб читаются с любой Linux-машины,
куда открыт SSH.

TODO: клиенты VLESS. На входе нет StatsService, поэтому `xray api statsquery`
обычно молчит. Если ответ всё же пришёл, панель разбирает
`user>>>uuid>>>traffic>>>uplink/downlink`. Включать API в конфиг Xray
нельзя вслепую: это другой inbound рядом с tproxy.
"""

from __future__ import annotations

import base64
import json
import re
import socket
import time
from datetime import datetime, timezone

_JSON_RE = re.compile(r"CASCADE_STATS\s*(\{.*\})")
ONLINE_SECONDS = 180
_XRAY_USER = re.compile(r"^user>>>(.+)>>>traffic>>>(uplink|downlink)$")


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


def _wg_key(value: str) -> bool:
    if len(value) < 40 or len(value) > 64:
        return False
    try:
        raw = base64.b64decode(value, validate=True)
    except Exception:
        return False
    return len(raw) == 32


def _endpoint(value: str) -> bool:
    text = value.strip()
    if text in {"", "(none)"}:
        return True
    return ":" in text


def parse_awg_dump(text: str) -> list[dict]:
    """Строки `awg show cawg0 dump`: ключ, рукопожатие, rx, tx.

    Первая строка интерфейса тоже с табами, но во втором поле у неё порт,
    а не endpoint. Её пропускаем, чтобы не принять ключ сервера за клиента.
    """
    peers: list[dict] = []
    seen: set[str] = set()
    for raw in text.splitlines():
        parts = raw.strip().split("\t")
        if len(parts) < 7 or not _endpoint(parts[2]) or not _wg_key(parts[0].strip()):
            continue
        try:
            handshake = int(parts[4])
            rx = int(parts[5])
            tx = int(parts[6])
        except ValueError:
            continue
        key = parts[0].strip()
        if key in seen:
            continue
        seen.add(key)
        peers.append({"public_key": key, "handshake": handshake, "rx": max(0, rx), "tx": max(0, tx)})
    return peers


def parse_xray_stats(text: str) -> list[dict]:
    """Разбор `xray api statsquery`. Пустой список, если API не ответил.

    TODO: на входе StatsService выключен, поэтому сюда почти никогда
    не попадает живой ответ. Формат оставлен, чтобы включение API
    сразу начало копиться в тех же счётчиках.
    """
    if not text or not str(text).strip():
        return []
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return []
    rows = data.get("stat") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        return []
    totals: dict[str, dict[str, int]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        match = _XRAY_USER.match(str(row.get("name") or ""))
        if not match:
            continue
        user, direction = match.group(1), match.group(2)
        try:
            value = int(row.get("value") or 0)
        except (TypeError, ValueError):
            continue
        bucket = totals.setdefault(user, {"uplink": 0, "downlink": 0})
        bucket[direction] = max(0, value)
    return [
        {"user": user, "rx": bucket["downlink"], "tx": bucket["uplink"]}
        for user, bucket in totals.items()
    ]


def roll_counter(total: int, baseline: int | None, current: int) -> tuple[int, int, int]:
    """Копит байты и переживает обнуление счётчика после перезапуска awg.

    Возвращает новый итог, новую базу и прирост за этот опрос.
    Первый снимок при нулевом итоге берёт текущий счётчик как есть.
    Если итог уже был (демо или прошлая панель), база ставится молча,
    чтобы не прибавить историю второй раз.
    """
    current = max(0, int(current))
    total = max(0, int(total or 0))
    if baseline is None:
        if total:
            return total, current, 0
        return current, current, current
    baseline = max(0, int(baseline))
    delta = current - baseline if current >= baseline else current
    return total + delta, current, delta


def handshake_online(handshake: int, now: datetime) -> bool:
    if handshake <= 0:
        return False
    seen = datetime.fromtimestamp(int(handshake), timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    delta = (now - seen).total_seconds()
    return -ONLINE_SECONDS <= delta <= ONLINE_SECONDS


def handshake_stamp(handshake: int) -> str:
    return datetime.fromtimestamp(int(handshake), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def peer_rows_from_stats(remote: dict) -> list[dict] | None:
    """None значит дампа не было и клиентов трогать нельзя."""
    dump = remote.get("awg_dump")
    if isinstance(dump, str) and dump.strip():
        return parse_awg_dump(dump)
    rows = remote.get("peers")
    if not isinstance(rows, list):
        return None
    parsed = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        key = str(row.get("public_key") or "").strip()
        if not key:
            continue
        try:
            parsed.append(
                {
                    "public_key": key,
                    "handshake": int(row.get("handshake") or 0),
                    "rx": int(row.get("rx") or 0),
                    "tx": int(row.get("tx") or 0),
                }
            )
        except (TypeError, ValueError):
            continue
    return parsed


def xray_rows_from_stats(remote: dict) -> list[dict]:
    raw = remote.get("xray_stats")
    if isinstance(raw, str):
        return parse_xray_stats(raw)
    if isinstance(raw, list):
        return [row for row in raw if isinstance(row, dict)]
    return []


def _series_append(raw: str, point: int, limit: int = 48) -> str:
    try:
        series = json.loads(raw or "[]")
    except json.JSONDecodeError:
        series = []
    if not isinstance(series, list):
        series = []
    series.append(int(point))
    return json.dumps(series[-limit:])


def _baselines(raw: str) -> dict:
    try:
        data = json.loads(raw or "{}")
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def apply_awg_peers(clients: list, peers: list[dict], now: datetime) -> None:
    """Пишет online, last_seen, rx/tx и точку графика. Ключ пира = public_key клиента."""
    by_key = {str(item["public_key"]).strip(): item for item in peers}
    for client in clients:
        if getattr(client, "protocol", "") != "awg" or not getattr(client, "public_key", ""):
            continue
        peer = by_key.get(client.public_key.strip())
        if peer is None:
            client.online = False
            continue
        base = _baselines(getattr(client, "counters_json", "") or "{}")
        base_rx = base.get("rx") if "rx" in base else None
        base_tx = base.get("tx") if "tx" in base else None
        new_rx, raw_rx, delta_rx = roll_counter(client.rx_bytes, base_rx, int(peer["rx"]))
        new_tx, raw_tx, delta_tx = roll_counter(client.tx_bytes, base_tx, int(peer["tx"]))
        client.rx_bytes = new_rx
        client.tx_bytes = new_tx
        client.counters_json = json.dumps({"rx": raw_rx, "tx": raw_tx})
        client.traffic_json = _series_append(client.traffic_json, delta_rx + delta_tx)
        handshake = int(peer["handshake"])
        if handshake > 0:
            client.last_seen = handshake_stamp(handshake)
        client.online = handshake_online(handshake, now)


def apply_xray_users(clients: list, rows: list[dict], now: datetime) -> None:
    """TODO: живой статус VLESS, если StatsService уже отвечает.

    Онлайн здесь значит, что за опрос счётчик вырос. Рукопожатия у Xray нет.
    """
    if not rows:
        return
    by_user = {str(item.get("user") or ""): item for item in rows}
    stamp = now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    for client in clients:
        if getattr(client, "protocol", "") == "awg":
            continue
        user = str(getattr(client, "vless_uuid", "") or "")
        row = by_user.get(user) or by_user.get(str(getattr(client, "name", "") or ""))
        if row is None:
            continue
        base = _baselines(getattr(client, "counters_json", "") or "{}")
        base_rx = base.get("xray_rx") if "xray_rx" in base else None
        base_tx = base.get("xray_tx") if "xray_tx" in base else None
        new_rx, raw_rx, delta_rx = roll_counter(client.rx_bytes, base_rx, int(row.get("rx") or 0))
        new_tx, raw_tx, delta_tx = roll_counter(client.tx_bytes, base_tx, int(row.get("tx") or 0))
        client.rx_bytes = new_rx
        client.tx_bytes = new_tx
        stored = _baselines(client.counters_json)
        stored["xray_rx"] = raw_rx
        stored["xray_tx"] = raw_tx
        client.counters_json = json.dumps(stored)
        client.traffic_json = _series_append(client.traffic_json, delta_rx + delta_tx)
        moved = delta_rx + delta_tx > 0
        client.online = moved
        if moved:
            client.last_seen = stamp


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

agent_peers = None

def awg_dump():
    global agent_peers
    attempts = [
        prefix + ["docker", "exec", "cascade-awg", "awg", "show", "cawg0", "dump"],
        prefix + ["awg", "show", "cawg0", "dump"],
    ]
    for cmd in attempts:
        status, text = run(cmd, timeout=12)
        if status == 0 and text.strip():
            return text
    try:
        doc = json.load(open("/var/lib/cascade-agent/status.json", encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    if not isinstance(doc, dict):
        return ""
    if isinstance(doc.get("peers"), list):
        agent_peers = doc["peers"]
    saved = doc.get("awg_dump")
    return saved if isinstance(saved, str) else ""

dump = awg_dump()
if dump.strip() and awg is None:
    awg = {"up": True, "handshakes": dump.count("\n")}

# TODO: VLESS. StatsService на входе не включён, вызов обычно не удаётся.
xray_stats = None
xray_bin = "/opt/cascade/bin/xray" if os.path.exists("/opt/cascade/bin/xray") else ""
if not xray_bin and shutil.which("xray"):
    xray_bin = "xray"
if xray_bin:
    status, text = run(prefix + [xray_bin, "api", "statsquery", "--server=127.0.0.1:10085"], timeout=5)
    if status == 0 and text.strip().startswith("{"):
        xray_stats = text.strip()[:8000]

def tail_file(path, limit=50000):
    try:
        with open(path, "rb") as handle:
            handle.seek(0, 2)
            size = handle.tell()
            handle.seek(max(0, size - limit))
            return handle.read().decode("utf-8", "replace")
    except OSError:
        return None

def mem_disk():
    mem_pct = None
    try:
        info = {}
        for line in open("/proc/meminfo", encoding="utf-8", errors="replace"):
            if ":" not in line:
                continue
            key, rest = line.split(":", 1)
            info[key] = int(rest.strip().split()[0])
        total = info.get("MemTotal") or 0
        avail = info.get("MemAvailable", info.get("MemFree", 0))
        if total > 0:
            mem_pct = round(100.0 * max(0, total - avail) / total, 1)
    except (OSError, ValueError):
        mem_pct = None
    disk_pct = disk_free = disk_total = None
    try:
        st = os.statvfs("/")
        disk_total = st.f_blocks * st.f_frsize
        disk_free = st.f_bavail * st.f_frsize
        if disk_total > 0:
            disk_pct = round(100.0 * (disk_total - disk_free) / disk_total, 1)
    except OSError:
        pass
    return mem_pct, disk_pct, disk_free, disk_total

def net_bytes():
    rx = tx = 0
    try:
        for line in open("/proc/net/dev", encoding="utf-8", errors="replace"):
            if ":" not in line:
                continue
            name, rest = line.split(":", 1)
            if name.strip() == "lo":
                continue
            parts = rest.split()
            if len(parts) >= 9:
                rx += int(parts[0])
                tx += int(parts[8])
    except (OSError, ValueError):
        return None, None
    return rx, tx

def observe_extra(container_lines):
    names = []
    for line in container_lines:
        names.append(line.split("|", 1)[0])
    dns = {"has_container": "amnezia-dns" in names, "container_up": False, "log_queries": "missing", "text": "", "error": ""}
    for line in container_lines:
        if line.startswith("amnezia-dns|"):
            dns["container_up"] = line.split("|", 2)[1].lower().startswith("up")
    if dns["has_container"] and dns["container_up"]:
        status, text = run(prefix + ["docker", "exec", "amnezia-dns", "sh", "-c",
            "grep -E '^[[:space:]]*log-queries:' /opt/unbound/etc/unbound/unbound.conf || true"], timeout=12)
        if status != 0 and not text.strip():
            dns["error"] = "unreadable"
        elif "yes" in text:
            dns["log_queries"] = "yes"
        elif "no" in text:
            dns["log_queries"] = "no"
        status, text = run(prefix + ["docker", "exec", "amnezia-dns", "sh", "-c",
            "tail -c 80000 /opt/unbound/etc/unbound/unbound.log 2>/dev/null; "
            "for f in /opt/unbound/etc/unbound/log-archive/unbound-*.log; do [ -f \"$f\" ] && tail -c 20000 \"$f\"; done; "
            "tail -c 40000 /opt/adguardhome/work/data/querylog.json 2>/dev/null; "
            "tail -c 20000 /var/log/unbound/unbound.log 2>/dev/null; true"], timeout=20)
        if status != 0 and not (text or "").strip():
            dns["error"] = dns["error"] or "unreadable"
        dns["text"] = (text or "")[-120000:]
    xray = {"enabled": False, "text": "", "error": ""}
    cfg_path = "/opt/cascade/xray/config.json"
    if os.path.exists(cfg_path):
        try:
            cfg = json.load(open(cfg_path, encoding="utf-8"))
            xray["enabled"] = bool((cfg.get("log") or {}).get("access"))
        except (OSError, ValueError):
            xray["error"] = "unreadable"
    chunks = []
    for path in [ "/opt/cascade/xray/access.log" ]:
        if os.path.exists(path):
            xray["enabled"] = True
            chunk = tail_file(path)
            if chunk is None:
                xray["error"] = "unreadable"
            elif chunk:
                chunks.append(chunk)
    archive = "/opt/cascade/xray/log-archive"
    if os.path.isdir(archive):
        for name in sorted(os.listdir(archive)):
            if not name.startswith("access-"):
                continue
            chunk = tail_file(os.path.join(archive, name), 20000)
            if chunk:
                chunks.append(chunk)
    xray["text"] = "\n".join(chunks)[-120000:]
    return dns, xray

container_lines = []
status, text = run(prefix + ["docker", "ps", "-a", "--format", "{{.Names}}|{{.Status}}|{{.Image}}"], timeout=12)
if status == 0:
    container_lines = [line for line in text.splitlines() if line.strip()]
mem_pct, disk_pct, disk_free, disk_total = mem_disk()
net_rx, net_tx = net_bytes()
dns_probe, xray_access = observe_extra(container_lines)

payload = {
    "cpu": cpu_percent(),
    "ram": ram_text(),
    "disk": disk_text(),
    "uptime": uptime_text(),
    "docker": docker,
    "agent": unit("cascade-agent"),
    "xray": unit("cascade-xray"),
    "awg": awg,
    "awg_dump": dump,
    "xray_stats": xray_stats,
    "containers": container_lines,
    "mem_pct": mem_pct,
    "disk_pct": disk_pct,
    "disk_free": disk_free,
    "disk_total": disk_total,
    "net_rx": net_rx,
    "net_tx": net_tx,
    "dns_probe": dns_probe,
    "xray_access": xray_access,
}
if not dump.strip() and agent_peers is not None:
    payload["peers"] = agent_peers
print("CASCADE_STATS " + json.dumps(payload, ensure_ascii=False))
PY
"""
