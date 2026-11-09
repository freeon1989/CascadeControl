"""Разбор отчёта предварительной проверки сервера.

Скрипт на узле печатает одну строку ``CASCADE_JSON {...}``. Остальной вывод
попадает в живой журнал и не мешает разбору.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field

from .settings import ALLOWED_OS, MIN_DISK_GB, MIN_KERNEL, WARN_DISK_GB

_JSON_RE = re.compile(r"CASCADE_JSON\s*(\{.*\})")

VIRT_REJECT = {"openvz", "lxc", "lxd", "docker", "container", "podman"}
VIRT_OK = {"kvm", "qemu", "none", "microsoft", "vmware", "xen", "bochs"}


@dataclass
class Check:
    id: str
    title: str
    ok: bool
    level: str  # ok | warn | fail
    detail: str


@dataclass
class PrecheckReport:
    ok: bool
    checks: list[Check]
    virt: str = ""
    os_name: str = ""
    os_version: str = ""
    kernel: str = ""
    disk_free_gb: float = 0
    docker_installed: bool = False
    used_ports: list[int] = field(default_factory=list)
    containers: list[dict] = field(default_factory=list)
    protected: list[str] = field(default_factory=list)
    raw: dict = field(default_factory=dict)

    def to_json(self) -> dict:
        data = asdict(self)
        return data


def parse_precheck_output(text: str) -> dict:
    match = None
    for line in text.splitlines():
        found = _JSON_RE.search(line.strip())
        if found:
            match = found
    if not match:
        raise ValueError("в выводе проверки нет строки CASCADE_JSON")
    return json.loads(match.group(1))


def _kernel_tuple(version: str) -> tuple[int, int]:
    parts = re.findall(r"\d+", version)
    if len(parts) < 2:
        return (0, 0)
    return int(parts[0]), int(parts[1])


def evaluate(raw: dict) -> PrecheckReport:
    checks: list[Check] = []
    virt = str(raw.get("virt") or "unknown").lower()
    if virt in VIRT_REJECT:
        checks.append(Check("virt", "Виртуализация", False, "fail", f"{virt}: нужен KVM, не OpenVZ/LXC"))
    elif virt in VIRT_OK:
        label = "железо" if virt == "none" else virt.upper()
        checks.append(Check("virt", "Виртуализация", True, "ok", label))
    else:
        checks.append(Check("virt", "Виртуализация", False, "fail", f"неизвестный тип: {virt}"))

    os_id = str(raw.get("os_id") or "").lower()
    os_ver = str(raw.get("os_version") or "")
    # Debian 12.5 -> 12, Ubuntu 24.04.1 -> 24.04
    os_key = os_ver
    if os_id == "debian":
        os_key = os_ver.split(".")[0]
    elif os_id == "ubuntu":
        bits = os_ver.split(".")
        os_key = ".".join(bits[:2]) if len(bits) >= 2 else os_ver
    os_ok = (os_id, os_key) in ALLOWED_OS
    pretty = str(raw.get("os_pretty") or f"{os_id} {os_ver}")
    checks.append(
        Check(
            "os",
            "Система",
            os_ok,
            "ok" if os_ok else "fail",
            pretty if os_ok else f"{pretty}: нужна Ubuntu 22.04/24.04 или Debian 12",
        )
    )

    kernel = str(raw.get("kernel") or "")
    major = _kernel_tuple(kernel)
    kernel_ok = major >= MIN_KERNEL
    checks.append(
        Check(
            "kernel",
            "Ядро",
            kernel_ok,
            "ok" if kernel_ok else "fail",
            kernel if kernel_ok else f"{kernel}: нужно >= {MIN_KERNEL[0]}.{MIN_KERNEL[1]}",
        )
    )

    port_free = bool(raw.get("port_443_tcp_free", False))
    checks.append(
        Check(
            "port443",
            "Порт 443",
            port_free,
            "ok" if port_free else "warn",
            "свободен" if port_free else "занят, будет выбран другой порт",
        )
    )

    docker_ok = bool(raw.get("docker"))
    checks.append(
        Check(
            "docker",
            "Docker",
            docker_ok,
            "ok" if docker_ok else "warn",
            "установлен" if docker_ok else "не найден, будет установлен из репозитория ОС",
        )
    )

    disk = float(raw.get("disk_free_gb") or 0)
    if disk < MIN_DISK_GB:
        disk_level, disk_ok = "fail", False
        disk_detail = f"{disk:.1f} ГБ свободно, минимум {MIN_DISK_GB:.0f} ГБ"
    elif disk < WARN_DISK_GB:
        disk_level, disk_ok = "warn", True
        disk_detail = f"{disk:.1f} ГБ свободно, рекомендуется от {WARN_DISK_GB:.0f} ГБ"
    else:
        disk_level, disk_ok = "ok", True
        disk_detail = f"{disk:.1f} ГБ свободно"
    checks.append(Check("disk", "Диск", disk_ok, disk_level, disk_detail))

    containers = list(raw.get("containers") or [])
    protected = [c.get("name", "") for c in containers if str(c.get("name", "")).startswith("amnezia-")]
    if protected:
        checks.append(
            Check(
                "amnezia",
                "Контейнеры Amnezia",
                True,
                "ok",
                "не трогаем: " + ", ".join(protected),
            )
        )

    failed = any(c.level == "fail" for c in checks)
    return PrecheckReport(
        ok=not failed,
        checks=checks,
        virt=virt,
        os_name=os_id,
        os_version=os_ver,
        kernel=kernel,
        disk_free_gb=disk,
        docker_installed=docker_ok,
        used_ports=[int(p) for p in raw.get("used_ports") or []],
        containers=containers,
        protected=protected,
        raw=raw,
    )


PRECHECK_SCRIPT = r"""#!/bin/bash
# Предпроверка Cascade Control. Ничего не устанавливает и не удаляет.
set -u
virt="unknown"
if [ -d /proc/vz ] && [ ! -d /proc/bc ]; then
  virt="openvz"
