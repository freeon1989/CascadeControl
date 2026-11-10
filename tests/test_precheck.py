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


def test_supported_families_and_newer_versions():
    noble = evaluate(payload(os_version="26.04", os_pretty="Ubuntu 26.04 LTS", kernel="7.0.0-14-generic"))
    assert noble.ok
    assert any(c.id == "os" and c.level == "ok" and "Ubuntu 20.04" in c.hint and "CentOS Stream 9" in c.hint for c in noble.checks)
    assert any(c.id == "kernel" and c.level == "ok" for c in noble.checks)
    assert evaluate(payload(os_version="20.04", os_pretty="Ubuntu 20.04 LTS")).ok
    assert evaluate(payload(os_version="25.10", os_pretty="Ubuntu 25.10")).ok
    assert evaluate(payload(os_id="debian", os_version="11", os_pretty="Debian GNU/Linux 11")).ok
    assert evaluate(payload(os_id="debian", os_version="13", os_pretty="Debian GNU/Linux 13")).ok
    assert evaluate(payload(os_id="almalinux", os_version="8.10", os_pretty="AlmaLinux 8.10", kernel="4.18.0-553.el8_10.x86_64")).ok
    assert evaluate(payload(os_id="rocky", os_version="9.4", os_pretty="Rocky Linux 9.4")).ok
    assert evaluate(payload(os_id="centos", os_version="9", os_pretty="CentOS Stream 9")).ok
    assert evaluate(payload(os_id="fedora", os_version="42", os_pretty="Fedora Linux 42")).ok
    newer = evaluate(payload(os_version="28.04", os_pretty="Ubuntu 28.04 LTS"))
    assert newer.ok
    assert any(c.id == "os" and c.level == "warn" and "Fedora" in c.hint for c in newer.checks)
    assert evaluate(payload(os_id="debian", os_version="14", os_pretty="Debian GNU/Linux 14")).ok
    assert evaluate(payload(os_id="almalinux", os_version="10", os_pretty="AlmaLinux 10")).ok
    assert evaluate(payload(os_id="fedora", os_version="46", os_pretty="Fedora Linux 46")).ok
    assert any(c.level == "warn" for c in evaluate(payload(os_id="fedora", os_version="46")).checks if c.id == "os")
    foreign = evaluate(payload(os_id="ubuntu", os_version="24.04", kernel_name="FreeBSD"))
    assert not foreign.ok
    el8 = evaluate(payload(os_id="almalinux", os_version="8.10", kernel="4.18.0-553.el8_10.x86_64"))
    assert el8.ok
    assert any(c.id == "kernel" and c.level == "warn" for c in el8.checks)


def test_openvz_without_tun_blocks_and_with_tun_warns():
    assert not evaluate(payload(virt="openvz", tun=False)).ok
    with_tun = evaluate(payload(virt="lxc", tun=True))
    assert with_tun.ok
    assert any(c.id == "virt" and c.level == "warn" for c in with_tun.checks)


def test_old_os_kernel_and_disk():
    assert evaluate(payload(os_id="ubuntu", os_version="20.04")).ok
    assert not evaluate(payload(kernel="4.15.0")).ok
    low = evaluate(payload(disk_free_gb=1.2))
    assert not low.ok
    warn = evaluate(payload(disk_free_gb=6))
    assert warn.ok
    assert any(c.id == "disk" and c.level == "warn" for c in warn.checks)


def test_ubuntu_with_docker_and_busy_443_is_not_blocked():
    report = evaluate(
        payload(
            os_id="ubuntu",
            os_version="24.04.1",
            os_pretty="Ubuntu 24.04.1 LTS",
            docker=True,
            port_443_tcp_free=False,
            uid=0,
            sudo_nopass=True,
            containers=[
                {"name": "amnezia-awg", "image": "amneziavpn/amnezia-awg", "ports": "0.0.0.0:443->443/tcp", "status": "Up"},
                {"name": "amnezia-dns", "image": "amneziavpn/amnezia-dns", "ports": "", "status": "Up"},
            ],
        )
    )
    assert report.ok
    assert any(c.id == "port443" and c.level == "warn" and c.hint for c in report.checks)
    assert any(c.id == "docker" and c.level == "ok" for c in report.checks)
    assert any(c.id == "os" and c.level == "ok" for c in report.checks)
    assert "amnezia-awg" in report.protected
    debian = evaluate(payload(os_id="debian", os_version="12.5", docker=True, port_443_tcp_free=False, uid=0))
    assert debian.ok


def test_virt_detection_does_not_fail_closed_on_noise():
    assert evaluate(payload(virt="none\nunknown")).ok
    assert evaluate(payload(virt="oracle")).ok
    unknown = evaluate(payload(virt="customhv"))
    assert unknown.ok
    assert any(c.id == "virt" and c.level == "warn" and c.hint for c in unknown.checks)
    assert not evaluate(payload(virt="openvz")).ok
    assert not evaluate(payload(virt="lxc")).ok


def test_unreadable_disk_and_missing_sudo_are_warnings():
    disk = evaluate(payload(disk_free_gb=None))
    assert disk.ok
    assert any(c.id == "disk" and c.level == "warn" for c in disk.checks)
    rights = evaluate(payload(uid=1000, sudo_nopass=False))
    assert rights.ok
    assert any(c.id == "sudo" and c.level == "warn" and c.hint for c in rights.checks)
    root = evaluate(payload(uid=0, sudo_nopass=False))
    assert any(c.id == "sudo" and c.level == "ok" for c in root.checks)


def test_precheck_script_reads_os_and_does_not_prompt_sudo():
    from cascade_panel.precheck import PRECHECK_SCRIPT

    assert "os-release" in PRECHECK_SCRIPT
    assert '"sudo", "-n"' in PRECHECK_SCRIPT
    assert "|| echo unknown" not in PRECHECK_SCRIPT
    assert "veinfo" in PRECHECK_SCRIPT


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
