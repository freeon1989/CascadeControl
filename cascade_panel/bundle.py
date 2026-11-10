"""Файлы, которые панель кладёт на узел. Секреты уже подставлены, в журнал не пишутся."""

from __future__ import annotations

import json
import shlex

from .settings import OWN_PREFIX

AWG_IMAGE = "amneziavpn/amneziawg-go:latest"
XRAY_VERSION = "v26.10.31"
TPROXY_PORT = 10808
SUB_PORT = 2096


def assert_own_container(name: str) -> str:
    if not name.startswith(OWN_PREFIX):
        raise ValueError(f"отказ удалять контейнер вне каскада: {name}")
    return name


def tproxy_nft(interface: str = "cawg0", port: int = TPROXY_PORT) -> str:
    return f"""table inet cascade
delete table inet cascade
table inet cascade {{
  chain prerouting {{
    type filter hook prerouting priority mangle; policy accept;
    iifname "{interface}" meta nfproto ipv6 drop
    iifname "{interface}" meta l4proto {{ tcp, udp }} tproxy ip to 127.0.0.1:{port} meta mark set 0x63
  }}
}}
"""


def awg_start_script() -> str:
    return """#!/bin/bash
# Запуск AmneziaWG 3.1 на интерфейсе cawg0 и перехват трафика клиентов в Xray.
set -euo pipefail
CONF=/opt/amnezia/awg/cawg0.conf
if [ ! -f "$CONF" ]; then
  echo "нет $CONF" >&2
  exit 1
fi
awg-quick down "$CONF" >/dev/null 2>&1 || true
sysctl -w net.ipv4.ip_forward=1 >/dev/null
sysctl -w net.ipv4.conf.all.route_localnet=1 >/dev/null
sysctl -w net.ipv4.conf.all.src_valid_mark=1 >/dev/null
awg-quick up "$CONF"
nft -f /opt/amnezia/awg/tproxy.nft
ip rule del fwmark 0x63 table 100 >/dev/null 2>&1 || true
ip rule add fwmark 0x63 table 100
ip route replace local 0.0.0.0/0 dev lo table 100
# Контейнер с --network host: процесс должен жить, правила уже на хосте.
tail -f /dev/null
"""


def xray_unit() -> str:
    return """[Unit]
Description=Cascade Control Xray
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=/opt/cascade/bin/xray run -config /opt/cascade/xray/config.json
Restart=always
RestartSec=2
LimitNOFILE=1048576
AmbientCapabilities=CAP_NET_ADMIN CAP_NET_BIND_SERVICE
CapabilityBoundingSet=CAP_NET_ADMIN CAP_NET_BIND_SERVICE
NoNewPrivileges=true
Environment=XRAY_LOCATION_ASSET=/opt/cascade/xray

[Install]
WantedBy=multi-user.target
"""


def agent_unit() -> str:
    return """[Unit]
Description=Cascade Control entry agent
After=network-online.target cascade-xray.service
Wants=network-online.target

[Service]
Type=simple
ExecStart=/usr/local/bin/cascade-agent -config /etc/cascade-agent/config.json
Restart=always
RestartSec=2
NoNewPrivileges=true

[Install]
WantedBy=multi-user.target
"""


