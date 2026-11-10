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
    iifname "{interface}" meta l4proto {{ tcp, udp }} meta mark set 0x63 tproxy ip to :{port} counter accept
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
if ! command -v nft >/dev/null 2>&1 && command -v apk >/dev/null 2>&1; then
  apk add --no-cache nftables
fi
awg-quick down "$CONF" >/dev/null 2>&1 || true
sysctl -w net.ipv4.ip_forward=1 >/dev/null
sysctl -w net.ipv4.conf.all.route_localnet=1 >/dev/null
# 1 заставляет ядро считать источник с fwmark локальным и отбрасывать пакет до INPUT.
sysctl -w net.ipv4.conf.all.src_valid_mark=0 >/dev/null
sysctl -w net.ipv4.conf.default.src_valid_mark=0 >/dev/null
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
Environment=XRAY_LOCATION_ASSET=/opt/cascade/bin

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


def render_install_script(*, role: str, awg: bool, xray: bool, agent: bool, xray_port: int = 443) -> str:
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
        "  if [ -f /etc/os-release ] && grep -q '^ID=ubuntu' /etc/os-release; then",
        "    $SUDO apt-get install -y software-properties-common || true",
        "    $SUDO add-apt-repository -y universe || true",
        "    $SUDO apt-get update -y || true",
        "  fi",
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
        lines += [
            "if ! command -v docker >/dev/null 2>&1; then",
            "  log пакет docker не встал, качаю статический бинарник",
            "  dtmp=$(mktemp -d)",
            "  if curl -fsSL -o \"$dtmp/docker.tgz\" https://download.docker.com/linux/static/stable/x86_64/docker-27.5.1.tgz; then",
            "    tar -xzf \"$dtmp/docker.tgz\" -C \"$dtmp\"",
            "    $SUDO install -m 0755 \"$dtmp/docker/docker\" \"$dtmp/docker/dockerd\" \"$dtmp/docker/containerd\" \"$dtmp/docker/containerd-shim-runc-v2\" \"$dtmp/docker/runc\" /usr/bin/",
            "    $SUDO tee /etc/systemd/system/docker.service >/dev/null << 'UNIT'",
            "[Unit]",
            "Description=Docker",
            "After=network-online.target",
            "[Service]",
            "ExecStart=/usr/bin/dockerd",
            "Restart=always",
            "[Install]",
            "WantedBy=multi-user.target",
            "UNIT",
            "    $SUDO systemctl daemon-reload",
            "    $SUDO systemctl enable --now docker",
            "  fi",
            "  rm -rf \"$dtmp\"",
            "fi",
            "if ! command -v docker >/dev/null 2>&1; then",
            "  log Docker не установился, AmneziaWG на входе без него не запустить",
            "  exit 1",
            "fi",
            "log docker готов",
        ]
    if awg:
        quoted = shlex.quote(awg_name)
        lines += [
            "log фиксирую src_valid_mark=0, иначе fwmark 0x63 роняет ответы клиенту",
            "$SUDO tee /etc/sysctl.d/99-cascade.conf >/dev/null << 'EOF'",
            "net.ipv4.ip_forward = 1",
            "net.ipv4.conf.all.route_localnet = 1",
            "net.ipv4.conf.all.src_valid_mark = 0",
            "net.ipv4.conf.default.src_valid_mark = 0",
            "EOF",
            "$SUDO sysctl --system >/dev/null 2>&1 || true",
            "$SUDO sysctl -w net.ipv4.conf.all.src_valid_mark=0 >/dev/null",
            "$SUDO sysctl -w net.ipv4.conf.default.src_valid_mark=0 >/dev/null",
            "if $SUDO modprobe amneziawg >/dev/null 2>&1 && command -v awg >/dev/null 2>&1 && command -v awg-quick >/dev/null 2>&1; then",
            "  log ядро умеет AmneziaWG, поднимаю cawg0 на хосте без контейнера",
            "  $SUDO bash -c 'set -e; CONF=/opt/cascade/awg/cawg0.conf; awg-quick down \"$CONF\" >/dev/null 2>&1 || true; sysctl -w net.ipv4.ip_forward=1 >/dev/null; sysctl -w net.ipv4.conf.all.route_localnet=1 >/dev/null; sysctl -w net.ipv4.conf.all.src_valid_mark=0 >/dev/null; sysctl -w net.ipv4.conf.default.src_valid_mark=0 >/dev/null; awg-quick up \"$CONF\"; nft -f /opt/cascade/awg/tproxy.nft; ip rule del fwmark 0x63 table 100 >/dev/null 2>&1 || true; ip rule add fwmark 0x63 table 100; ip route replace local 0.0.0.0/0 dev lo table 100'",
            "else",
            f"  log модуль ядра не найден, контейнер {awg_name}",
            f"  if $SUDO docker ps -a --format '{{{{.Names}}}}' | grep -qx {quoted}; then $SUDO docker rm -f {quoted}; fi",
            "  log образ AmneziaWG",
            f"  $SUDO docker pull {AWG_IMAGE}",
            "  log запускаю AWG на хостовой сети, интерфейс cawg0",
            "  $SUDO docker run -d --name "
            + awg_name
            + " --network host --restart always --privileged --cap-add NET_ADMIN --cap-add SYS_MODULE"
            + " --log-driver local --log-opt max-size=10m --log-opt max-file=3"
            + " -v /lib/modules:/lib/modules:ro"
            + " -v /opt/cascade:/opt/cascade"
            + " -v /opt/cascade/awg:/opt/amnezia/awg"
            + " -v /opt/cascade/awg/start.sh:/opt/amnezia/start.sh:ro"
            + " --entrypoint bash "
            + AWG_IMAGE
            + " /opt/amnezia/start.sh",
            "fi",
        ]
    if xray:
        lines += [
            "log бинарник Xray " + XRAY_VERSION,
            "tmp=$(mktemp -d)",
            "curl -fsSL -o \"$tmp/xray.zip\" "
            + f"https://github.com/XTLS/Xray-core/releases/download/{XRAY_VERSION}/Xray-linux-64.zip",
            "unzip -o \"$tmp/xray.zip\" -d \"$tmp/xray\" >/dev/null",
            "$SUDO install -m 0755 \"$tmp/xray/xray\" /opt/cascade/bin/xray",
            "$SUDO install -m 0644 \"$tmp/xray/geoip.dat\" \"$tmp/xray/geosite.dat\" /opt/cascade/bin/",
            "rm -rf \"$tmp\"",
            "$SUDO cp /opt/cascade/xray/cascade-xray.service /etc/systemd/system/cascade-xray.service",
            "$SUDO systemctl daemon-reload",
            "log проверяю конфиг Xray",
            "if ! $SUDO env XRAY_LOCATION_ASSET=/opt/cascade/bin /opt/cascade/bin/xray run -test -config /opt/cascade/xray/config.json; then",
            "  log конфиг Xray не прошёл проверку",
            "  $SUDO journalctl -u cascade-xray -n 50 --no-pager || true",
            "  exit 1",
            "fi",
            "$SUDO systemctl enable cascade-xray",
            "$SUDO systemctl restart cascade-xray",
            "sleep 1",
            "if ! $SUDO systemctl is-active --quiet cascade-xray; then",
            "  log служба cascade-xray не активна",
            "  $SUDO journalctl -u cascade-xray -n 50 --no-pager || true",
            "  exit 1",
            "fi",
            f"if command -v ss >/dev/null 2>&1 && ! ss -lntH | grep -Eq '[:.]{int(xray_port)}([^0-9]|$)'; then",
            f"  log порт {int(xray_port)} не слушается",
            "  $SUDO journalctl -u cascade-xray -n 50 --no-pager || true",
            "  exit 1",
            "fi",
            f"log xray слушает {int(xray_port)}",
        ]
    if agent and role == "entry":
        lines += [
            "if [ ! -f /opt/cascade/bin/cascade-agent ]; then",
            "  log агент ещё не загружен панелью",
            "  exit 1",
            "fi",
            "if [ ! -f /opt/cascade/agent/cascade-agent.service ]; then",
            "  log нет файла службы агента",
            "  exit 1",
            "fi",
            "$SUDO install -m 0755 /opt/cascade/bin/cascade-agent /usr/local/bin/cascade-agent",
            "$SUDO cp /opt/cascade/agent/cascade-agent.service /etc/systemd/system/cascade-agent.service",
            "$SUDO systemctl daemon-reload",
            "$SUDO systemctl enable cascade-agent",
            "$SUDO systemctl restart cascade-agent",
            "sleep 1",
            "state=$($SUDO systemctl is-active cascade-agent || true)",
            "log агент: $state",
            "if [ \"$state\" != active ]; then",
            "  $SUDO systemctl status cascade-agent --no-pager || true",
            "  $SUDO journalctl -u cascade-agent -n 50 --no-pager || true",
            "  exit 1",
            "fi",
        ]
    if awg and xray:
        lines += [
            "log самопроверка пересылки: sysctl, порядок nft и проба через cawg0",
            "mark=$($SUDO sysctl -n net.ipv4.conf.all.src_valid_mark || echo missing)",
            "if [ \"$mark\" != 0 ]; then",
            "  log src_valid_mark=$mark, ядро отбросит пакеты клиента до Xray",
            "  exit 1",
            "fi",
            "chain=$($SUDO nft list chain inet cascade prerouting 2>/dev/null || true)",
            "if ! printf '%s\\n' \"$chain\" | grep -Eq 'meta mark set 0x0*63 tproxy ip to :10808'; then",
            "  log в nft нет метки 0x63 перед tproxy на :10808",
            "  printf '%s\\n' \"$chain\"",
            "  exit 1",
            "fi",
            "if ! ss -lntH | grep -Eq '[:.]10808([^0-9]|$)'; then",
            "  log Xray не слушает 0.0.0.0:10808",
            "  $SUDO journalctl -u cascade-xray -n 50 --no-pager || true",
            "  exit 1",
            "fi",
            "if ! command -v awg >/dev/null 2>&1 || ! $SUDO ip link show cawg0 >/dev/null 2>&1; then",
            "  log проба из netns пропущена: на хосте нет awg или cawg0 ещё не поднялся",
            "else",
            "  ns=cascade-fwtest",
            "  $SUDO ip netns del \"$ns\" >/dev/null 2>&1 || true",
            "  $SUDO ip link del veth-cascade >/dev/null 2>&1 || true",
            "  priv=$($SUDO awg genkey)",
            "  pub=$(printf '%s\\n' \"$priv\" | $SUDO awg pubkey)",
            "  $SUDO awg set cawg0 peer \"$pub\" allowed-ips 10.10.1.250/32",
            "  sport=$($SUDO awg show cawg0 listen-port)",
            "  spub=$($SUDO awg show cawg0 public-key)",
            "  work=$(mktemp -d)",
            "  {",
            "    echo \"[Interface]\"",
            "    echo \"PrivateKey = $priv\"",
            "    echo \"Address = 10.10.1.250/32\"",
            "    grep -E '^(Jc|Jmin|Jmax|S1|S2|S3|S4|H1|H2|H3|H4|HeaderProtectionKey) =' /opt/cascade/awg/cawg0.conf || true",
            "    echo \"[Peer]\"",
            "    echo \"PublicKey = $spub\"",
            "    echo \"AllowedIPs = 0.0.0.0/0, ::/0\"",
            "    echo \"Endpoint = 10.255.255.1:${sport}\"",
            "    echo \"PersistentKeepalive = 25\"",
            "  } > \"$work/client.conf\"",
            "  $SUDO ip link add veth-cascade type veth peer name veth-ns",
            "  $SUDO ip addr add 10.255.255.1/30 dev veth-cascade",
            "  $SUDO ip link set veth-cascade up",
            "  $SUDO ip netns add \"$ns\"",
            "  $SUDO ip link set veth-ns netns \"$ns\"",
            "  $SUDO ip netns exec \"$ns\" ip addr add 10.255.255.2/30 dev veth-ns",
            "  $SUDO ip netns exec \"$ns\" ip link set veth-ns up",
            "  $SUDO ip netns exec \"$ns\" ip link set lo up",
            "  $SUDO ip netns exec \"$ns\" ip route add default via 10.255.255.1 || true",
            "  probe=fail",
            "  if $SUDO ip netns exec \"$ns\" awg-quick up \"$work/client.conf\"; then",
            "    if $SUDO ip netns exec \"$ns\" timeout 12 bash -c 'echo >/dev/tcp/1.1.1.1/443'; then probe=ok; fi",
            "    $SUDO ip netns exec \"$ns\" awg-quick down \"$work/client.conf\" || true",
            "  fi",
            "  $SUDO nft list chain inet cascade prerouting || true",
            "  $SUDO awg set cawg0 peer \"$pub\" remove || true",
            "  $SUDO ip netns del \"$ns\" >/dev/null 2>&1 || true",
            "  $SUDO ip link del veth-cascade >/dev/null 2>&1 || true",
            "  rm -rf \"$work\"",
            "  if [ \"$probe\" != ok ]; then",
            "    log проба из netns через cawg0 до 1.1.1.1:443 не прошла",
            "    $SUDO journalctl -u cascade-xray -n 30 --no-pager || true",
            "    exit 1",
            "  fi",
            "  log проба через cawg0 дошла до сети, счётчик tproxy смотри выше",
            "fi",
            "log пересылка с туннеля в порядке",
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


def render_awg_sync_script() -> str:
    """Подменяет пиры без awg-quick down, чтобы остальные клиенты не отвалились."""
    name = assert_own_container("cascade-awg")
    quoted = shlex.quote(name)
    return f"""#!/bin/bash
set -u
log() {{ echo "CASCADE_LOG $*"; }}
if [ "$(id -u)" -ne 0 ]; then SUDO="sudo -n"; else SUDO=; fi
if $SUDO ip link show cawg0 >/dev/null 2>&1 && command -v awg >/dev/null 2>&1 && ! $SUDO docker ps --format '{{{{.Names}}}}' | grep -qx {quoted}; then
  $SUDO bash -c 'awg syncconf cawg0 <(awg-quick strip /opt/cascade/awg/cawg0.conf)'
  log пиры применены на хосте, интерфейс не перезапускался
  exit 0
fi
if $SUDO docker ps --format '{{{{.Names}}}}' | grep -qx {quoted}; then
  $SUDO docker exec {quoted} bash -c 'awg syncconf cawg0 <(awg-quick strip /opt/amnezia/awg/cawg0.conf)'
  log пиры применены в контейнере, интерфейс не перезапускался
  exit 0
fi
log интерфейс cawg0 ещё не поднят, конфиг только записан на диск
exit 0
"""


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
