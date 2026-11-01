"""SSH-транспорт.

Скрипт передаётся в stdin ``sh``: не зависим от локальной оболочки
(PowerShell на Windows ломает ``|``, ``$`` и ``>``) и не требуем ``base64``
на роутере. Аргументы CLI экранируются ``shlex.quote``.

Соединение переиспользуется; доступ к каналу сериализован блокировкой.
"""

from __future__ import annotations

import base64
import logging
import os
import tempfile
import threading
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import paramiko

from .config import TargetConfig
from .errors import RemoteCommandError, SSHError

log = logging.getLogger(__name__)


@dataclass
class SSHResult:
    stdout: str
    stderr: str
    exit_code: int
    command: str = ""

    @property
    def ok(self) -> bool:
        return self.exit_code == 0

    def tail(self, limit: int = 1200) -> str:
        text = (self.stderr or self.stdout).strip()
        return text[-limit:]


class _AcceptNewPolicy(paramiko.MissingHostKeyPolicy):
    """Добавляет новый ключ хоста, но не прощает несовпадение известного."""

    def __init__(self, known_hosts: str | None) -> None:
        self.known_hosts = known_hosts

    def missing_host_key(self, client: paramiko.SSHClient, hostname: str, key: paramiko.PKey) -> None:
        client.get_host_keys().add(hostname, key.get_name(), key)
        if self.known_hosts:
            try:
                Path(self.known_hosts).parent.mkdir(parents=True, exist_ok=True)
                client.save_host_keys(self.known_hosts)
            except OSError:
                log.warning("Не удалось сохранить known_hosts %s", self.known_hosts)


