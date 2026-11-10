import base64
import json
import os
import shutil
import subprocess
import tempfile

import pytest

from cascade_panel.reality import generate_reality, normalize_reality_key, reality_key_b64, vless_uri
from cascade_panel.xraycfg import exit_config


def test_reality_keys_are_rawurl_and_old_std_base64_converts():
    material = generate_reality(443)
    for key in (material.private_key, material.public_key):
        assert len(key) == 43
        assert "=" not in key
        assert "+" not in key and "/" not in key
        assert normalize_reality_key(key) == key
    raw = bytes(range(32))
    standard = base64.b64encode(raw).decode("ascii")
    assert standard.endswith("=")
    assert normalize_reality_key(standard) == reality_key_b64(raw)
    link = vless_uri(material, "203.0.113.10", "телефон")
    assert f"pbk={material.public_key}" in link
    assert "%3D" not in link


def test_xray_accepts_generated_exit_config():
    binary = os.environ.get("XRAY_BIN") or shutil.which("xray") or "/tmp/xray-v26.10.31"
    if not binary or not os.path.isfile(binary):
        pytest.skip("бинарник xray не установлен")
    material = generate_reality(8443)
    config = exit_config(inbound=material, inbound_users=[{"id": material.uuid, "flow": material.flow}])
    text = json.dumps(config)
    assert material.private_key in text
    assert "=" not in material.private_key
    with tempfile.TemporaryDirectory() as folder:
        path = os.path.join(folder, "config.json")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)
        result = subprocess.run([binary, "run", "-test", "-config", path], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
