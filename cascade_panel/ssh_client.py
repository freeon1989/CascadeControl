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
    def __init__(self, target: SSHTarget, timeout: float = 25.0) -> None:
        self.target = target
        self.timeout = timeout
        self._client = None

    def __enter__(self) -> "ParamikoSession":
        import paramiko

        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        kwargs = {
            "hostname": self.target.host,
            "port": self.target.port,
            "username": self.target.user,
            "timeout": self.timeout,
            "allow_agent": False,
            "look_for_keys": False,
            "banner_timeout": self.timeout,
        }
        if self.target.auth_type == "key":
            kwargs["pkey"] = _load_pkey(self.target.secret)
        else:
            kwargs["password"] = self.target.secret
        try:
            client.connect(**kwargs)
        except Exception as exc:  # noqa: BLE001 — наружу уходит короткое сообщение без секрета
            raise SSHError(f"SSH: не удалось подключиться к {self.target.host}: {exc.__class__.__name__}") from exc
        self._client = client
        return self

    def __exit__(self, *_) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    def run(self, command: str, on_line: Callable[[str], None]) -> int:
        if self._client is None:
            raise SSHError("сессия закрыта")
        _stdin, stdout, stderr = self._client.exec_command(command, timeout=self.timeout)
        channel = stdout.channel
        while not channel.exit_status_ready() or channel.recv_ready() or channel.recv_stderr_ready():
            if channel.recv_ready():
                for line in stdout.readline().splitlines():
                    on_line(line)
            if channel.recv_stderr_ready():
                for line in stderr.readline().splitlines():
                    on_line(line)
            if channel.exit_status_ready() and not channel.recv_ready() and not channel.recv_stderr_ready():
                break
        # дочитываем хвост
        rest = stdout.read().decode("utf-8", errors="replace")
        err = stderr.read().decode("utf-8", errors="replace")
        for line in (rest + ("\n" + err if err else "")).splitlines():
            if line:
                on_line(line)
        return channel.recv_exit_status()

    def write_file(self, path: str, content: bytes, mode: int = 0o600) -> None:
        if self._client is None:
            raise SSHError("сессия закрыта")
        sftp = self._client.open_sftp()
        try:
            parent = path.rsplit("/", 1)[0]
            self.run(f"sudo mkdir -p {parent} && sudo chown {self.target.user} {parent}", lambda _line: None)
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

    def __init__(self, precheck_output: str = "", fail_connect: bool = False) -> None:
        self.precheck_output = precheck_output
        self.fail_connect = fail_connect
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
        if "cascade-agent" in command and "status" in command:
            on_line('{"healthy_exits":[]}')
            return 0
        if command.startswith("sudo rm") or "remove.sh" in command:
            on_line("CASCADE_LOG удалены только контейнеры cascade-*")
            return 0
        return 0

    def write_file(self, path: str, content: bytes, mode: int = 0o600) -> None:
        self.files[path] = content

    def read_file(self, path: str) -> bytes:
        if path not in self.files:
            raise SSHError(f"нет файла {path}")
        return self.files[path]
