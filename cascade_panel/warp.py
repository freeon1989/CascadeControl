"""Cloudflare WARP на выходном узле: скрипты включения и отката, разбор статуса."""

from __future__ import annotations

import ipaddress
import json
import re
import shlex
from urllib.error import URLError
from urllib.request import Request, urlopen

WARP_IFACE = "CloudflareWARP"
TRACE_URL = "https://www.cloudflare.com/cdn-cgi/trace"
TRACE_FALLBACK_URL = "https://1.1.1.1/cdn-cgi/trace"
APT_REPO = "https://pkg.cloudflareclient.com/"
APT_KEY_URL = "https://pkg.cloudflareclient.com/pubkey.gpg"
FALLBACK_CODENAME = "noble"
# Частные сети не гоняем через WARP: иначе ломаются docker, AWG и связь между узлами.
PRIVATE_EXCLUDES = (
    "10.0.0.0/8",
    "172.16.0.0/12",
    "192.168.0.0/16",
    "169.254.0.0/16",
    "fc00::/7",
    "fe80::/10",
)

_IP_LINE = re.compile(r"^ip=(.+)$", re.MULTILINE)
_WARP_LINE = re.compile(r"^warp=(.+)$", re.MULTILINE)


class WarpError(RuntimeError):
    pass


def normalize_ip(value: str) -> str | None:
    text = (value or "").strip()
    if not text:
        return None
    if "/" in text:
        return None
    if text.startswith("[") and text.endswith("]"):
        text = text[1:-1]
    try:
        return str(ipaddress.ip_address(text))
    except ValueError:
        return None


def resolve_host_ips(host: str) -> list[str]:
    """Возвращает адреса узла. Имя в список не попадает — warp-cli ждёт IP."""
    direct = normalize_ip(host)
    if direct:
        return [direct]
    name = (host or "").strip()
    if not name:
        return []
    try:
        import socket

        found: list[str] = []
        for item in socket.getaddrinfo(name, None):
            address = normalize_ip(str(item[4][0]))
            if address and address not in found:
                found.append(address)
        return found
    except OSError:
        return []


def collect_exclude_ips(*, entry_host: str = "", panel_ip: str | None = None, via_entry: bool = False) -> list[str]:
    """Адреса, которые WARP не должен перехватывать, иначе оборвётся SSH."""
    collected: list[str] = []
    for address in resolve_host_ips(entry_host):
        if address not in collected:
            collected.append(address)
    if not via_entry:
        panel = normalize_ip(panel_ip or "")
        if panel and panel not in collected:
            collected.append(panel)
    return collected


def parse_trace(text: str) -> dict[str, str]:
    blob = text or ""
    ip_match = _IP_LINE.search(blob)
    warp_match = _WARP_LINE.search(blob)
    return {
        "ip": (ip_match.group(1).strip() if ip_match else ""),
        "warp": (warp_match.group(1).strip() if warp_match else ""),
    }


def detect_panel_public_ip(timeout: float = 8.0) -> str | None:
    """Публичный адрес компьютера с панелью. Нужен, когда SSH идёт на выход напрямую."""
    headers = {"User-Agent": "CascadeControl/warp"}
    for url in (TRACE_URL, TRACE_FALLBACK_URL):
        try:
            request = Request(url, headers=headers)
            with urlopen(request, timeout=timeout) as response:  # noqa: S310 — только два фиксированных URL Cloudflare
                parsed = parse_trace(response.read().decode("utf-8", errors="replace"))
            address = normalize_ip(parsed.get("ip") or "")
            if address:
                return address
        except (URLError, OSError, TimeoutError, ValueError):
            continue
    return None


def parse_warp_output(lines: list[str]) -> dict:
    marker = "CASCADE_WARP_JSON "
    for line in reversed(lines):
        if line.startswith(marker):
            raw = line[len(marker) :].strip()
            try:
                data = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise WarpError("не удалось разобрать ответ WARP") from exc
            if not isinstance(data, dict):
                raise WarpError("не удалось разобрать ответ WARP")
            return data
    errors = [line[len("CASCADE_ERR ") :].strip() for line in lines if line.startswith("CASCADE_ERR ")]
    if errors:
        raise WarpError(errors[-1])
    raise WarpError("скрипт WARP не вернул статус")


