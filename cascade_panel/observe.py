"""Мониторинг узлов и журнал сайтов.

Парсер DNS повторяет AmneziaMonitor: строка Unbound
`[epoch] unbound[x:y] info: <client_ip> name. TYPE IN` и строка AdGuard querylog.json.
Панель сама ничего не включает, пока пользователь не подтвердит кнопку.
"""

from __future__ import annotations

import json
import re
import shlex
import time
from datetime import datetime, timezone

_UNBOUND_RE = re.compile(r"(?:^\[(\d+)\].*?)?info: ([0-9a-fA-F.:]+) (\S+?)\.? (\w+) IN\b")
_XRAY_ACCESS_RE = re.compile(
    r"from (\d+\.\d+\.\d+\.\d+):\d+ accepted \w+:([A-Za-z0-9._-]+):\d+"
)
_XRAY_TS_RE = re.compile(r"^(\d{4})/(\d{2})/(\d{2}) (\d{2}):(\d{2}):(\d{2})")
_SKIP_DOMAINS = (".in-addr.arpa", ".ip6.arpa", ".local", ".lan", ".internal", "localhost")
_SLD = {"co", "com", "org", "net", "gov", "edu", "ac", "msk", "spb"}
_CONTAINER_TYPES = ("awg", "wireguard", "xray", "openvpn", "cloak", "shadowsocks", "ipsec", "dns", "sftp", "tor")

CPU_ALERT = 85.0
MEM_ALERT = 90.0
XRAY_ACCESS_PATH = "/opt/cascade/xray/access.log"
UNBOUND_CONF = "/opt/unbound/etc/unbound/unbound.conf"
UNBOUND_DIR = "/opt/unbound/etc/unbound"

DNS_NOTES = {
    "not_enabled": "Журнал выключен. Кнопка ниже включает его на этом сервере. "
    "Будут сохраняться домены, которые запрашивают клиенты. Предупредите их.",
    "no_container": "Контейнера amnezia-dns нет, и это не вход каскада. "
    "Без журнала DNS или журнала Xray видны только объёмы, не сайты.",
    "unreadable": "Журнал должен быть, но файл прочитать не удалось. "
    "Контейнер мог быть пересоздан. Можно применить настройку ещё раз.",
    "empty": "Запись включена, но за последние сутки запросов нет. "
    "Так бывает, если на телефоне включён безопасный DNS и запросы идут мимо туннеля.",
    "ok": "",
}


def parse_dns_log(text: str) -> list[dict]:
    """Unbound log-queries и AdGuard querylog.json. Служебные PTR не берём."""
    found: list[dict] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        item = None
        if line.startswith("{"):
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                payload = None
            if isinstance(payload, dict) and payload.get("QH") and payload.get("IP"):
                stamp = None
                try:
                    stamp = datetime.fromisoformat(str(payload.get("T", "")).replace("Z", "+00:00")).timestamp()
                except ValueError:
                    stamp = None
                item = {
                    "ts": stamp,
                    "ip": str(payload["IP"]),
                    "domain": str(payload["QH"]),
                    "qtype": str(payload.get("QT") or ""),
                }
        else:
            match = _UNBOUND_RE.search(line)
            if match:
                item = {
                    "ts": float(match.group(1)) if match.group(1) else None,
                    "ip": match.group(2),
                    "domain": match.group(3),
                    "qtype": match.group(4),
                }
        if item is None:
            continue
        domain = item["domain"].rstrip(".").lower()
        if not domain or domain.endswith(_SKIP_DOMAINS) or "." not in domain:
            continue
        item["domain"] = domain
        item["line"] = line
        found.append(item)
    return found


def parse_xray_access(text: str) -> list[dict]:
    """Домен из access-лога Xray. IP вместо имени пропускаем: это не сайт."""
    found: list[dict] = []
    for raw in text.splitlines():
        line = raw.strip()
        match = _XRAY_ACCESS_RE.search(line)
        if not match:
            continue
        domain = match.group(2).rstrip(".").lower()
        if not domain or domain.endswith(_SKIP_DOMAINS) or "." not in domain:
            continue
        if all(part.isdigit() for part in domain.split(".")):
            continue
        stamp = None
        ts_match = _XRAY_TS_RE.match(line)
        if ts_match:
            year, month, day, hour, minute, second = (int(part) for part in ts_match.groups())
            stamp = datetime(year, month, day, hour, minute, second, tzinfo=timezone.utc).timestamp()
        found.append(
            {
                "ts": stamp,
                "ip": match.group(1),
                "domain": domain,
                "qtype": "SNI",
                "line": line,
            }
        )
    return found