class SSHSession:
    """Одно SSH-соединение с роутером."""

    def __init__(self, target: TargetConfig, *, max_output_bytes: int = 200_000) -> None:
        self.target = target
        self.max_output_bytes = max_output_bytes
        self._client: paramiko.SSHClient | None = None
        self._lock = threading.RLock()
        self._temp_key: str | None = None

    # ------------------------------------------------------------------ connect
    def _resolve_ssh_config(self) -> dict[str, object]:
        if not self.target.ssh_config_host:
            return {}
        config_path = Path(os.path.expanduser("~/.ssh/config"))
        if not config_path.exists():
            raise SSHError(f"SSH config не найден: {config_path}")
        try:
            config = paramiko.SSHConfig()
            with config_path.open(encoding="utf-8") as fh:
                config.parse(fh)
            return dict(config.lookup(self.target.ssh_config_host))
        except Exception as exc:  # paramiko бросает разные типы
            raise SSHError(f"Не удалось прочитать SSH config для {self.target.ssh_config_host}: {exc}") from exc

    def _load_pkey(self) -> paramiko.PKey | None:
        if self.target.key_b64:
            try:
                raw = base64.b64decode(self.target.key_b64, validate=True)
            except Exception as exc:
                raise SSHError("Некорректный base64 в ключе SSH") from exc
            fd, path = tempfile.mkstemp(prefix="podkop-mcp-key-", suffix=".pem")
            try:
                with os.fdopen(fd, "wb") as fh:
                    fh.write(raw)
                os.chmod(path, 0o600)
                self._temp_key = path
                return paramiko.PKey.from_path(path, password=self.target.key_passphrase)
            except Exception as exc:
                raise SSHError(f"Не удалось загрузить inline SSH-ключ: {exc}") from exc
            finally:
                # Файл нужен только на время загрузки ключа в память.
                if self._temp_key:
                    try:
                        os.unlink(self._temp_key)
                    except OSError:
                        pass
                    self._temp_key = None

        if self.target.key_path:
            path = Path(self.target.key_path)
            if not path.exists():
                raise SSHError(f"SSH-ключ не найден: {path}")
            try:
                return paramiko.PKey.from_path(str(path), password=self.target.key_passphrase)
            except Exception as exc:
                raise SSHError(f"Не удалось загрузить SSH-ключ {path}: {exc}") from exc
        return None

    def connect(self) -> None:
        with self._lock:
            if self._client is not None:
                transport = self._client.get_transport()
                if transport is not None and transport.is_active():
                    return
                self.close()

            cfg = self._resolve_ssh_config()
            host = str(cfg.get("hostname") or self.target.host or "")
            if not host:
                raise SSHError("Не удалось определить хост: нет host и SSH config host")
            user = str(cfg.get("user") or self.target.user)
            port = int(cfg.get("port") or self.target.port)
            identity = cfg.get("identityfile") or []
            if isinstance(identity, str):
                identity = [identity]
            if not self.target.key_path and not self.target.key_b64 and identity:
                self.target = TargetConfig(
                    **{
                        **self.target.__dict__,
                        "key_path": str(identity[0]),
                        "host": host,
                        "port": port,
                        "user": user,
                    }
                )

            client = paramiko.SSHClient()
            known_hosts = self.target.known_hosts or str(Path.home() / ".ssh" / "known_hosts")
            if Path(known_hosts).exists():
                try:
                    client.load_host_keys(known_hosts)
                except Exception as exc:
                    log.warning("Не удалось прочитать known_hosts %s: %s", known_hosts, exc)
            else:
                try:
                    client.load_system_host_keys()
                except Exception:
                    pass

            if self.target.host_key_policy == "ignore":
                client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            elif self.target.host_key_policy == "accept-new":
                client.set_missing_host_key_policy(_AcceptNewPolicy(known_hosts))
            else:
                client.set_missing_host_key_policy(paramiko.RejectPolicy())

            pkey = self._load_pkey()
            try:
                client.connect(
                    hostname=host,
                    port=port,
                    username=user,
                    pkey=pkey,
                    password=None if pkey else self.target.password,
                    timeout=self.target.connect_timeout,
                    banner_timeout=self.target.connect_timeout,
                    auth_timeout=self.target.connect_timeout,
                    allow_agent=not pkey and not self.target.password,
                    look_for_keys=not pkey and not self.target.password,
                )
            except paramiko.BadHostKeyException as exc:
                client.close()
                raise SSHError(
                    f"Ключ хоста {host} не совпадает с known_hosts. "
                    "Проверьте отпечаток или обновите known_hosts вручную."
                ) from exc
            except paramiko.AuthenticationException as exc:
                client.close()
                raise SSHError(
                    f"SSH-аутентификация не прошла для {user}@{host}. Проверьте ключ и ssh config."
                ) from exc
            except (paramiko.SSHException, OSError) as exc:
                client.close()
                raise SSHError(f"Не удалось подключиться к {user}@{host}:{port}: {exc}") from exc

            transport = client.get_transport()
            if transport is not None:
                transport.set_keepalive(30)
            self._client = client
            log.info("SSH подключён: %s@%s:%s", user, host, port)

    # ------------------------------------------------------------------ execute
    def _execute(self, script: str, timeout: float) -> SSHResult:
        assert self._client is not None
        try:
            # Скрипт уходит в stdin ``sh``: не нужен base64 на роутере и
            # исключены проблемы с кавычками/переводами строк в аргументах.
            stdin, stdout, stderr = self._client.exec_command("sh", timeout=timeout, get_pty=False)
            stdin.write(script.encode("utf-8"))
            stdin.flush()
            stdin.channel.shutdown_write()
            stdin.close()
            out = stdout.read()
            err = stderr.read()
            exit_code = stdout.channel.recv_exit_status()
        except TimeoutError as exc:
            raise SSHError(f"Команда на роутере не уложилась в {timeout:.0f} с") from exc
        except (paramiko.SSHException, EOFError, OSError) as exc:
            raise SSHError(f"Обрыв SSH при выполнении команды: {exc}") from exc

        text_out = out.decode("utf-8", "replace")
        text_err = err.decode("utf-8", "replace")
        if len(text_out) > self.max_output_bytes:
            text_out = text_out[: self.max_output_bytes] + "\n... [вывод обрезан]"
        if len(text_err) > self.max_output_bytes:
            text_err = text_err[-self.max_output_bytes :]
        return SSHResult(stdout=text_out, stderr=text_err, exit_code=exit_code, command=script[:400])

    def run(self, script: str, *, timeout: float | None = None, check: bool = False) -> SSHResult:
        """Выполняет shell-скрипт на роутере (скрипт передаётся в stdin ``sh``)."""

        with self._lock:
            self.connect()
            assert self._client is not None
            deadline = timeout or self.target.command_timeout
            attempts = 0
            while True:
                try:
                    result = self._execute(script, deadline)
                    break
                except SSHError:
                    attempts += 1
                    if attempts >= 2:
                        raise
                    log.warning("SSH-команда не удалась, переподключаюсь (попытка %s)", attempts)
                    self.close()
                    time.sleep(0.4)
                    self.connect()

            if check and not result.ok:
                raise RemoteCommandError(
                    f"Команда завершилась с кодом {result.exit_code}: {result.tail()}",
                    exit_code=result.exit_code,
                    stdout=result.stdout,
                    stderr=result.stderr,
                )
            return result

    def run_argv(self, program: str, args: Sequence[str], *, timeout: float | None = None, check: bool = False) -> SSHResult:
        """Запускает программу с аргументами без риска инъекций.

        Аргументы экранируются ``shlex.quote``; скрипт передаётся в stdin,
        поэтому на роутере не нужен base64.
        """

        if not program or any(ch in program for ch in " \t\n\r;|&$`'\"()<>\\"):
            raise SSHError(f"Недопустимое имя программы: {program!r}")
        import shlex

        parts = [program, *[shlex.quote(str(arg)) for arg in args]]
        return self.run(" ".join(parts), timeout=timeout, check=check)

    # ------------------------------------------------------------------ cleanup
    def close(self) -> None:
        with self._lock:
            if self._client is not None:
                try:
                    self._client.close()
                finally:
                    self._client = None

    def __enter__(self) -> SSHSession:
        self.connect()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()


class SSHManager:
    """Кэш SSH-сессий по имени target."""

    def __init__(self, targets: dict[str, TargetConfig], *, max_output_bytes: int = 200_000) -> None:
        self._targets = targets
        self._max_output_bytes = max_output_bytes
        self._sessions: dict[str, SSHSession] = {}
        self._lock = threading.Lock()

    def session(self, name: str) -> SSHSession:
        with self._lock:
            session = self._sessions.get(name)
            if session is None:
                target = self._targets[name]
                session = SSHSession(target, max_output_bytes=self._max_output_bytes)
                self._sessions[name] = session
            return session

    def close_all(self) -> None:
        with self._lock:
            for session in self._sessions.values():
                session.close()
            self._sessions.clear()