def warp_view(enabled: bool, payload: dict | None, last_error: str = "") -> dict:
    data = payload or {}
    connected = bool(data.get("connected")) if enabled else False
    egress = str(data.get("egress_ip") or "")
    if enabled and connected:
        status = str(data.get("status") or "подключён")
    elif enabled:
        status = str(data.get("status") or "выключен или ошибка")
    else:
        status = "выключен"
    return {
        "enabled": bool(enabled),
        "connected": connected,
        "egress_ip": egress,
        "direct_ip": str(data.get("direct_ip") or ""),
        "status": status,
        "error": last_error or str(data.get("error") or ""),
    }


def render_enable_script(exclude_ips: list[str]) -> str:
    cleaned: list[str] = []
    for raw in exclude_ips:
        address = normalize_ip(raw)
        if address and address not in cleaned:
            cleaned.append(address)
    if not cleaned:
        raise WarpError(
            "нельзя включать WARP без адреса входного узла или панели: иначе SSH к серверу может оборваться"
        )
    ips = " ".join(shlex.quote(item) for item in cleaned)
    private = " ".join(shlex.quote(item) for item in PRIVATE_EXCLUDES)
    return f"""#!/bin/bash
set -u
log() {{ echo "CASCADE_LOG $*"; }}
err() {{ echo "CASCADE_ERR $*"; }}
if [ "$(id -u)" -ne 0 ]; then SUDO="sudo -n"; else SUDO=; fi
IFACE={shlex.quote(WARP_IFACE)}
EXCLUDE_IPS="{ips}"
PRIVATE_EXCLUDES="{private}"
ROLLBACK=0

rollback() {{
  log откатываю WARP, каскад Xray и AmneziaWG не трогаю
  if command -v warp-cli >/dev/null 2>&1; then
    $SUDO warp-cli --accept-tos disconnect >/dev/null 2>&1 || $SUDO warp-cli disconnect >/dev/null 2>&1 || true
  fi
  $SUDO nft delete table inet cascade-warp >/dev/null 2>&1 || true
  if command -v iptables >/dev/null 2>&1; then
    $SUDO iptables -t nat -D POSTROUTING -o "$IFACE" -j MASQUERADE >/dev/null 2>&1 || true
  fi
  $SUDO systemctl disable --now cascade-warp-nat.service >/dev/null 2>&1 || true
  $SUDO rm -f /etc/systemd/system/cascade-warp-nat.service /opt/cascade/warp/nat.nft /etc/sysctl.d/99-cascade-warp.conf
  $SUDO systemctl daemon-reload >/dev/null 2>&1 || true
}}

fail() {{
  err "$*"
  if [ "$ROLLBACK" = 1 ]; then rollback; fi
  exit 1
}}

trap 'rc=$?; if [ $rc -ne 0 ] && [ "$ROLLBACK" = 1 ]; then rollback; fi' EXIT

mkdir_warp() {{
  $SUDO mkdir -p /opt/cascade/warp
  $SUDO chown -R "$USER" /opt/cascade/warp || true
}}

log запоминаю прямой адрес до включения WARP
BEFORE=$(curl -4 -fsS --max-time 15 {shlex.quote(TRACE_URL)} 2>/dev/null || curl -4 -fsS --max-time 15 {shlex.quote(TRACE_FALLBACK_URL)} 2>/dev/null || true)
BEFORE_IP=$(printf '%s\\n' "$BEFORE" | awk -F= '/^ip=/{{print $2; exit}}')
log прямой адрес ${{BEFORE_IP:-неизвестен}}

if ! command -v warp-cli >/dev/null 2>&1; then
  if ! command -v apt-get >/dev/null 2>&1; then
    fail "Cloudflare WARP ставится через apt. На этой системе автоматическая установка не поддерживается"
  fi
  log добавляю официальный репозиторий Cloudflare
  $SUDO apt-get update -y || true
  $SUDO apt-get install -y curl ca-certificates gnupg lsb-release || true
  CODENAME=$(lsb_release -cs 2>/dev/null || true)
  if [ -z "$CODENAME" ]; then
    CODENAME={shlex.quote(FALLBACK_CODENAME)}
    log кодовое имя выпуска не определено, беру $CODENAME
  fi
  TMPKEY=$(mktemp)
  curl -fsSL {shlex.quote(APT_KEY_URL)} -o "$TMPKEY" || fail "не скачался ключ репозитория Cloudflare"
  $SUDO mkdir -p /usr/share/keyrings
  $SUDO gpg --yes --dearmor --output /usr/share/keyrings/cloudflare-warp-archive-keyring.gpg "$TMPKEY" || fail "не удалось установить ключ репозитория Cloudflare"
  rm -f "$TMPKEY"
  echo "deb [signed-by=/usr/share/keyrings/cloudflare-warp-archive-keyring.gpg] {APT_REPO} $CODENAME main" | $SUDO tee /etc/apt/sources.list.d/cloudflare-client.list >/dev/null
  $SUDO apt-get update -y || fail "не обновился список пакетов Cloudflare"
  $SUDO apt-get install -y cloudflare-warp || fail "не установился пакет cloudflare-warp"
fi
command -v warp-cli >/dev/null 2>&1 || fail "команда warp-cli так и не появилась"

ROLLBACK=1
log принимаю условия и регистрирую WARP
$SUDO warp-cli --accept-tos registration new >/dev/null 2>&1 || $SUDO warp-cli registration new >/dev/null 2>&1 || true

exclude_one() {{
  ip="$1"
  if $SUDO warp-cli --accept-tos tunnel ip add "$ip" >/dev/null 2>&1; then
    log исключил из туннеля $ip
    return 0
  fi
  if $SUDO warp-cli tunnel ip add "$ip" >/dev/null 2>&1; then
    log исключил из туннеля $ip
    return 0
  fi
  if $SUDO warp-cli --accept-tos add-excluded-route "$ip" >/dev/null 2>&1 || $SUDO warp-cli add-excluded-route "$ip" >/dev/null 2>&1; then
    log исключил из туннеля $ip (старая команда)
    return 0
  fi
  return 1
}}

log исключаю адреса управления до подключения WARP
for ip in $EXCLUDE_IPS; do
  exclude_one "$ip" || fail "не удалось исключить $ip из туннеля WARP, подключать опасно"
done
for net in $PRIVATE_EXCLUDES; do
  exclude_one "$net" || log частную сеть $net исключить не удалось, продолжаю
done

# Режим warp + исключения: SSH к серверу идёт напрямую, интернет каскада — через Cloudflare.
if $SUDO warp-cli --accept-tos mode warp >/dev/null 2>&1 || $SUDO warp-cli mode warp >/dev/null 2>&1 || $SUDO warp-cli set-mode warp >/dev/null 2>&1; then
  log режим warp включён
else
  fail "не удалось выставить режим warp так, чтобы сервер оставался доступен"
fi

mkdir_warp
$SUDO tee /etc/sysctl.d/99-cascade-warp.conf >/dev/null << 'SYSCTL'
net.ipv4.ip_forward = 1
SYSCTL
$SUDO sysctl -w net.ipv4.ip_forward=1 >/dev/null || fail "не включился ip_forward"

$SUDO tee /opt/cascade/warp/nat.nft >/dev/null << EOF
table inet cascade-warp {{
  chain postrouting {{
    type nat hook postrouting priority 100; policy accept;
    oifname "$IFACE" masquerade
  }}
}}
EOF
$SUDO tee /etc/systemd/system/cascade-warp-nat.service >/dev/null << 'UNIT'
[Unit]
Description=Cascade Control NAT for Cloudflare WARP
After=network-online.target warp-svc.service
Wants=network-online.target
[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/bin/bash -c 'sysctl -w net.ipv4.ip_forward=1 >/dev/null; nft delete table inet cascade-warp >/dev/null 2>&1 || true; nft -f /opt/cascade/warp/nat.nft; if command -v iptables >/dev/null 2>&1; then iptables -t nat -C POSTROUTING -o CloudflareWARP -j MASQUERADE 2>/dev/null || iptables -t nat -A POSTROUTING -o CloudflareWARP -j MASQUERADE; fi'
ExecStop=/bin/bash -c 'nft delete table inet cascade-warp >/dev/null 2>&1 || true; if command -v iptables >/dev/null 2>&1; then iptables -t nat -D POSTROUTING -o CloudflareWARP -j MASQUERADE >/dev/null 2>&1 || true; fi'
[Install]
WantedBy=multi-user.target
UNIT
$SUDO systemctl daemon-reload
$SUDO systemctl enable cascade-warp-nat.service >/dev/null 2>&1 || true

log подключаю WARP
if ! $SUDO warp-cli --accept-tos connect && ! $SUDO warp-cli connect; then
  fail "не удалось подключить Cloudflare WARP"
fi

ok=0
for _ in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20 21 22 23 24 25 26 27 28 29 30; do
  if ip link show "$IFACE" >/dev/null 2>&1; then
    ok=1
    break
  fi
  sleep 1
done
[ "$ok" = 1 ] || fail "интерфейс CloudflareWARP так и не появился"

$SUDO systemctl start cascade-warp-nat.service || fail "не применились правила MASQUERADE для CloudflareWARP"
if command -v nft >/dev/null 2>&1; then
  $SUDO nft delete table inet cascade-warp >/dev/null 2>&1 || true
  $SUDO nft -f /opt/cascade/warp/nat.nft || fail "не применилась таблица nft inet cascade-warp"
fi
if command -v iptables >/dev/null 2>&1; then
  $SUDO iptables -t nat -C POSTROUTING -o "$IFACE" -j MASQUERADE 2>/dev/null || $SUDO iptables -t nat -A POSTROUTING -o "$IFACE" -j MASQUERADE || fail "не применился MASQUERADE iptables"
  log MASQUERADE на $IFACE включён
fi

log проверяю https://www.cloudflare.com/cdn-cgi/trace
AFTER=$(curl -4 -fsS --max-time 20 --interface "$IFACE" {shlex.quote(TRACE_URL)} 2>/dev/null || curl -4 -fsS --max-time 20 {shlex.quote(TRACE_URL)} 2>/dev/null || true)
AFTER_IP=$(printf '%s\\n' "$AFTER" | awk -F= '/^ip=/{{print $2; exit}}')
AFTER_WARP=$(printf '%s\\n' "$AFTER" | awk -F= '/^warp=/{{print $2; exit}}')
[ "$AFTER_WARP" = "on" ] || fail "WARP не подтвердился: в trace нет warp=on"
if [ -n "$BEFORE_IP" ] && [ -n "$AFTER_IP" ] && [ "$AFTER_IP" = "$BEFORE_IP" ]; then
  fail "адрес выхода не изменился ($AFTER_IP), WARP не перехватил трафик"
fi
[ -n "$AFTER_IP" ] || fail "не удалось прочитать адрес выхода WARP"

trap - EXIT
log WARP включён, выход $AFTER_IP
printf 'CASCADE_WARP_JSON %s\\n' "{{\\"enabled\\":true,\\"connected\\":true,\\"egress_ip\\":\\"$AFTER_IP\\",\\"direct_ip\\":\\"${{BEFORE_IP}}\\",\\"warp\\":\\"on\\",\\"status\\":\\"подключён\\"}}"
exit 0
"""