def root_domain(domain: str) -> str:
    parts = domain.rstrip(".").lower().split(".")
    if len(parts) >= 3 and len(parts[-1]) == 2 and parts[-2] in _SLD:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:]) if len(parts) >= 2 else domain


def parse_containers(text: str) -> list[dict]:
    """Строки `имя|статус|образ`. Берём только amnezia-* и cascade-*."""
    rows = []
    for line in text.splitlines():
        parts = line.strip().split("|")
        if len(parts) < 2 or not parts[0]:
            continue
        name, status = parts[0], parts[1]
        image = parts[2] if len(parts) > 2 else ""
        if not (name.startswith(("amnezia-", "cascade-")) or "amnezia" in image.lower()):
            continue
        rows.append(
            {
                "name": name,
                "status": status,
                "up": status.lower().startswith("up"),
                "type": container_type(name),
            }
        )
    return rows


def container_type(name: str) -> str:
    lowered = name.lower()
    for kind in _CONTAINER_TYPES:
        if kind in lowered:
            return kind
    return "other"


def bucketize(points: list[tuple[float, float | None]], start: float, end: float, buckets: int, how: str = "avg") -> list:
    if buckets <= 0 or end <= start:
        return []
    size = (end - start) / buckets
    bins: list[list[float]] = [[] for _ in range(buckets)]
    for ts, value in points:
        if value is None or ts < start or ts > end:
            continue
        index = min(buckets - 1, int((ts - start) / size))
        bins[index].append(float(value))
    result = []
    for values in bins:
        if how == "sum":
            result.append(sum(values))
        elif not values:
            result.append(None)
        elif how == "max":
            result.append(max(values))
        else:
            result.append(sum(values) / len(values))
    return result


def fresh_lines(entries: list[dict], last_line: str | None) -> list[dict]:
    if not entries:
        return []
    lines = [item["line"] for item in entries]
    if last_line and last_line in lines:
        start = len(lines) - lines[::-1].index(last_line)
        return entries[start:]
    return entries


def classify_sites(
    *,
    role: str,
    has_dns_container: bool,
    container_up: bool,
    log_queries: str,
    dns_error: str,
    xray_enabled: bool,
    xray_error: str,
    parsed: int,
) -> str:
    """not_enabled, no_container, unreadable, empty или ok."""
    if parsed > 0:
        return "ok"
    if dns_error == "unreadable" or xray_error == "unreadable":
        return "unreadable"
    entry = role in {"entry", "standby_entry"}
    if has_dns_container:
        if not container_up:
            return "unreadable"
        if log_queries != "yes":
            return "not_enabled"
        return "empty"
    if entry:
        if not xray_enabled:
            return "not_enabled"
        return "empty"
    return "no_container"


def protocol_status(containers: list[dict], live: dict) -> list[dict]:
    rows = []
    seen = set()
    for item in containers:
        seen.add(item["type"])
        rows.append(
            {
                "id": item["name"],
                "type": item["type"],
                "up": bool(item["up"]),
                "detail": item["status"],
            }
        )
    awg = live.get("awg") if isinstance(live.get("awg"), dict) else None
    if "awg" not in seen and awg:
        rows.append({"id": "cawg0", "type": "awg", "up": bool(awg.get("up")), "detail": "интерфейс на хосте"})
    xray = str(live.get("xray") or "")
    if "xray" not in seen and xray and xray != "missing":
        rows.append({"id": "cascade-xray", "type": "xray", "up": xray == "active", "detail": xray})
    return rows


