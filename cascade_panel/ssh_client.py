"""SSH-сессия панели. Пароли и ключи не попадают в argv удалённых команд."""

from __future__ import annotations

import io
from collections.abc import Callable
from dataclasses import dataclass


class SSHError(RuntimeError):
    pass


@dataclass
class SSHTarget:
    host: str
    port: int
    user: str
    auth_type: str  # password | key
    secret: str


class ParamikoSession:
    def __init__(self, target: SSHTarget, timeout: float = 25.0, jump: SSHTarget | None = None) -> None:
        self.target = target
        self.timeout = timeout
        self.jump = jump
        self._client = None
        self._jump_client = None

    def __enter__(self) -> "ParamikoSession":
        sock = None
        if self.jump is not None:
            try:
                self._jump_client = _connect_client(self.jump, self.timeout)
                transport = self._jump_client.get_transport()
                if transport is None or not transport.is_active():
                    raise SSHError("SSH через входной узел недоступен: входной сервер не отвечает")
                sock = transport.open_channel(
                    "direct-tcpip",
                    (self.target.host, int(self.target.port)),
                    ("127.0.0.1", 0),
                    timeout=self.timeout,
                )
            except SSHError:
                self._close_jump()
                raise
            except Exception as exc:  # noqa: BLE001 — наружу короткое сообщение без секрета
                self._close_jump()
                raise SSHError("SSH через входной узел недоступен: входной сервер не отвечает") from exc
        try:
            self._client = _connect_client(self.target, self.timeout, sock=sock)
        except Exception:
            self._close_jump()
            raise
        return self

    def __exit__(self, *_) -> None:
        if self._client is not None:
            try:
                self._client.close()
            except Exception:  # noqa: BLE001
                pass
            self._client = None
        self._close_jump()

    def _close_jump(self) -> None:
        if self._jump_client is not None:
            try:
                self._jump_client.close()
            except Exception:  # noqa: BLE001
                pass
            self._jump_client = None

    def run(self, command: str, on_line: Callable[[str], None]) -> int:
        if self._client is None:
            raise SSHError("сессия закрыта")
        chan = self._client.get_transport().open_session()
        chan.settimeout(self.timeout)
        # LC_ALL не даёт удалённой стороне перекодировать журнал в cp866.
        chan.exec_command("export LC_ALL=C.UTF-8 LANG=C.UTF-8; " + command)
        stdout = chan.makefile("rb")
        stderr = chan.makefile_stderr("rb")
        while not chan.exit_status_ready() or chan.recv_ready() or chan.recv_stderr_ready():
            if chan.recv_ready():
                for line in _utf8_text(stdout.readline()).splitlines():
                    if line:
                        on_line(line)
            if chan.recv_stderr_ready():
                for line in _utf8_text(stderr.readline()).splitlines():
                    if line:
                        on_line(line)
            if chan.exit_status_ready() and not chan.recv_ready() and not chan.recv_stderr_ready():
                break
        rest = _utf8_text(stdout.read())
        err = _utf8_text(stderr.read())
        for line in (rest + ("\n" + err if err else "")).splitlines():
            if line:
                on_line(line)
        return chan.recv_exit_status()

    def write_file(self, path: str, content: bytes, mode: int = 0o600) -> None:
        if self._client is None:
            raise SSHError("сессия закрыта")
        sftp = self._client.open_sftp()
        try:
            parent = path.rsplit("/", 1)[0]
            prefix = "" if self.target.user == "root" else "sudo -n "
            self.run(
                f"{prefix}mkdir -p {parent} && {prefix}chown {self.target.user} {parent}",
                lambda _line: None,
            )
            with sftp.file(path, "wb") as remote:
                remote.write(content)
            sftp.chmod(path, mode)
        finally:
            sftp.close()

    def read_file(self, path: str) -> bytes:
        if self._client is None:
            raise SSHError("сессия закрыта")
        sftp = self._client.open_sftp()
        try:
            with sftp.file(path, "rb") as remote:
                return remote.read()
        finally:
            sftp.close()


def _utf8_text(chunk) -> str:
    """Журнал SSH всегда UTF-8. Если байты уже прочитали как cp866, чиним строку."""
    if isinstance(chunk, bytes):
        text = chunk.decode("utf-8", errors="replace")
    elif isinstance(chunk, str):
        text = chunk
    else:
        text = str(chunk)
    return _repair_cp866(text)


def _repair_cp866(text: str) -> str:
    try:
        raw = text.encode("cp866")
        fixed = raw.decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return text

    def cyr(value: str) -> int:
        return sum(1 for char in value if "\u0400" <= char <= "\u04ff")

    if cyr(fixed) > cyr(text):
        return fixed
    return text


def _connect_client(target: SSHTarget, timeout: float, sock=None):
    """Открывает SSH-клиент. sock — канал direct-tcpip через входной узел."""
    import paramiko

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    kwargs = {
        "hostname": target.host,
        "port": target.port,
        "username": target.user,
        "timeout": timeout,
        "allow_agent": False,
        "look_for_keys": False,
        "banner_timeout": timeout,
        "auth_timeout": timeout,
    }
    if sock is not None:
        kwargs["sock"] = sock
    try:
        if target.auth_type == "key":
            kwargs["pkey"] = _load_pkey(target.secret)
            client.connect(**kwargs)
        else:
            client.connect(**kwargs, password=target.secret.strip())
    except Exception as exc:  # noqa: BLE001 — наружу уходит короткое сообщение без секрета
        auth_failed = exc.__class__.__name__ == "AuthenticationException"
        if target.auth_type != "key" and auth_failed:
            try:
                client.close()
            except Exception:  # noqa: BLE001
                pass
            try:
                client = _connect_password_interactive(target, timeout, sock=sock)
            except Exception as second:  # noqa: BLE001
                raise SSHError(
                    f"SSH: не удалось подключиться к {target.host}: {second.__class__.__name__}"
                ) from second
        else:
            raise SSHError(f"SSH: не удалось подключиться к {target.host}: {exc.__class__.__name__}") from exc
    return client