def render_disable_script(*, uninstall: bool = True) -> str:
    remove_pkg = "1" if uninstall else "0"
    return f"""#!/bin/bash
set -u
log() {{ echo "CASCADE_LOG $*"; }}
err() {{ echo "CASCADE_ERR $*"; }}
if [ "$(id -u)" -ne 0 ]; then SUDO="sudo -n"; else SUDO=; fi
IFACE={shlex.quote(WARP_IFACE)}

if command -v warp-cli >/dev/null 2>&1; then
  log отключаю WARP
  $SUDO warp-cli --accept-tos disconnect >/dev/null 2>&1 || $SUDO warp-cli disconnect >/dev/null 2>&1 || true
  $SUDO warp-cli --accept-tos registration delete >/dev/null 2>&1 || $SUDO warp-cli registration delete >/dev/null 2>&1 || true
fi

log снимаю правила MASQUERADE, каскад Xray и AmneziaWG не трогаю
$SUDO nft delete table inet cascade-warp >/dev/null 2>&1 || true
if command -v iptables >/dev/null 2>&1; then
  $SUDO iptables -t nat -D POSTROUTING -o "$IFACE" -j MASQUERADE >/dev/null 2>&1 || true
fi
$SUDO systemctl disable --now cascade-warp-nat.service >/dev/null 2>&1 || true
$SUDO rm -f /etc/systemd/system/cascade-warp-nat.service /opt/cascade/warp/nat.nft /opt/cascade/warp/enable.sh /opt/cascade/warp/disable.sh /etc/sysctl.d/99-cascade-warp.conf
$SUDO systemctl daemon-reload >/dev/null 2>&1 || true
# ip_forward не выключаю: он может быть нужен каскаду.

if [ {remove_pkg} = 1 ] && command -v apt-get >/dev/null 2>&1; then
  log удаляю пакет cloudflare-warp
  $SUDO apt-get remove -y cloudflare-warp >/dev/null 2>&1 || log пакет снять не удалось, правила уже сняты
  $SUDO rm -f /etc/apt/sources.list.d/cloudflare-client.list /usr/share/keyrings/cloudflare-warp-archive-keyring.gpg
  $SUDO apt-get update -y >/dev/null 2>&1 || true
fi

log WARP выключен
printf 'CASCADE_WARP_JSON %s\\n' '{{"enabled":false,"connected":false,"egress_ip":"","direct_ip":"","warp":"off","status":"выключен"}}'
exit 0
"""


