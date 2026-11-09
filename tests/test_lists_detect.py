from cascade_panel.detect import Probe, classify, classify_body
from cascade_panel.lists import parse_cidr_list, parse_domain_list


def test_domain_and_cidr_parsers_skip_comments():
    domains = parse_domain_list("# c\nYouTube.com\n\nfull:gemini.google.com\nnot a domain\n")
    assert domains == ["youtube.com", "gemini.google.com"]
    cidrs = parse_cidr_list("# x\n10.0.0.0/8\n999.1.1.1/32\n192.0.2.1\n")
    assert "10.0.0.0/8" in cidrs
    assert "192.0.2.1/32" in cidrs


def test_block_classifier():
    assert classify(Probe("timeout")) == "cascade"
    assert classify(Probe("reset")) == "cascade"
    assert classify(Probe("ok")) is None
    stub = classify_body(200, "<html>Доступ ограничен по решению РКН</html>")
    assert stub.status == "stub"
    assert classify(stub) == "cascade"
    assert classify(classify_body(200, "<html>welcome</html>")) is None