def patch_unbound_conf(text: str, enable: bool) -> str:
    """В секции server: включает или выключает log-queries. Остальные секции не трогает."""
    lines = text.replace("\r\n", "\n").split("\n")
    start = None
    for index, line in enumerate(lines):
        if line.strip() == "server:":
            start = index
            break
    if start is None:
        raise ValueError("в unbound.conf нет секции server:")
    end = len(lines)
    for index in range(start + 1, len(lines)):
        stripped = lines[index].strip()
        if stripped.endswith(":") and not lines[index].startswith((" ", "\t")):
            end = index
            break
    body = lines[start + 1 : end]
    keys = {
        "log-queries": "yes" if enable else "no",
        "use-syslog": "no",
    }
    if enable:
        keys["logfile"] = "unbound.log"
    else:
        keys["logfile"] = '""'

    def replace_key(block: list[str], key: str, value: str) -> list[str]:
        pattern = re.compile(rf"^(\s*){re.escape(key)}\s*:")
        replaced = False
        result = []
        for row in block:
            if pattern.match(row):
                indent = pattern.match(row).group(1) or "    "
                result.append(f"{indent}{key}: {value}")
                replaced = True
            else:
                result.append(row)
        if not replaced:
            result.append(f"    {key}: {value}")
        return result

    for key, value in keys.items():
        body = replace_key(body, key, value)
    if enable and not any(re.match(r"^\s*verbosity\s*:", row) for row in body):
        body.append("    verbosity: 1")
    return "\n".join(lines[: start + 1] + body + lines[end:]).rstrip() + "\n"


def patch_xray_access(config: dict, enable: bool, path: str = XRAY_ACCESS_PATH) -> dict:
    """Меняет только log.access. Маршруты, ключи и порты остаются как были."""
    cloned = json.loads(json.dumps(config))
    log = dict(cloned.get("log") or {})
    if enable:
        log["access"] = path
        log.setdefault("loglevel", "warning")
    else:
        log.pop("access", None)
    cloned["log"] = log
    return cloned


def archives_to_delete(
    files: list[tuple[str, int, float]],
    now: float,
    retention_hours: float,
    cap_bytes: int,
) -> list[str]:
    """files: имя, размер, mtime. Сначала старше срока, затем самые старые, пока сумма выше потолка."""
    ordered = sorted(files, key=lambda item: item[2])
    remove: list[str] = []
    kept: list[tuple[str, int, float]] = []
    horizon = now - retention_hours * 3600
    for name, size, mtime in ordered:
        if mtime < horizon:
            remove.append(name)
        else:
            kept.append((name, size, mtime))
    total = sum(size for _name, size, _mtime in kept)
    for name, size, _mtime in kept:
        if total <= cap_bytes:
            break
        remove.append(name)
        total -= size
    return remove


def render_rotate_script() -> str:
    """Крутит активный журнал раз в запуск: архив, срок и потолок размера."""
    return r"""#!/bin/bash
# Ротация журналов. Активный файл обнуляется, удаляются только архивы.
set -u
python3 - << 'PY'
import json, shutil, subprocess, time
from pathlib import Path

spec_path = Path("/etc/cascade/log-rotate.json")
if not spec_path.is_file():
    raise SystemExit(0)
spec = json.loads(spec_path.read_text(encoding="utf-8"))
now = time.time()

def choose(files, retention, cap):
    ordered = sorted(files, key=lambda item: item[2])
    horizon = now - float(retention) * 3600
    remove, kept = [], []
    for name, size, mtime in ordered:
        if mtime < horizon:
            remove.append(name)
        else:
            kept.append((name, size, mtime))
    total = sum(size for _name, size, _mtime in kept)
    for name, size, _mtime in kept:
        if total <= cap:
            break
        remove.append(name)
        total -= size
    return remove

def quote(name):
    return "'" + str(name).replace("'", "'\\''") + "'"

def rotate(job):
    retention = float(job.get("retention_hours") or 24)
    cap = int(job.get("cap_bytes") or 50 * 1024 * 1024)
    prefix = job["prefix"]
    archive = job["archive"]
    active = job["active"]
    if job.get("via") == "docker":
        container = job["container"]
        directory = job["directory"]
        inner = (
            f"cd {directory} && mkdir -p {archive} && "
            f"if [ -s {active} ]; then cp -f {active} {archive}/{prefix}$(date -u +%Y%m%d%H%M%S).log && : > {active}; fi; "
            f"for f in {archive}/{prefix}*.log; do [ -f \"$f\" ] || continue; stat -c '%n %s %Y' \"$f\"; done"
        )
        proc = subprocess.run(["docker", "exec", container, "sh", "-c", inner], capture_output=True, text=True)
        files = []
        for line in (proc.stdout or "").splitlines():
            parts = line.rsplit(" ", 2)
            if len(parts) != 3:
                continue
            try:
                files.append((parts[0], int(float(parts[1])), float(parts[2])))
            except ValueError:
                continue
        drop = choose(files, retention, cap)
        if drop:
            subprocess.run(["docker", "exec", container, "sh", "-c", "rm -f " + " ".join(quote(name) for name in drop)], check=False)
        return
    directory = Path(job["directory"])
    folder = directory / archive
    folder.mkdir(parents=True, exist_ok=True)
    source = directory / active
    if source.is_file() and source.stat().st_size > 0:
        stamp = time.strftime("%Y%m%d%H%M%S", time.gmtime(now))
        shutil.copyfile(source, folder / f"{prefix}{stamp}.log")
        source.write_text("", encoding="utf-8")
    files = []
    for path in folder.glob(prefix + "*.log"):
        st = path.stat()
        files.append((str(path), st.st_size, st.st_mtime))
    for name in choose(files, retention, cap):
        Path(name).unlink(missing_ok=True)

for job in spec.get("jobs") or []:
    try:
        rotate(job)
    except Exception:
        continue
PY
"""