WARP_SOURCE = "warp"
WARP_MODES = ("sites", "all")
# Типовой список для режима «только выбранные сайты». Пользователь может менять его в панели.
DEFAULT_WARP_SITES = (
    ("geosite", "openai"),
    ("geosite", "google"),
    ("domain", "gemini.google.com"),
    ("domain", "gemini.google"),
    ("domain", "aistudio.google.com"),
    ("domain", "generativelanguage.googleapis.com"),
    ("domain", "notebooklm.google"),
    ("domain", "notebooklm.google.com"),
)
CF_PEER_PUBLIC = "bmXOC+F1FxEMF9dyiK2H5/1SUtzHpluFPRsv0gDwv1s="
WARP_REG_URL = "https://api.cloudflareclient.com/v0a1922/reg"


def parse_warp_entry(raw: str) -> tuple[str, str]:
    """Разбирает строку списка WARP: домен, сеть, geosite: или geoip:."""
    text = (raw or "").strip()
    if not text:
        raise WarpError("нужен домен, сеть или категория geosite:/geoip:")
    lower = text.lower()
    if lower.startswith("geosite:"):
        name = text.split(":", 1)[1].strip()
        if not name:
            raise WarpError("после geosite: нужно имя категории")
        return "geosite", name
    if lower.startswith("geoip:"):
        name = text.split(":", 1)[1].strip()
        if not name:
            raise WarpError("после geoip: нужно имя категории")
        return "geoip", name
    if "/" in text or normalize_ip(text):
        try:
            net = ipaddress.ip_network(text, strict=False)
        except ValueError as exc:
            raise WarpError("это не IP и не сеть") from exc
        return "cidr", str(net)
    host = lower.rstrip(".")
    if " " in host or "/" in host:
        raise WarpError("нужен домен, сеть или категория geosite:/geoip:")
    return "domain", host


