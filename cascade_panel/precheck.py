"""Разбор отчёта предварительной проверки сервера.

Скрипт на узле печатает одну строку ``CASCADE_JSON {...}``. Остальной вывод
попадает в живой журнал и не мешает разбору.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field

from .settings import (
    CENTOS_STREAM_KNOWN,
    DEBIAN_KNOWN,
    EL_KNOWN,
    FEDORA_KNOWN,
    KERNEL_WARN,
    MIN_DISK_GB,
    MIN_KERNEL,
    UBUNTU_KNOWN,
    WARN_DISK_GB,
)

OS_SUPPORT_HINT = (
    "Поддерживаются Ubuntu 20.04, 22.04, 24.04, 25.x и 26.04; "
    "Debian 11, 12 и 13; AlmaLinux 8 и 9, Rocky Linux 8 и 9, CentOS Stream 9, Fedora 40–45. "
    "Незнакомая или более новая версия этих семейств не блокирует установку. "
    "Отказ по системе только если это не Linux."
)

_JSON_RE = re.compile(r"CASCADE_JSON\s*(\{.*\})")

VIRT_REJECT = {"openvz", "lxc", "lxd", "docker", "container", "podman", "systemd-nspawn", "rkt", "wsl"}
VIRT_OK = {
    "kvm",
    "qemu",
    "none",
    "microsoft",
    "vmware",
    "xen",
    "bochs",
    "oracle",
    "amazon",
    "aws",
    "google",
    "azure",
    "parallels",
    "bhyve",
    "uml",
    "powervm",
    "vm-other",
    "hyperv",
}


@dataclass
class Check:
    id: str
    title: str
    ok: bool
    level: str  # ok | warn | fail
    detail: str
    hint: str = ""


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
        return asdict(self)


def parse_precheck_output(text: str) -> dict:
    match = None
    for line in text.splitlines():
        found = _JSON_RE.search(line.strip())
        if found:
            match = found
    if not match:
        raise ValueError("в выводе проверки нет строки CASCADE_JSON")
    return json.loads(match.group(1))


def _ubuntu_release(version: str) -> tuple[int, int] | None:
    bits = [piece for piece in version.split(".") if piece != ""]
    if not bits or not bits[0].isdigit():
        return None
    minor = int(bits[1]) if len(bits) > 1 and bits[1].isdigit() else 0
    return int(bits[0]), minor


def _major(version: str) -> int | None:
    head = version.split(".")[0]
    return int(head) if head.isdigit() else None


def _family(os_id: str, os_like: str) -> str:
    os_id = os_id.strip().lower()
    like = set(os_like.lower().replace(",", " ").split())
    if os_id == "ubuntu" or "ubuntu" in like:
        return "ubuntu"
    if os_id == "debian":
        return "debian"
    if os_id in {"almalinux", "rocky", "rhel", "centos", "fedora"}:
        return os_id
    if "rhel" in like or "fedora" in like or "centos" in like:
        return "rhel-like"
    if "debian" in like:
        return "debian-like"
    return ""


def _os_verdict(os_id: str, os_ver: str, os_like: str = "", kernel_name: str = "") -> str:
    """ok, warn или fail. Блокирует только не-Linux. Чужая версия известного семейства — предупреждение."""
    if kernel_name and kernel_name.strip().lower() not in {"linux", ""}:
        return "fail"
    family = _family(os_id, os_like)
    if family == "ubuntu":
        release = _ubuntu_release(os_ver)
        if release is None:
            return "warn"
        if release[0] == 25 or release in UBUNTU_KNOWN:
            return "ok"
        return "warn"
    if family == "debian":
        major = _major(os_ver)
        if major is None:
            return "warn"
        return "ok" if major in DEBIAN_KNOWN else "warn"
    if family in {"almalinux", "rocky", "rhel"}:
        major = _major(os_ver)
        if major is None:
            return "warn"
        return "ok" if major in EL_KNOWN else "warn"
    if family == "centos":
        major = _major(os_ver)
        if major is None:
            return "warn"
        return "ok" if major in CENTOS_STREAM_KNOWN else "warn"
    if family == "fedora":
        major = _major(os_ver)
        if major is None:
            return "warn"
        return "ok" if major in FEDORA_KNOWN else "warn"
    if family in {"rhel-like", "debian-like"}:
        return "warn"
    return "warn"


def _kernel_tuple(version: str) -> tuple[int, int]:
    parts = re.findall(r"\d+", version)
    if len(parts) < 2:
        return (0, 0)
    return int(parts[0]), int(parts[1])


def _virt_name(raw: dict) -> str:
    text = str(raw.get("virt") or "unknown").replace("\r", "").strip().lower()
    first = text.splitlines()[0].strip() if text else "unknown"
    return first or "unknown"


def evaluate(raw: dict) -> PrecheckReport:
    checks: list[Check] = []
    virt = _virt_name(raw)
    tun = raw.get("tun")
    tun_ok = tun is True
    if virt in {"openvz", "lxc", "lxd"} and not tun_ok:
        checks.append(
            Check(
                "virt",
                "Виртуализация",
                False,
                "fail",
                f"{virt}: нет /dev/net/tun",
                "OpenVZ и LXC без tun не подходят для AmneziaWG. Нужен KVM или контейнер, в котором есть /dev/net/tun.",
            )
        )
    elif virt in {"openvz", "lxc", "lxd"}:
        checks.append(
            Check(
                "virt",
                "Виртуализация",
                True,
                "warn",
                f"{virt}: tun есть, установка не блокируется",
                "Предпочтительнее обычный KVM. Контейнер с /dev/net/tun не останавливает установку.",
            )
        )
    elif virt in VIRT_REJECT:
        checks.append(
            Check(
                "virt",
                "Виртуализация",
                True,
                "warn",
                f"{virt}: похоже на контейнер, установка не блокируется",
                "Если это хост, а не контейнер, выполните systemd-detect-virt. Для AmneziaWG нужен /dev/net/tun.",
            )
        )
    elif virt in VIRT_OK:
        label = "железо" if virt == "none" else virt.upper()
        checks.append(Check("virt", "Виртуализация", True, "ok", label))
    else:
        checks.append(
            Check(
                "virt",
                "Виртуализация",
                True,
                "warn",
                f"{virt}: тип не из списка, установка не блокируется",
                "На сервере выполните systemd-detect-virt. Если это не контейнер без tun, можно продолжать.",
            )
        )

    os_id = str(raw.get("os_id") or "").lower()
    os_ver = str(raw.get("os_version") or "")
    os_like = str(raw.get("os_like") or "")
    kernel_name = str(raw.get("kernel_name") or "")
    pretty = str(raw.get("os_pretty") or f"{os_id} {os_ver}").strip()
    os_level = _os_verdict(os_id, os_ver, os_like, kernel_name)
    if os_level == "ok":
        os_detail = pretty
    elif os_level == "warn":
        os_detail = f"{pretty or 'версия не распознана'}: версия не из основного списка, установка не блокируется"
    else:
        os_detail = f"{pretty or kernel_name or 'не Linux'}: панель ставит каскад только на Linux"
    checks.append(Check("os", "Система", os_level != "fail", os_level, os_detail, OS_SUPPORT_HINT))

    kernel = str(raw.get("kernel") or "")
    major = _kernel_tuple(kernel)
    if major >= MIN_KERNEL:
        kernel_level, kernel_detail, kernel_hint = "ok", kernel, ""
    elif major >= KERNEL_WARN:
        kernel_level = "warn"
        kernel_detail = f"{kernel}: старше {MIN_KERNEL[0]}.{MIN_KERNEL[1]}, для Alma/Rocky 8 этого достаточно"
        kernel_hint = "Ядро 4.18 и новее установку не блокирует. AmneziaWG здесь работает в userspace."
    else:
        kernel_level = "fail"
        kernel_detail = f"{kernel or 'неизвестно'}: нужно хотя бы {KERNEL_WARN[0]}.{KERNEL_WARN[1]}"
        kernel_hint = "Команда: uname -r."
    checks.append(Check("kernel", "Ядро", kernel_level != "fail", kernel_level, kernel_detail, kernel_hint))

    port_free = bool(raw.get("port_443_tcp_free", True))
    checks.append(
        Check(
            "port443",
            "Порт 443",
            True,
            "ok" if port_free else "warn",
            "свободен" if port_free else "занят, будет выбран другой порт",
            "" if port_free else "Занятый 443 установку не блокирует. Часто его занимает другой сервис. Каскад возьмёт свободный порт.",
        )
    )

    docker_ok = bool(raw.get("docker"))
    checks.append(
        Check(
            "docker",
            "Docker",
            True,
            "ok" if docker_ok else "warn",
            "установлен, контейнеры Amnezia не трогаем" if docker_ok else "не найден, будет установлен из репозитория ОС",
            "" if docker_ok else "Если docker уже стоит, а проверка его не видит, войдите как root. Контейнеры amnezia-* панель не удаляет.",
        )
    )

    if "uid" in raw or "sudo_nopass" in raw:
        uid = raw.get("uid")
        sudo_ok = bool(raw.get("sudo_nopass"))
        if uid == 0:
            checks.append(Check("sudo", "Права", True, "ok", "вход под root, sudo не требуется"))
        elif sudo_ok:
            checks.append(Check("sudo", "Права", True, "ok", "sudo без пароля работает"))
        else:
            checks.append(
                Check(
                    "sudo",
                    "Права",
                    True,
                    "warn",
                    "пользователь не root и sudo без пароля не работает",
                    "Укажите пользователя root или выдайте sudo без пароля. Иначе запись файлов остановится на запросе пароля.",
                )
            )

    disk_raw = raw.get("disk_free_gb")
    try:
        disk = float(disk_raw)
        disk_known = True
    except (TypeError, ValueError):
        disk = 0.0
        disk_known = False
    if not disk_known:
        disk_ok, disk_level = True, "warn"
        disk_detail = "не удалось прочитать свободное место"
        disk_hint = "На сервере: df -h /. Это предупреждение, установку не блокирует."
    elif disk < MIN_DISK_GB:
        disk_ok, disk_level = False, "fail"
        disk_detail = f"{disk:.1f} ГБ свободно, минимум {MIN_DISK_GB:.0f} ГБ"
        disk_hint = "Освободите место на /. Минимум 2 ГБ, лучше от 10 ГБ."
    elif disk < WARN_DISK_GB:
        disk_ok, disk_level = True, "warn"
        disk_detail = f"{disk:.1f} ГБ свободно, рекомендуется от {WARN_DISK_GB:.0f} ГБ"
        disk_hint = ""
    else:
        disk_ok, disk_level = True, "ok"
        disk_detail = f"{disk:.1f} ГБ свободно"
        disk_hint = ""
    checks.append(Check("disk", "Диск", disk_ok, disk_level, disk_detail, disk_hint))

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
                "Панель создаёт только cascade-*. Уже стоящий AmneziaVPN в Docker остаётся.",
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
if ! command -v python3 >/dev/null 2>&1; then
  printf '%s\n' 'CASCADE_JSON {"virt":"unknown","os_id":"","os_version":"","os_pretty":"нет python3","kernel":"","disk_free_gb":null,"docker":false,"port_443_tcp_free":true,"used_ports":[],"containers":[],"uid":null,"sudo_nopass":false}'
  exit 0
fi
exec python3 - << 'PY'
import json
import os
import re
import subprocess

def run(cmd, timeout=12):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return None

def virt():
    # /proc/vz бывает и на обычном ядре. OpenVZ считаем только по veinfo.
    if os.path.exists("/proc/vz/veinfo") and not os.path.isdir("/proc/bc"):
        return "openvz"
    try:
        env = open("/proc/1/environ", "rb").read().split(b"\0")
    except OSError:
        env = []
    for item in env:
        if item.startswith(b"container="):
            name = item.split(b"=", 1)[1].decode("utf-8", "replace").strip().lower()
            if name and name != "none":
                return name
    proc = run(["systemd-detect-virt"])
    # Код 1 и текст none означают «не контейнер». Нельзя подменять это на unknown.
    if proc is not None and proc.stdout.strip():
        return proc.stdout.replace("\r", "").strip().splitlines()[0].strip()
    return "none"

def os_release():
    info = {}
    try:
        text = open("/etc/os-release", encoding="utf-8", errors="replace").read()
    except OSError:
        return info
    for line in text.splitlines():
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        info[key.strip()] = value.strip().strip('"').strip("'")
    return info

def disk_gb():
    try:
        st = os.statvfs("/")
        return round(st.f_bavail * st.f_frsize / (1024 ** 3), 1)
    except OSError:
        return None

def command_ok(cmd):
    proc = run(cmd)
    return bool(proc and proc.returncode == 0)

uid = os.geteuid()
sudo_nopass = uid == 0 or command_ok(["sudo", "-n", "true"])
docker_direct = command_ok(["docker", "info"])
docker = docker_direct or (uid != 0 and sudo_nopass and command_ok(["sudo", "-n", "docker", "info"]))
docker_prefix = ["docker"] if docker_direct or uid == 0 else ["sudo", "-n", "docker"]

containers = []
if docker:
    proc = run(docker_prefix + ["ps", "-a", "--format", "{{.Names}}\t{{.Image}}\t{{.Ports}}\t{{.Status}}"], timeout=20)
    if proc and proc.returncode == 0:
        for line in proc.stdout.splitlines():
            parts = line.split("\t")
            while len(parts) < 4:
                parts.append("")
            if parts[0]:
                containers.append({
                    "name": parts[0],
                    "image": parts[1],
                    "ports": parts[2],
                    "status": parts[3],
                })

used = []
port_free = True
proc = run(["ss", "-lntuH"])
if proc and proc.returncode == 0:
    for line in proc.stdout.splitlines():
        bits = line.split()
        if len(bits) < 5:
            continue
        local = bits[4]
        match = re.search(r":(\d+)$", local)
        if match:
            port = int(match.group(1))
            if port not in used:
                used.append(port)
        if bits[0].startswith("tcp") and re.search(r"[:\]]443$", local):
            port_free = False

info = os_release()
kernel_proc = run(["uname", "-r"])
kernel = kernel_proc.stdout.strip() if kernel_proc and kernel_proc.stdout else ""
uname_s = run(["uname", "-s"])
kernel_name = uname_s.stdout.strip() if uname_s and uname_s.stdout else ""
payload = {
    "virt": virt(),
    "tun": os.path.exists("/dev/net/tun"),
    "os_id": info.get("ID", ""),
    "os_like": info.get("ID_LIKE", ""),
    "os_version": info.get("VERSION_ID", ""),
    "os_pretty": info.get("PRETTY_NAME", ""),
    "kernel": kernel,
    "kernel_name": kernel_name,
    "disk_free_gb": disk_gb(),
    "docker": docker,
    "port_443_tcp_free": port_free,
    "used_ports": used,
    "containers": containers,
    "uid": uid,
    "sudo_nopass": bool(sudo_nopass),
}
print("CASCADE_JSON " + json.dumps(payload, ensure_ascii=False))
PY
"""
