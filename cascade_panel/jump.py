"""План SSH: прямое подключение или прыжок через входной узел (как ProxyJump)."""

from __future__ import annotations

from .ssh_client import SSHError, SSHTarget

# Вход (и запасной вход) всегда напрямую. Прыжок — только к транзиту и выходам.
JUMP_ROLES = {"transit", "exit", "standby_exit"}
SSH_VIA_ENTRY_KEY = "ssh_via_entry"


def role_uses_jump(role: str) -> bool:
    return role in JUMP_ROLES


def setting_enabled(value: str | None) -> bool:
    return (value or "").strip() in {"1", "true", "yes", "on"}


def same_endpoint(left: SSHTarget, right: SSHTarget) -> bool:
    return left.host == right.host and int(left.port) == int(right.port)


def resolve_ssh_plan(
    target: SSHTarget,
    *,
    role: str,
    via_entry: bool,
    entry: SSHTarget | None,
) -> tuple[SSHTarget, SSHTarget | None]:
    """Возвращает (цель, прыжок или None). Не открывает соединение."""
    if not via_entry or not role_uses_jump(role):
        return target, None
    if entry is None:
        raise SSHError("SSH через входной узел недоступен: входной сервер не задан")
    if same_endpoint(entry, target):
        return target, None
    return target, entry


def jump_connect_error(exc: BaseException, *, entry_down: bool = False) -> SSHError:
    if entry_down:
        return SSHError("SSH через входной узел недоступен: входной сервер не отвечает")
    return SSHError(f"SSH через входной узел недоступен: {exc.__class__.__name__}")