def entry_to_xray(kind: str, value: str) -> tuple[str, str]:
    """Возвращает (семейство domain|ip, значение для правила Xray)."""
    if kind == "geosite":
        return "domain", value if value.startswith("geosite:") else f"geosite:{value}"
    if kind == "geoip":
        return "ip", value if value.startswith("geoip:") else f"geoip:{value}"
    if kind == "cidr":
        return "ip", value
    return "domain", value if ":" in value else f"domain:{value}"


def compile_warp_matchers(entries: list[dict]) -> tuple[list[str], list[str]]:
    domains: list[str] = []
    ips: list[str] = []
    for item in entries:
        if item.get("excluded"):
            continue
        family, value = entry_to_xray(str(item.get("kind") or ""), str(item.get("value") or ""))
        if family == "ip":
            if value not in ips:
                ips.append(value)
        elif value and value not in domains:
            domains.append(value)
    return domains, ips


def protection_rules(*, bittorrent: bool = False, private: bool = False, ads: bool = False) -> list[dict]:
    rules: list[dict] = []
    if bittorrent:
        rules.append({"type": "field", "protocol": ["bittorrent"], "outboundTag": "block"})
    if private:
        rules.append({"type": "field", "ip": ["geoip:private"], "outboundTag": "block"})
    if ads:
        rules.append({"type": "field", "domain": ["geosite:category-ads-all"], "outboundTag": "block"})
    return rules


