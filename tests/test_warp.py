import json

from cascade_panel.ssh_client import FakeSession
from cascade_panel.warp import (
    DEFAULT_WARP_SITES,
    collect_exclude_ips,
    compile_warp_matchers,
    masque_supported,
    parse_trace,
    parse_warp_account,
    parse_warp_entry,
    parse_warp_output,
    protection_rules,
    render_disable_script,
    render_enable_script,
    wireguard_outbound,
)
from cascade_panel.xraycfg import exit_config
from cascade_panel.reality import RealityMaterial


def test_enable_script_uses_official_repo_and_excludes_before_connect():
    script = render_enable_script(["192.0.2.10", "198.51.100.20"])
    assert "pkg.cloudflareclient.com" in script
    assert "CODENAME=noble" in script or "noble" in script
    assert "tunnel ip add" in script
    assert "192.0.2.10" in script
    assert script.index("tunnel ip add") < script.index("warp-cli --accept-tos connect") or script.index("exclude_one") < script.index("connect")
    assert "inet cascade-warp" in script
    assert "CloudflareWARP" in script
    assert "MASQUERADE" in script or "masquerade" in script
    assert "cdn-cgi/trace" in script
    assert "rollback" in script
    assert "каскад Xray и AmneziaWG не трогаю" in script


def test_enable_script_refuses_empty_excludes():
    try:
        render_enable_script([])
    except Exception as exc:
        assert "нельзя включать WARP" in str(exc)
    else:
        raise AssertionError("ожидали ошибку")


def test_disable_script_disconnects_and_removes_rules():
    script = render_disable_script()
    assert "disconnect" in script
    assert "cascade-warp" in script
    assert "cloudflare-warp" in script
    assert "ip_forward не выключаю" in script


def test_parse_trace_and_output():
    parsed = parse_trace("ip=203.0.113.50\nwarp=on\n")
    assert parsed == {"ip": "203.0.113.50", "warp": "on"}
    data = parse_warp_output(
        [
            "CASCADE_LOG ok",
            'CASCADE_WARP_JSON {"enabled":true,"connected":true,"egress_ip":"203.0.113.50","warp":"on","status":"подключён"}',
        ]
    )
    assert data["egress_ip"] == "203.0.113.50"


def test_exclude_ips_skips_panel_when_via_entry(monkeypatch):
    monkeypatch.setattr("cascade_panel.warp.resolve_host_ips", lambda host: ["192.0.2.10"] if host else [])
    assert collect_exclude_ips(entry_host="entry.example", panel_ip="198.51.100.8", via_entry=True) == ["192.0.2.10"]
    assert "198.51.100.8" in collect_exclude_ips(entry_host="entry.example", panel_ip="198.51.100.8", via_entry=False)


def test_warp_entry_parsing_and_xray_matchers():
    assert parse_warp_entry("geosite:openai") == ("geosite", "openai")
    assert parse_warp_entry("10.0.0.0/8")[0] == "cidr"
    assert parse_warp_entry("Gemini.Google.com") == ("domain", "gemini.google.com")
    domains, ips = compile_warp_matchers(
        [
            {"kind": "geosite", "value": "openai", "excluded": False},
            {"kind": "cidr", "value": "192.0.2.0/24", "excluded": False},
            {"kind": "domain", "value": "gemini.google.com", "excluded": True},
        ]
    )
    assert "geosite:openai" in domains
    assert ips == ["192.0.2.0/24"]
    assert DEFAULT_WARP_SITES[0] == ("geosite", "openai")


def test_protection_and_sites_exit_config():
    material = RealityMaterial(
        port=443, sni="www.example.com", dest="www.example.com:443",
        private_key="priv", public_key="pub", short_id="ab", uuid="u", flow="xtls-rprx-vision", fingerprint="chrome",
    )
    account = {
        "secret_key": "c2VjcmV0",
        "address": ["172.16.0.2/32"],
        "peer_public": "bmXOC+F1FxEMF9dyiK2H5/1SUtzHpluFPRsv0gDwv1s=",
        "endpoint": "engage.cloudflareclient.com:2408",
        "reserved": [1, 2, 3],
    }
    cfg = exit_config(
        inbound=material,
        inbound_users=[{"id": "u"}],
        protections={"bittorrent": True, "private": True, "ads": True},
        warp={"enabled": True, "mode": "sites", "account": account, "sites": [{"kind": "geosite", "value": "openai"}]},
    )
    tags = [item["tag"] for item in cfg["outbounds"]]
    assert tags[:2] == ["direct", "block"]
    assert "warp" in tags
    protocols = [rule.get("protocol") for rule in cfg["routing"]["rules"]]
    assert ["bittorrent"] in protocols
    assert any(rule.get("ip") == ["geoip:private"] for rule in cfg["routing"]["rules"])
    assert any(rule.get("domain") == ["geosite:category-ads-all"] for rule in cfg["routing"]["rules"])
    assert any(rule.get("outboundTag") == "warp" and "geosite:openai" in (rule.get("domain") or []) for rule in cfg["routing"]["rules"])
    assert cfg["routing"]["rules"][-1]["outboundTag"] == "direct"
    # секрет есть в конфиге узла, но не отдаётся API
    blob = json.dumps(cfg)
    assert "c2VjcmV0" in blob
    assert masque_supported("something masque here") is True
    assert masque_supported("wireguard only") is False


def test_parse_account_and_outbound():
    account = parse_warp_account(
        {
            "config": {
                "interface": {"addresses": {"v4": "172.16.0.2"}},
                "peers": [{"public_key": "pub", "endpoint": {"host": "engage.cloudflareclient.com", "port": 2408}}],
                "client_id": "AQID",
            }
        },
        "SECRET",
    )
    assert account["secret_key"] == "SECRET"
    assert "172.16.0.2/32" in account["address"]
    outbound = wireguard_outbound("warp", account)
    assert outbound["protocol"] == "wireguard"
    assert outbound["settings"]["peers"][0]["endpoint"].endswith(":2408")


def test_fake_session_runs_warp_script():
    ssh = FakeSession()
    lines = []
    assert ssh.run("bash /opt/cascade/warp/enable.sh", lines.append) == 0
    assert any("CASCADE_WARP_JSON" in line for line in lines)
    lines = []
    ssh = FakeSession(fail_warp=True)
    assert ssh.run("bash /opt/cascade/warp/enable.sh", lines.append) == 1
