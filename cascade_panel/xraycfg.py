"""Сборка конфигов Xray для входа, транзита и выхода.

Весь клиентский трафик AWG уходит в tproxy, а разделение direct/cascade/block
делает routing Xray со sniffing (routeOnly), чтобы freedom выходил с IP входа.
"""

from __future__ import annotations

from .reality import RealityMaterial
from .routing import RuleView, compile_xray_rules


def _reality_inbound(tag: str, material: RealityMaterial, clients: list[dict]) -> dict:
    return {
        "tag": tag,
        "listen": "0.0.0.0",
        "port": material.port,
        "protocol": "vless",
        "settings": {
            "clients": clients,
            "decryption": "none",
        },
        "streamSettings": {
            "network": "tcp",
            "security": "reality",
            "realitySettings": {
                "show": False,
                "dest": material.dest,
                "xver": 0,
                "serverNames": [material.sni],
                "privateKey": material.private_key,
                "shortIds": [material.short_id],
            },
        },
        "sniffing": {"enabled": True, "destOverride": ["http", "tls", "quic"], "routeOnly": True},
    }


def _vless_outbound(tag: str, host: str, material: RealityMaterial, user_uuid: str) -> dict:
    return {
        "tag": tag,
        "protocol": "vless",
        "settings": {
            "vnext": [
                {
                    "address": host,
                    "port": material.port,
                    "users": [{"id": user_uuid, "encryption": "none", "flow": material.flow}],
                }
            ]
        },
        "streamSettings": {
            "network": "tcp",
            "security": "reality",
            "realitySettings": {
                "serverName": material.sni,
                "fingerprint": material.fingerprint,
                "publicKey": material.public_key,
                "shortId": material.short_id,
            },
        },
    }


def _strip_meta(rules: list[dict]) -> list[dict]:
    clean = []
    for rule in rules:
        item = {k: v for k, v in rule.items() if not k.startswith("_")}
        clean.append(item)
    return clean


def entry_config(
    *,
    client_material: RealityMaterial,
    client_users: list[dict],
    tproxy_port: int,
    rules: list[RuleView],
    cascade_outbounds: list[dict],
    cascade_tags: list[str],
    strategy: str,
) -> dict:
    routing_rules = _strip_meta(compile_xray_rules(rules))
    if not any(r.get("network") == "tcp,udp" for r in routing_rules):
        routing_rules.append({"type": "field", "network": "tcp,udp", "balancerTag": "cascade"})
    else:
        # catch-all с outboundTag cascade заменяем на балансировщик
        for rule in routing_rules:
            if rule.get("network") == "tcp,udp" and rule.get("outboundTag") == "cascade":
                rule.pop("outboundTag", None)
                rule["balancerTag"] = "cascade"
    strategy_type = {"least_ping": "leastPing", "round_robin": "roundRobin", "weighted": "random"}.get(
        strategy, "leastPing"
    )
    # TODO: трафик клиентов VLESS. StatsService и api здесь не включены,
    # чтобы не трогать рабочий tproxy. Пока живые rx/tx есть только у AWG.
    return {
        "log": {"loglevel": "warning"},
        "inbounds": [
            {
                "tag": "tproxy-in",
                "listen": "0.0.0.0",
                "port": tproxy_port,
                "protocol": "dokodemo-door",
                "settings": {"network": "tcp,udp", "followRedirect": True},
                "streamSettings": {"sockopt": {"tproxy": "tproxy"}},
                "sniffing": {
                    "enabled": True,
                    "destOverride": ["http", "tls", "quic"],
                    "routeOnly": True,
                },
            },
            _reality_inbound("vless-clients", client_material, client_users),
        ],
        "outbounds": [
            {"tag": "direct", "protocol": "freedom"},
            {"tag": "block", "protocol": "blackhole"},
            *cascade_outbounds,
        ],
        "routing": {
            "domainStrategy": "IPIfNonMatch",
            "rules": routing_rules,
            "balancers": [
                {
                    "tag": "cascade",
                    "selector": cascade_tags or ["cascade"],
                    "strategy": {"type": strategy_type},
                }
            ],
        },
        "observatory": {
            "subjectSelector": ["exit-"],
            "probeURL": "https://www.gstatic.com/generate_204",
            "probeInterval": "30s",
        },
    }


def transit_config(
    *,
    inbound: RealityMaterial,
    inbound_users: list[dict],
    upstreams: list[dict],
    tags: list[str],
    strategy: str,
    user_rules: bool = False,
) -> dict:
    strategy_type = {"least_ping": "leastPing", "round_robin": "roundRobin", "weighted": "random"}.get(
        strategy, "leastPing"
    )
    if user_rules:
        rules = []
        for user in inbound_users:
            email = user.get("email")
            if email:
                rules.append({"type": "field", "user": [email], "outboundTag": email})
        rules.append({"type": "field", "network": "tcp,udp", "outboundTag": tags[0] if tags else "block"})
        routing: dict = {"domainStrategy": "AsIs", "rules": rules}
    else:
        routing = {
            "domainStrategy": "AsIs",
            "rules": [{"type": "field", "network": "tcp,udp", "balancerTag": "exits"}],
            "balancers": [
                {"tag": "exits", "selector": tags, "strategy": {"type": strategy_type}},
            ],
        }
    return {
        "log": {"loglevel": "warning"},
        "inbounds": [_reality_inbound("from-entry", inbound, inbound_users)],
        "outbounds": [*upstreams, {"tag": "block", "protocol": "blackhole"}],
        "routing": routing,
    }


def exit_config(*, inbound: RealityMaterial, inbound_users: list[dict]) -> dict:
    return {
        "log": {"loglevel": "warning"},
        "inbounds": [_reality_inbound("from-transit", inbound, inbound_users)],
        "outbounds": [
            {"tag": "direct", "protocol": "freedom"},
            {"tag": "block", "protocol": "blackhole"},
        ],
        "routing": {
            "rules": [{"type": "field", "network": "tcp,udp", "outboundTag": "direct"}],
        },
    }


def link_outbound(tag: str, host: str, material: RealityMaterial, user_uuid: str) -> dict:
    return _vless_outbound(tag, host, material, user_uuid)