def render_rotate_units() -> tuple[str, str]:
    service = """[Unit]
Description=Cascade log rotate
[Service]
Type=oneshot
ExecStart=/opt/cascade/bin/cascade-log-rotate.sh
"""
    timer = """[Unit]
Description=Cascade log rotate every 15 minutes
[Timer]
OnBootSec=2min
OnUnitActiveSec=15min
Persistent=true
[Install]
WantedBy=timers.target
"""
    return service, timer


def _run(ssh, command: str) -> tuple[int, str]:
    lines: list[str] = []
    code = ssh.run(command, lambda line: lines.append(line))
    return code, "\n".join(lines)


def _root(command: str) -> str:
    """Одна команда от root, если вход не root."""
    return 'if [ "$(id -u)" -eq 0 ]; then ' + command + '; else sudo -n ' + command + "; fi"


def apply_dns_logging(
    ssh,
    *,
    enable: bool,
    role: str,
    retention_hours: int,
    cap_mb: int,
    disk_free: int | None,
) -> list[str]:
    """Включает или выключает журнал. amnezia-awg и правила Xray не трогает."""
    if retention_hours < 1 or retention_hours > 168:
        raise ValueError("срок хранения: от 1 до 168 часов")
    if cap_mb < 5 or cap_mb > 500:
        raise ValueError("потолок журнала: от 5 до 500 МБ")
    if enable and disk_free is not None and disk_free < 200 * 1024 * 1024:
        raise ValueError("на диске меньше 200 МБ свободно, журнал не включаю")
    logs = [f"свободно на диске: {disk_free if disk_free is not None else 'неизвестно'} байт"]
    code, listing = _run(ssh, _root("docker ps -a --format '{{.Names}}|{{.Status}}|{{.Image}}'"))
    containers = parse_containers(listing) if code == 0 else []
    dns = next((item for item in containers if item["name"] == "amnezia-dns"), None)
    if dns is not None:
        logs.extend(_apply_unbound(ssh, enable=enable, retention_hours=retention_hours, cap_mb=cap_mb))
        return logs
    if role in {"entry", "standby_entry"}:
        logs.extend(_apply_xray(ssh, enable=enable, retention_hours=retention_hours, cap_mb=cap_mb))
        return logs
    raise ValueError("нет контейнера amnezia-dns, а узел не является входом")


def _apply_unbound(ssh, *, enable: bool, retention_hours: int, cap_mb: int) -> list[str]:
    code, text = _run(ssh, _root(f"docker exec amnezia-dns cat {shlex.quote(UNBOUND_CONF)}"))
    if code != 0 or "server:" not in text:
        raise ValueError("конфиг Unbound в amnezia-dns не прочитан")
    patched = patch_unbound_conf(text, enable)
    ssh.write_file("/tmp/cascade-unbound.conf", patched.encode("utf-8"), 0o644)
    code, _out = _run(
        ssh,
        _root("docker cp /tmp/cascade-unbound.conf amnezia-dns:/opt/unbound/etc/unbound/unbound.conf"),
    )
    if code != 0:
        raise ValueError("не удалось записать unbound.conf обратно в контейнер")
    _install_rotate(
        ssh,
        {
            "via": "docker",
            "container": "amnezia-dns",
            "directory": UNBOUND_DIR,
            "active": "unbound.log",
            "archive": "log-archive",
            "prefix": "unbound-",
            "retention_hours": retention_hours,
            "cap_bytes": cap_mb * 1024 * 1024,
        },
    )
    code, _out = _run(ssh, _root("docker restart amnezia-dns"))
    if code != 0:
        raise ValueError("amnezia-dns не перезапустился")
    state = "включён" if enable else "выключен"
    return [
        f"Unbound: log-queries {state}, logfile unbound.log рядом с конфигом",
        "контейнер amnezia-dns перезапущен",
        "если Amnezia пересоздаст контейнер без тома с конфигом, панель предложит применить настройку снова",
    ]


