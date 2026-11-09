import json
import random

from cascade_panel.awg import generate_params
from cascade_panel.vpnuri import build_envelope, build_last_config, decode_vpn_uri, encode_vpn_uri, qcompress, quncompress


def test_qcompress_roundtrip():
    raw = "каскад".encode()
    assert quncompress(qcompress(raw)) == raw


def test_vpn_uri_imports_as_amnezia_awg_31():
    params = generate_params(random.Random(3))
    conf = "[Interface]\nPrivateKey = abc\n"
    last = build_last_config(
        native_conf=conf,
        host="203.0.113.10",
        port=51820,
        client_ip="10.10.1.14",
        client_private="cHJpdmF0ZS1rZXk=",
        client_public="cHVibGljLWtleQ==",
        server_public="c2VydmVyLWtleQ==",
        psk="cHNrLWtleQ==",
        params=params,
        dns1="1.1.1.1",
        dns2="1.0.0.1",
    )
    envelope = build_envelope(
        host="203.0.113.10",
        description="Роутер дом",
        port=51820,
        last_config=last,
        dns1="1.1.1.1",
        dns2="1.0.0.1",
    )
    uri = encode_vpn_uri(envelope)
    assert uri.startswith("vpn://")
    assert "=" not in uri[len("vpn://") :]
    decoded = decode_vpn_uri(uri)
    assert decoded["defaultContainer"] == "amnezia-awg"
    awg = decoded["containers"][0]["awg"]
    assert awg["transport_proto"] == "udp"
    assert awg["protocol_version"] == "3.1"
    assert isinstance(awg["last_config"], str)
    inner = json.loads(awg["last_config"])
    assert inner["isObfuscationEnabled"] is True
    assert inner["client_priv_key"] == "cHJpdmF0ZS1rZXk="
    assert inner["HeaderProtectionKey"] == params.header_protection_key
    assert inner["H1"] == "1" and inner["H4"] == "4"
    assert inner["allowed_ips"] == ["0.0.0.0/0", "::/0"]
    assert inner["config"] == conf
