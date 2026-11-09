"""Движок правил входа: первое совпавшее включённое правило задаёт маршрут."""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass, field

from .presets import PRESETS, domain_in_preset


@dataclass
class RuleView:
    id: str
    position: int
    enabled: bool
    name: str
    match: dict
    action: str  # direct | cascade | exit | block
    exit_id: str | None = None
    hits: int = 0


@dataclass
class Decision:
    rule_id: str | None
    action: str
    exit_id: str | None
    reason: str


@dataclass
class Catalog:
    """Раскрытые списки. Ключ категории geosite/geoip -> множество значений."""

    geosite: dict[str, set[str]] = field(default_factory=dict)
    geoip: dict[str, list[ipaddress._BaseNetwork]] = field(default_factory=dict)
    extra_domains: dict[str, str] = field(default_factory=dict)  # domain -> action
    extra_cidrs: list[tuple[ipaddress._BaseNetwork, str]] = field(default_factory=list)


def _domain_matches(pattern: str, host: str) -> bool:
    pattern = pattern.lower().strip().rstrip(".")
    host = host.lower().strip().rstrip(".")
    if pattern.startswith("*."):
        suffix = pattern[2:]
        return host == suffix or host.endswith("." + suffix)
    return host == pattern or host.endswith("." + pattern)


def _match_clause(clause: dict, host: str | None, ip: str | None, catalog: Catalog) -> bool:
    if clause.get("any") is True:
        return True
    if "domain" in clause and host:
        return _domain_matches(str(clause["domain"]), host)
    if "domains" in clause and host:
        return any(_domain_matches(str(item), host) for item in clause["domains"])
    if "geosite" in clause and host:
        names = catalog.geosite.get(str(clause["geosite"]), set())
        h = host.lower().rstrip(".")
        return any(h == name or h.endswith("." + name) for name in names)
    if "cidr" in clause and ip:
        try:
            addr = ipaddress.ip_address(ip)
            return addr in ipaddress.ip_network(str(clause["cidr"]), strict=False)
        except ValueError:
            return False
    if "cidrs" in clause and ip:
        try:
            addr = ipaddress.ip_address(ip)
        except ValueError:
            return False
        for item in clause["cidrs"]:
            try:
                if addr in ipaddress.ip_network(str(item), strict=False):
                    return True
            except ValueError:
                continue
        return False
    if "geoip" in clause and ip:
        try:
            addr = ipaddress.ip_address(ip)
        except ValueError:
            return False
        for net in catalog.geoip.get(str(clause["geoip"]), []):
            if addr in net:
                return True
        return False
    if "preset" in clause:
        preset = PRESETS.get(str(clause["preset"]))
        if not preset:
            return False
        if host and domain_in_preset(str(clause["preset"]), host):
            return True
        if ip:
            try:
                addr = ipaddress.ip_address(ip)
            except ValueError:
                return False
            for item in preset["cidrs"]:
                if addr in ipaddress.ip_network(item, strict=False):
                    return True
        return False
    if "all" in clause and isinstance(clause["all"], list):
        return all(_match_clause(part, host, ip, catalog) for part in clause["all"])
    if "any_of" in clause and isinstance(clause["any_of"], list):
        return any(_match_clause(part, host, ip, catalog) for part in clause["any_of"])
    return False


def evaluate(rules: list[RuleView], host: str | None, ip: str | None, catalog: Catalog | None = None) -> Decision:
    catalog = catalog or Catalog()
    ordered = sorted(rules, key=lambda r: r.position)
    for rule in ordered:
        if not rule.enabled:
            continue
        if _match_clause(rule.match, host, ip, catalog):
            return Decision(rule.id, rule.action, rule.exit_id, rule.name)
    return Decision(None, "cascade", None, "нет правила, каскад по умолчанию")


def compile_xray_rules(rules: list[RuleView]) -> list[dict]:
    """Правила Xray. Балансировщик каскада вызывается последним catch-all не здесь,
    а отдельным правилом сети, если в списке нет ``any: true``.
    """
    compiled: list[dict] = []
    for rule in sorted(rules, key=lambda r: r.position):
        if not rule.enabled:
            continue
        outbound = _outbound_tag(rule)
        domains, ips = _xray_matchers(rule.match)
        if rule.match.get("any") is True:
            compiled.append({"type": "field", "network": "tcp,udp", "outboundTag": outbound, "_rule": rule.id})
            continue
        if domains:
            compiled.append({"type": "field", "domain": domains, "outboundTag": outbound, "_rule": rule.id})
        if ips:
            compiled.append({"type": "field", "ip": ips, "outboundTag": outbound, "_rule": rule.id})
        preset_id = rule.match.get("preset")
        if preset_id and preset_id in PRESETS:
            preset = PRESETS[preset_id]
            compiled.append(
                {
                    "type": "field",
                    "domain": [f"domain:{name}" for name in preset["domains"]],
                    "outboundTag": outbound,
                    "_rule": rule.id,
                }
            )
            if preset["cidrs"]:
                compiled.append(
                    {"type": "field", "ip": list(preset["cidrs"]), "outboundTag": outbound, "_rule": rule.id}
                )
    return compiled


def _outbound_tag(rule: RuleView) -> str:
    if rule.action == "direct":
        return "direct"
    if rule.action == "block":
        return "block"
    if rule.action == "exit" and rule.exit_id:
        return f"exit-{rule.exit_id}"
    return "cascade"


def _xray_matchers(match: dict) -> tuple[list[str], list[str]]:
    domains: list[str] = []
    ips: list[str] = []
    if "domain" in match:
        domains.append(f"domain:{match['domain']}")
    for name in match.get("domains") or []:
        domains.append(f"domain:{name}")
    if "geosite" in match:
        domains.append(f"geosite:{match['geosite']}")
    if "cidr" in match:
        ips.append(str(match["cidr"]))
    for item in match.get("cidrs") or []:
        ips.append(str(item))
    if "geoip" in match:
        ips.append(f"geoip:{match['geoip']}")
    if "any_of" in match:
        for part in match["any_of"]:
            d, i = _xray_matchers(part)
            domains.extend(d)
            ips.extend(i)
    return domains, ips