elif grep -qa container=lxc /proc/1/environ 2>/dev/null; then
  virt="lxc"
elif command -v systemd-detect-virt >/dev/null 2>&1; then
  virt=$(systemd-detect-virt 2>/dev/null || echo unknown)
fi
os_id="unknown"
os_version=""
os_pretty=""
if [ -f /etc/os-release ]; then
  # shellcheck disable=SC1091
  . /etc/os-release
  os_id="${ID:-unknown}"
  os_version="${VERSION_ID:-}"
  os_pretty="${PRETTY_NAME:-}"
fi
kernel=$(uname -r)
disk_free_gb=$(df -BG --output=avail / | tail -1 | tr -dc '0-9.')
if [ -z "$disk_free_gb" ]; then disk_free_gb=0; fi
docker=false
if command -v docker >/dev/null 2>&1 && sudo docker info >/dev/null 2>&1; then
  docker=true
fi
used=$(ss -lntuH 2>/dev/null | awk '{print $5}' | sed -E 's/.*:([0-9]+)$/\1/' | sort -n | uniq | tr '\n' ' ')
port_443_tcp_free=true
if ss -lntH 2>/dev/null | awk '{print $4}' | grep -Eq '(^|:|\])443$'; then
  port_443_tcp_free=false
fi
containers="[]"
if [ "$docker" = true ]; then
  containers="["
  first=1
  while IFS=$'\t' read -r name image ports status; do
    [ -z "$name" ] && continue
    esc() { printf '%s' "$1" | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g'; }
    if [ "$first" -eq 0 ]; then containers+=","; fi
    first=0
    containers+=$(printf '{"name":"%s","image":"%s","ports":"%s","status":"%s"}' \
      "$(esc "$name")" "$(esc "$image")" "$(esc "$ports")" "$(esc "$status")")
  done < <(sudo docker ps -a --format '{{.Names}}\t{{.Image}}\t{{.Ports}}\t{{.Status}}')
  containers+="]"
fi
python3 - "$virt" "$os_id" "$os_version" "$os_pretty" "$kernel" "$disk_free_gb" "$docker" "$port_443_tcp_free" "$used" "$containers" <<'PY'
import json, sys
virt, os_id, os_version, os_pretty, kernel, disk, docker, port_free, used, containers = sys.argv[1:]
ports=[]
for item in used.split():
    if item.isdigit():
        ports.append(int(item))
try:
    cont=json.loads(containers)
except Exception:
    cont=[]
payload={
  "virt": virt,
  "os_id": os_id,
  "os_version": os_version,
  "os_pretty": os_pretty,
  "kernel": kernel,
  "disk_free_gb": float(disk or 0),
  "docker": docker == "true",
  "port_443_tcp_free": port_free == "true",
  "used_ports": ports,
  "containers": cont,
}
print("CASCADE_JSON " + json.dumps(payload, ensure_ascii=False))
PY
"""
