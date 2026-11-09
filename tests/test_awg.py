import base64
import random

from cascade_panel.awg import (
    INTERFACE_FIELDS,
    PEER_FIELDS,
    generate_params,
    parse_conf,
    render_client_conf,
    render_server_conf,
    validate_params,
)


def test_hundred_profiles_stay_inside_protocol_bounds():
    for seed in range(100):
        params = generate_params(random.Random(seed))
        validate_params(params)
        assert (params.h1, params.h2, params.h3, params.h4) == ("1", "2", "3", "4")
        assert params.s1 == params.s2 == params.s3 == params.s4
        assert params.s1 >= 12
        assert params.random_trailers == "on"
        assert params.disable_cookies == "on"
        assert 4 <= params.jc <= 6
        raw = base64.b64decode(params.header_protection_key)
        assert len(raw) == 32


def test_client_conf_field_order_matches_amnezia_template():
    params = generate_params(random.Random(1))
    text = render_client_conf(
        address="10.10.1.14/32",
        dns1="1.1.1.1",
        dns2="1.0.0.1",
        private_key="cHJpdmF0ZQ==",
        params=params,
        server_public_key="cHVibGlj",
        preshared_key="cHNr",
        endpoint_host="203.0.113.10",
        endpoint_port=51820,
    )
    parsed = parse_conf(text)
    assert list(parsed["Interface"]) == INTERFACE_FIELDS
    assert list(parsed["Peer"]) == PEER_FIELDS
    assert parsed["Interface"]["DNS"] == "1.1.1.1, 1.0.0.1"
    assert parsed["Interface"]["H1"] == "1"
    assert parsed["Peer"]["Endpoint"] == "203.0.113.10:51820"
    assert parsed["Peer"]["AllowedIPs"] == "0.0.0.0/0, ::/0"
    assert "I1" not in parsed["Interface"]
    server = render_server_conf(
        server_private_key="c2VydmVy",
        address_cidr="10.10.1.1/24",
        listen_port=51820,
        params=params,
        peers=[{"public_key": "Y2xpZW50", "preshared_key": "cHNr", "address": "10.10.1.14"}],
    )
    server_parsed = parse_conf(server)
    assert server_parsed["Interface"]["HeaderProtectionKey"] == params.header_protection_key
    assert server_parsed["Interface"]["Jc"] == str(params.jc)
    assert server_parsed["Peer"]["AllowedIPs"] == "10.10.1.14/32"