def _connect_password_interactive(target: SSHTarget, timeout: float, sock=None):
    """Пароль через keyboard-interactive, если сервер не принимает auth password."""
    import socket

    import paramiko

    password = target.secret.strip()
    if sock is None:
        sock = socket.create_connection((target.host, int(target.port)), timeout)
    transport = paramiko.Transport(sock)
    transport.banner_timeout = timeout
    try:
        transport.start_client(timeout=timeout)

        def handler(_title, _instructions, prompts):
            return [password for _prompt in prompts]

        try:
            transport.auth_password(target.user, password)
        except paramiko.AuthenticationException:
            transport.auth_interactive(target.user, handler)
        if not transport.is_authenticated():
            raise paramiko.AuthenticationException("password")
    except Exception:
        transport.close()
        raise
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client._transport = transport
    return client


def _load_pkey(secret: str):
    import paramiko

    buffer = io.StringIO(secret)
    errors = []
    for loader in (
        paramiko.Ed25519Key.from_private_key,
        paramiko.RSAKey.from_private_key,
        paramiko.ECDSAKey.from_private_key,
    ):
        buffer.seek(0)
        try:
            return loader(buffer)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc.__class__.__name__)
    raise SSHError("личный ключ SSH не разобран (" + ", ".join(errors) + ")")


class FakeSession:
    """Подмена SSH в тестах и в сухом прогоне."""

    def __init__(
        self,
        precheck_output: str = "",
        fail_connect: bool = False,
        stats_output: str = "",
        jump: SSHTarget | None = None,
        warp_output: str = "",
        fail_warp: bool = False,
    ) -> None:
        self.precheck_output = precheck_output
        self.fail_connect = fail_connect
        self.stats_output = stats_output
        self.jump = jump
        self.warp_output = warp_output
        self.fail_warp = fail_warp
        self.files: dict[str, bytes] = {}
        self.commands: list[str] = []

    def __enter__(self) -> "FakeSession":
        if self.fail_connect:
            raise SSHError("SSH: не удалось подключиться")
        return self

    def __exit__(self, *_) -> None:
        return None

    def run(self, command: str, on_line: Callable[[str], None]) -> int:
        self.commands.append(command)
        if "stats.sh" in command:
            text = self.stats_output or (
                'CASCADE_STATS {"cpu":1,"ram":"0.1 / 1 ГБ","disk":"1 / 10 ГБ","uptime":"1 ч",'
                '"docker":"missing","agent":"missing","xray":"missing","awg":null}'
            )
            for line in text.splitlines():
                on_line(line)
            return 0 if "CASCADE_STATS" in text else 1
        if "precheck.sh" in command:
            for line in self.precheck_output.splitlines():
                on_line(line)
            return 0 if "CASCADE_JSON" in self.precheck_output else 1
        if "install.sh" in command:
            on_line("CASCADE_LOG подготовка каталогов")
            on_line("CASCADE_LOG docker готов")
            on_line("CASCADE_LOG контейнер cascade-awg запущен")
            on_line("CASCADE_LOG xray запущен")
            on_line("CASCADE_LOG агент включён")
            on_line("CASCADE_LOG проверка портов: ok")
            return 0
        if "sync.sh" in command or "syncconf" in command:
            on_line("CASCADE_LOG пиры применены в контейнере, интерфейс не перезапускался")
            return 0
        if "cascade-xray" in command:
            on_line("CASCADE_LOG xray перечитал конфиг")
            return 0
        if "cascade-agent" in command and "status" in command:
            on_line('{"healthy_exits":[]}')
            return 0
        if command.startswith("sudo rm") or "remove.sh" in command:
            on_line("CASCADE_LOG удалены только контейнеры cascade-*")
            return 0
        if "warp/enable.sh" in command or "warp/disable.sh" in command:
            if self.fail_warp:
                on_line("CASCADE_ERR не удалось подключить Cloudflare WARP")
                return 1
            if self.warp_output:
                for line in self.warp_output.splitlines():
                    on_line(line)
                return 0
            if "warp/disable.sh" in command:
                on_line("CASCADE_LOG WARP выключен")
                on_line(
                    'CASCADE_WARP_JSON {"enabled":false,"connected":false,"egress_ip":"","direct_ip":"","warp":"off","status":"выключен"}'
                )
            else:
                on_line("CASCADE_LOG WARP включён, выход 203.0.113.50")
                on_line(
                    'CASCADE_WARP_JSON {"enabled":true,"connected":true,"egress_ip":"203.0.113.50","direct_ip":"203.0.113.10","warp":"on","status":"подключён"}'
                )
            return 0
        if "warp-cli" in command and "status" in command:
            on_line("Status update: Connected")
            return 0
        return 0

    def write_file(self, path: str, content: bytes, mode: int = 0o600) -> None:
        self.files[path] = content

    def read_file(self, path: str) -> bytes:
        if path not in self.files:
            raise SSHError(f"нет файла {path}")
        return self.files[path]
