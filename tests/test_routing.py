from cascade_panel.presets import domain_in_preset
from cascade_panel.routing import Catalog, RuleView, compile_xray_rules, evaluate


def rules():
    return [
        RuleView("1", 1, True, "ru", {"any_of": [{"geosite": "ru"}, {"geoip": "ru"}]}, "direct"),
        RuleView("2", 2, False, "off", {"domains": ["off.example"]}, "block"),
        RuleView("3", 3, True, "yt", {"preset": "youtube"}, "cascade"),
        RuleView("4", 4, True, "ai", {"any_of": [{"preset": "openai"}, {"preset": "google-ai"}]}, "exit", "nl"),
        RuleView("5", 5, True, "ads", {"preset": "ads"}, "block"),
        RuleView("6", 6, True, "rest", {"any": True}, "cascade"),
    ]


def test_first_match_wins_and_disabled_rules_are_skipped():
    catalog = Catalog(geosite={"ru": {"gosuslugi.ru", "yandex.ru"}}, geoip={})
    decision = evaluate(rules(), "mail.yandex.ru", None, catalog)
    assert decision.action == "direct"
    assert decision.rule_id == "1"
    assert evaluate(rules(), "off.example", None, catalog).action != "block"
    yt = evaluate(rules(), "www.youtube.com", None, catalog)
    assert yt.action == "cascade" and yt.rule_id == "3"
    ai = evaluate(rules(), "chatgpt.com", None, catalog)
    assert ai.action == "exit" and ai.exit_id == "nl"
    assert evaluate(rules(), "doubleclick.net", None, catalog).action == "block"
    assert evaluate(rules(), "unknown.example", None, catalog).rule_id == "6"


def test_preset_covers_subdomains_and_xray_compile_order():
    assert domain_in_preset("banks-gov", "lk.gosuslugi.ru")
    compiled = compile_xray_rules(rules())
    assert compiled[0]["domain"][0] == "geosite:ru"
    assert compiled[-1]["network"] == "tcp,udp"
    assert any(item.get("outboundTag") == "block" for item in compiled)