def _apply_xray(ssh, *, enable: bool, retention_hours: int, cap_mb: int) -> list[str]:
    code, text = _run(ssh, _root("cat /opt/cascade/xray/config.json"))
    if code != 0 or not text.strip():
        raise ValueError("на входе нет /opt/cascade/xray/config.json")
    try:
        config = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError("конфиг Xray не разобран") from exc
    patched = patch_xray_access(config, enable)
    ssh.write_file("/tmp/cascade-xray.json", json.dumps(patched, ensure_ascii=False, indent=2).encode("utf-8"), 0o600)
    code, _out = _run(ssh, _root("cp /tmp/cascade-xray.json /opt/cascade/xray/config.json"))
    if code != 0:
        raise ValueError("конфиг Xray не записан")
    _install_rotate(
        ssh,
        {
            "via": "host",
            "directory": "/opt/cascade/xray",
            "active": "access.log",
            "archive": "log-archive",
            "prefix": "access-",
            "retention_hours": retention_hours,
            "cap_bytes": cap_mb * 1024 * 1024,
        },
    )
    code, _out = _run(ssh, _root("systemctl restart cascade-xray"))
    if code != 0:
        raise ValueError("cascade-xray не перезапустился")
    state = "включён" if enable else "выключен"
    return [f"журнал Xray {state}: {XRAY_ACCESS_PATH}", "служба cascade-xray перезапущена"]


def _install_rotate(ssh, job: dict) -> None:
    code, current = _run(ssh, _root("cat /etc/cascade/log-rotate.json"))
    jobs: list[dict] = []
    if code == 0 and current.strip():
        try:
            loaded = json.loads(current)
            jobs = list(loaded.get("jobs") or [])
        except json.JSONDecodeError:
            jobs = []
    key = (job.get("via"), job.get("container"), job.get("directory"), job.get("active"))
    jobs = [
        item
        for item in jobs
        if (item.get("via"), item.get("container"), item.get("directory"), item.get("active")) != key
    ]
    jobs.append(job)
    service, timer = render_rotate_units()
    ssh.write_file("/tmp/cascade-log-rotate.sh", render_rotate_script().encode("utf-8"), 0o755)
    ssh.write_file("/tmp/cascade-log-rotate.service", service.encode("utf-8"), 0o644)
    ssh.write_file("/tmp/cascade-log-rotate.timer", timer.encode("utf-8"), 0o644)
    ssh.write_file(
        "/tmp/cascade-log-rotate.json",
        json.dumps({"jobs": jobs}, ensure_ascii=False, indent=2).encode("utf-8"),
        0o644,
    )
    code, _out = _run(
        ssh,
        _root("mkdir -p /opt/cascade/bin /etc/cascade")
        + " && "
        + _root("cp /tmp/cascade-log-rotate.sh /opt/cascade/bin/cascade-log-rotate.sh")
        + " && "
        + _root("chmod 755 /opt/cascade/bin/cascade-log-rotate.sh")
        + " && "
        + _root("cp /tmp/cascade-log-rotate.service /etc/systemd/system/cascade-log-rotate.service")
        + " && "
        + _root("cp /tmp/cascade-log-rotate.timer /etc/systemd/system/cascade-log-rotate.timer")
        + " && "
        + _root("cp /tmp/cascade-log-rotate.json /etc/cascade/log-rotate.json")
        + " && "
        + _root("systemctl daemon-reload")
        + " && "
        + _root("systemctl enable --now cascade-log-rotate.timer"),
    )
    if code != 0:
        raise ValueError("таймер ротации не включился")


def logging_drift(wanted: bool, status: str, mode: str) -> bool:
    """Контейнер пересоздали и запись снова выключена, хотя панель её включала."""
    if not wanted:
        return False
    if mode == "unbound":
        return status == "not_enabled"
    if mode == "xray":
        return status == "not_enabled"
    return False


def summary_line(name: str, status: str, cpu: float | None, peers_online: int, queries: int) -> str:
    cpu_text = f"{cpu:.0f}%" if cpu is not None else "нет CPU"
    return f"{name}: {status}, CPU {cpu_text}, клиентов онлайн {peers_online}, запросов DNS {queries}"


def now_unix() -> int:
    return int(time.time())