def wireguard_outbound(tag: str, account: dict, *, masque: bool = False) -> dict:
    endpoint = str(account.get("endpoint") or "engage.cloudflareclient.com:2408")
    if masque and ":" in endpoint:
        host = endpoint.rsplit(":", 1)[0]
        endpoint = f"{host}:443"
    reserved = account.get("reserved") or [0, 0, 0]
    if isinstance(reserved, str):
        reserved = _reserved_from_b64(reserved)
    addresses = account.get("address") or account.get("addresses") or ["172.16.0.2/32"]
    if isinstance(addresses, str):
        addresses = [addresses]
    outbound: dict = {
        "tag": tag,
        "protocol": "wireguard",
        "settings": {
            "secretKey": account["secret_key"],
            "address": list(addresses),
            "peers": [
                {
                    "publicKey": str(account.get("peer_public") or CF_PEER_PUBLIC),
                    "allowedIPs": ["0.0.0.0/0", "::/0"],
                    "endpoint": endpoint,
                }
            ],
            "reserved": list(reserved),
            "mtu": 1280,
            "domainStrategy": "ForceIPv4",
        },
    }
    if masque:
        # Если установленный Xray не знает MASQUE, конфиг не пройдёт -test и панель пропустит его.
        outbound["streamSettings"] = {"network": "masque"}
    return outbound


def _reserved_from_b64(value: str) -> list[int]:
    import base64

    raw = base64.b64decode(value + "==")
    return [b for b in raw[:3]] or [0, 0, 0]


def parse_warp_account(doc: dict, secret_key: str) -> dict:
    config = doc.get("config") or doc
    interface = config.get("interface") or {}
    addresses = interface.get("addresses") or {}
    addr: list[str] = []
    if addresses.get("v4"):
        v4 = addresses["v4"]
        addr.append(v4 if "/" in str(v4) else f"{v4}/32")
    if addresses.get("v6"):
        v6 = addresses["v6"]
        addr.append(v6 if "/" in str(v6) else f"{v6}/128")
    peers = config.get("peers") or [{}]
    peer = peers[0] if peers else {}
    endpoint = peer.get("endpoint") or {}
    if isinstance(endpoint, dict):
        host = endpoint.get("host") or "engage.cloudflareclient.com"
        port = endpoint.get("port") or 2408
        ep = f"{host}:{port}"
    else:
        ep = str(endpoint or "engage.cloudflareclient.com:2408")
    reserved = doc.get("reserved")
    if not reserved:
        client_id = config.get("client_id") or interface.get("client_id") or ""
        reserved = _reserved_from_b64(client_id) if client_id else [0, 0, 0]
    return {
        "secret_key": secret_key,
        "address": addr or ["172.16.0.2/32"],
        "peer_public": peer.get("public_key") or CF_PEER_PUBLIC,
        "endpoint": ep,
        "reserved": reserved,
    }


def register_warp_account(public_key: str, secret_key: str, opener=None) -> dict:
    """Регистрирует учётную запись WARP по API Cloudflare (как wgcf). В тестах подменяется opener."""
    from datetime import datetime, timezone
    from urllib.request import Request

    payload = json.dumps(
        {
            "key": public_key,
            "install_id": "",
            "fcm_token": "",
            "tos": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            "type": "Android",
            "locale": "en_US",
        }
    ).encode("utf-8")
    request = Request(
        WARP_REG_URL,
        data=payload,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "User-Agent": "okhttp/3.12.1",
            "CF-Client-Version": "a-6.30-1234",
        },
    )
    open_fn = opener or urlopen
    try:
        with open_fn(request, timeout=20) as response:
            doc = json.loads(response.read().decode("utf-8", errors="replace"))
    except (URLError, OSError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
        raise WarpError("не удалось зарегистрировать WARP у Cloudflare") from exc
    if not isinstance(doc, dict):
        raise WarpError("не удалось зарегистрировать WARP у Cloudflare")
    return parse_warp_account(doc, secret_key)


def masque_supported(xray_help: str) -> bool:
    text = (xray_help or "").lower()
    return "masque" in text


def public_warp_status(payload: dict | None, *, enabled: bool, mode: str, last_error: str = "") -> dict:
    view = warp_view(enabled, payload, last_error)
    view["mode"] = mode if mode in WARP_MODES else "sites"
    view["masque"] = str((payload or {}).get("masque") or "")
    return view
