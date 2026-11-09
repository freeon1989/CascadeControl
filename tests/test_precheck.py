import json

from cascade_panel.precheck import evaluate, parse_precheck_output


def payload(**overrides):
    raw = {
        "virt": "kvm",
        "os_id": "ubuntu",
        "os_version": "24.04.1",
        "os_pretty": "Ubuntu 24.04.1 LTS",
        "kernel": "6.8.0-45-generic",
        "disk_free_gb": 40,
        "docker": True,
        "port_443_tcp_free": True,
        "used_ports": [22],
        "containers": [],
    }
    raw.update(overrides)
    return raw


def test_parser_takes_the_json_line_out_of_the_log():
    text = "hello\nCASCADE_JSON " + json.dumps(payload()) + "\ntrailer"
    assert parse_precheck_output(text)["virt"] == "kvm"


def test_kvm_ubuntu_and_debian_pass_openvz_does_not():
    ok = evaluate(payload())
    assert ok.ok
    titles = {c.id: c.level for c in ok.checks}
    assert titles["virt"] == "ok"
    assert titles["os"] == "ok"
    debian = evaluate(payload(os_id="debian", os_version="12.5", os_pretty="Debian GNU/Linux 12"))
    assert debian.ok
    vz = evaluate(payload(virt="openvz"))
    assert not vz.ok
    assert any(c.id == "virt" and c.level == "fail" for c in vz.checks)


def test_old_os_kernel_and_disk():
    assert not evaluate(payload(os_id="ubuntu", os_version="20.04")).ok
    assert not evaluate(payload(kernel="4.15.0")).ok
    low = evaluate(payload(disk_free_gb=1.2))
    assert not low.ok
    warn = evaluate(payload(disk_free_gb=6))
    assert warn.ok
    assert any(c.id == "disk" and c.level == "warn" for c in warn.checks)


def test_busy_port_is_a_warning_and_amnezia_is_protected():
    report = evaluate(
        payload(
            port_443_tcp_free=False,
            containers=[{"name": "amnezia-awg", "image": "amnezia-awg", "ports": "0.0.0.0:39743->39743/udp"}],
        )
    )
    assert report.ok
    assert "amnezia-awg" in report.protected
    assert any(c.id == "port443" and c.level == "warn" for c in report.checks)
