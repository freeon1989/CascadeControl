from cascade_panel.jump import jump_connect_error, resolve_ssh_plan, role_uses_jump, setting_enabled
from cascade_panel.ssh_client import SSHError, SSHTarget


def _t(host, port=22):
    return SSHTarget(host, port, "root", "password", "secret")


def test_jump_only_for_transit_and_exits():
    assert role_uses_jump("exit")
    assert role_uses_jump("transit")
    assert role_uses_jump("standby_exit")
    assert not role_uses_jump("entry")
    assert not role_uses_jump("standby_entry")
    assert not setting_enabled("")
    assert not setting_enabled("0")
    assert setting_enabled("1")


def test_plan_direct_by_default():
    target, jump = resolve_ssh_plan(_t("192.0.2.20"), role="exit", via_entry=False, entry=_t("192.0.2.10"))
    assert jump is None
    assert target.host == "192.0.2.20"


def test_plan_jumps_when_enabled():
    target, jump = resolve_ssh_plan(_t("192.0.2.20"), role="exit", via_entry=True, entry=_t("192.0.2.10"))
    assert jump is not None
    assert jump.host == "192.0.2.10"
    assert target.host == "192.0.2.20"


def test_missing_entry_error():
    try:
        resolve_ssh_plan(_t("192.0.2.20"), role="exit", via_entry=True, entry=None)
    except SSHError as exc:
        assert "входной сервер не задан" in str(exc)
    else:
        raise AssertionError("ожидали ошибку")


def test_same_host_stays_direct():
    target, jump = resolve_ssh_plan(_t("192.0.2.10"), role="exit", via_entry=True, entry=_t("192.0.2.10"))
    assert jump is None


def test_jump_error_text():
    assert "не отвечает" in str(jump_connect_error(RuntimeError("x"), entry_down=True))