def render_install_script(*, role: str, awg: bool, xray: bool, agent: bool) -> str:
    """Скрипт установки. Удаляет только контейнеры cascade-*."""
    awg_name = assert_own_container("cascade-awg")
    lines = [
        "#!/bin/bash",
        "set -u",
        "log() { echo \"CASCADE_LOG $*\"; }",
        "if [ \"$(id -u)\" -ne 0 ]; then SUDO=\"sudo -n\"; else SUDO=; fi",
        "log подготовка каталогов",
        "$SUDO mkdir -p /opt/cascade/awg /opt/cascade/xray /opt/cascade/bin /etc/cascade-agent /var/lib/cascade-agent",
        "$SUDO chown -R \"$USER\" /opt/cascade /etc/cascade-agent /var/lib/cascade-agent || true",
        "if command -v apt-get >/dev/null 2>&1; then",
        "  log пакеты Ubuntu/Debian через apt",
        "  $SUDO apt-get update -y || true",
        "  $SUDO apt-get install -y nftables curl ca-certificates unzip || true",
        "  if ! command -v docker >/dev/null 2>&1; then",
        "    log ставлю docker: пакет docker.io, если его нет то get.docker.com",
        "    if ! $SUDO apt-get install -y docker.io; then",
        "      log пакет docker.io не встал, пробую get.docker.com",
        "      if ! curl -fsSL https://get.docker.com | $SUDO sh; then",
        "        log get.docker.com не подошёл, повторно ставлю docker.io",
        "        $SUDO apt-get install -y docker.io || true",
        "      fi",
        "    fi",
        "  fi",
        "elif command -v dnf >/dev/null 2>&1; then",
        "  log пакеты Alma/Rocky/CentOS/Fedora через dnf",
        "  $SUDO dnf install -y nftables curl ca-certificates unzip || true",
        "  if command -v firewall-cmd >/dev/null 2>&1; then",
        "    log firewalld найден и не отключается, таблица nft inet cascade живёт рядом",
        "  fi",
        "  if ! command -v docker >/dev/null 2>&1; then",
        "    log ставлю docker: пакет dnf, если его нет то get.docker.com",
        "    if ! $SUDO dnf install -y docker docker-cli; then",
        "      if ! curl -fsSL https://get.docker.com | $SUDO sh; then",
        "        log get.docker.com не подошёл, пробую moby-engine и docker-ce",
        "        $SUDO dnf install -y moby-engine || $SUDO dnf install -y docker-ce docker-ce-cli containerd.io || true",
        "      fi",
        "    fi",
        "  fi",
        "else",
        "  log нет apt-get и dnf, пакеты нужно поставить вручную",
        "fi",
        "$SUDO systemctl enable --now docker >/dev/null 2>&1 || true",
    ]
    if awg:
        quoted = shlex.quote(awg_name)
        lines += [
            f"log обновляю контейнер {awg_name}",
            f"if $SUDO docker ps -a --format '{{{{.Names}}}}' | grep -qx {quoted}; then $SUDO docker rm -f {quoted}; fi",
            "log образ AmneziaWG",
            f"$SUDO docker pull {AWG_IMAGE}",
            "log запускаю AWG на хостовой сети, интерфейс cawg0",
            "$SUDO docker run -d --name "
            + awg_name
            + " --network host --restart always --privileged --cap-add NET_ADMIN --cap-add SYS_MODULE"
            + " --log-driver none -v /lib/modules:/lib/modules:ro"
            + " -v /opt/cascade:/opt/cascade"
            + " -v /opt/cascade/awg:/opt/amnezia/awg"
            + " -v /opt/cascade/awg/start.sh:/opt/amnezia/start.sh:ro "
            + AWG_IMAGE,
        ]
    if xray:
        lines += [
            "log бинарник Xray " + XRAY_VERSION,
            "tmp=$(mktemp -d)",
            "curl -fsSL -o \"$tmp/xray.zip\" "
            + f"https://github.com/XTLS/Xray-core/releases/download/{XRAY_VERSION}/Xray-linux-64.zip",
            "unzip -o \"$tmp/xray.zip\" -d \"$tmp/xray\" >/dev/null",
            "$SUDO install -m 0755 \"$tmp/xray/xray\" /opt/cascade/bin/xray",
            "rm -rf \"$tmp\"",
            "$SUDO cp /opt/cascade/xray/cascade-xray.service /etc/systemd/system/cascade-xray.service",
            "$SUDO systemctl daemon-reload",
            "$SUDO systemctl enable --now cascade-xray",
            "$SUDO systemctl restart cascade-xray",
            "log xray перезапущен",
        ]
    if agent and role == "entry":
        lines += [
            "if [ ! -f /opt/cascade/bin/cascade-agent ]; then",
            "  log агент ещё не загружен панелью",
            "else",
            "  $SUDO install -m 0755 /opt/cascade/bin/cascade-agent /usr/local/bin/cascade-agent",
            "  $SUDO cp /opt/cascade/agent/cascade-agent.service /etc/systemd/system/cascade-agent.service",
            "  $SUDO systemctl daemon-reload",
            "  $SUDO systemctl enable --now cascade-agent",
            "  $SUDO systemctl restart cascade-agent",
            "  log агент включён",
            "fi",
        ]
    lines += [
        "log проверка",
        "if command -v ss >/dev/null 2>&1; then ss -lntup | head -n 40 || true; fi",
        "log готово",
        "exit 0",
        "",
    ]
    script = "\n".join(lines)
    if "amnezia-" in script and "docker rm" in script:
        raise RuntimeError("скрипт установки собрался с удалением контейнера Amnezia")
    return script


def render_remove_script() -> str:
    name = assert_own_container("cascade-awg")
    return f"""#!/bin/bash
set -u
log() {{ echo "CASCADE_LOG $*"; }}
if [ "$(id -u)" -ne 0 ]; then SUDO="sudo -n"; else SUDO=; fi
log останавливаю только контейнеры cascade-*
if $SUDO docker ps -a --format '{{{{.Names}}}}' | grep -qx {shlex.quote(name)}; then
  $SUDO docker rm -f {shlex.quote(name)}
fi
$SUDO systemctl disable --now cascade-agent cascade-xray >/dev/null 2>&1 || true
$SUDO rm -f /etc/systemd/system/cascade-agent.service /etc/systemd/system/cascade-xray.service
$SUDO systemctl daemon-reload || true
$SUDO rm -rf /opt/cascade /etc/cascade-agent
$SUDO rm -f /usr/local/bin/cascade-agent
log удалены только контейнеры cascade-*
"""


def agent_document(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2)


def xray_document(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2)
