"""Конфигурация MCP-сервера.

Поддерживаются два способа задать роутер:

1. Переменные окружения для одного target (быстрый старт).
2. JSON-файл или JSON-строка ``PODKOP_MCP_TARGETS`` для нескольких роутеров.

Секреты (ключи, пароли) никогда не попадают в логи и в ответы инструментов.
"""

from __future__ import annotations

import base64
import json
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from .errors import ConfigError

HostKeyPolicy = Literal["reject", "accept-new", "ignore"]


def _as_bool(value: str | bool | None, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _as_int(value: str | int | None, default: int) -> int:
    if value is None or value == "":
        return default
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"Ожидалось целое число, получено: {value!r}") from exc


def _as_float(value: str | float | None, default: float) -> float:
    if value is None or value == "":
        return default
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"Ожидалось число, получено: {value!r}") from exc


def _expand(path: str | None) -> str | None:
    if not path:
        return None
    return str(Path(os.path.expandvars(path)).expanduser())


@dataclass(frozen=True)
class TargetConfig:
    """Параметры одного роутера."""

    name: str = "router"
    host: str | None = None
    port: int = 22
    user: str = "root"
    key_path: str | None = None
    key_b64: str | None = None
    key_passphrase: str | None = None
    password: str | None = None
    ssh_config_host: str | None = None
    known_hosts: str | None = None
    host_key_policy: HostKeyPolicy = "reject"
    connect_timeout: float = 8.0
    command_timeout: float = 45.0
    backend: str = "auto"

    def __post_init__(self) -> None:
        if not self.host and not self.ssh_config_host:
            raise ConfigError(
                f"Target {self.name!r}: укажите PODKOP_MCP_SSH_HOST или PODKOP_MCP_SSH_CONFIG_HOST"
            )
        if self.host_key_policy not in {"reject", "accept-new", "ignore"}:
            raise ConfigError(
                f"Target {self.name!r}: host_key_policy должен быть reject|accept-new|ignore"
            )
        if self.port < 1 or self.port > 65535:
            raise ConfigError(f"Target {self.name!r}: неверный порт {self.port}")

    @property
    def display_host(self) -> str:
        host = self.ssh_config_host or self.host or "?"
        if self.ssh_config_host and self.host:
            return f"{self.ssh_config_host} ({self.host})"
        return host

    def public_dict(self) -> dict[str, Any]:
        """Безопасное представление для инструментов и логов."""
        return {
            "name": self.name,
            "host": self.display_host,
            "port": self.port,
            "user": self.user,
            "key_path": self.key_path,
            "has_inline_key": bool(self.key_b64),
            "ssh_config_host": self.ssh_config_host,
            "known_hosts": self.known_hosts,
            "host_key_policy": self.host_key_policy,
            "connect_timeout": self.connect_timeout,
            "command_timeout": self.command_timeout,
            "backend": self.backend,
        }


@dataclass
class Settings:
    targets: dict[str, TargetConfig] = field(default_factory=dict)
    default_target: str | None = None
    read_only: bool = False
    backup_dir: str = "/etc/podkop-mcp/backups"
    backup_keep: int = 30
    max_output_bytes: int = 200_000
    log_level: str = "INFO"
    profile_ttl: float = 30.0

    def resolve(self, name: str | None = None) -> TargetConfig:
        if not self.targets:
            raise ConfigError(
                "Роутер не настроен. Задайте PODKOP_MCP_SSH_HOST "
                "или PODKOP_MCP_SSH_CONFIG_HOST (см. README)."
            )
        key = name or self.default_target or next(iter(self.targets))
        if key not in self.targets:
            known = ", ".join(sorted(self.targets))
            raise ConfigError(f"Неизвестный target {key!r}. Доступные: {known}")
        return self.targets[key]

    def public_dict(self) -> dict[str, Any]:
        return {
            "targets": {name: target.public_dict() for name, target in self.targets.items()},
            "default_target": self.default_target,
            "read_only": self.read_only,
            "backup_dir": self.backup_dir,
            "backup_keep": self.backup_keep,
            "max_output_bytes": self.max_output_bytes,
            "profile_ttl": self.profile_ttl,
        }


def _target_from_mapping(name: str, data: Mapping[str, Any]) -> TargetConfig:
    allowed = {
        "host",
        "port",
        "user",
        "key_path",
        "key_b64",
        "key_passphrase",
        "password",
        "ssh_config_host",
        "known_hosts",
        "host_key_policy",
        "connect_timeout",
        "command_timeout",
        "backend",
    }
    unknown = set(data) - allowed
    if unknown:
        raise ConfigError(f"Target {name!r}: неизвестные поля {sorted(unknown)}")

    key_path = data.get("key_path")
    key_b64 = data.get("key_b64")
    if key_b64:
        # Позволяет передать ключ одной строкой в JSON без экранирования переводов строк.
        try:
            base64.b64decode(key_b64, validate=True)
        except Exception as exc:
            raise ConfigError(f"Target {name!r}: key_b64 не является валидным base64") from exc

    return TargetConfig(
        name=name,
        host=data.get("host"),
        port=_as_int(data.get("port"), 22),
        user=str(data.get("user") or "root"),
        key_path=_expand(key_path),
        key_b64=key_b64,
        key_passphrase=data.get("key_passphrase"),
        password=data.get("password"),
        ssh_config_host=data.get("ssh_config_host"),
        known_hosts=_expand(data.get("known_hosts")),
        host_key_policy=str(data.get("host_key_policy") or "reject"),  # type: ignore[arg-type]
        connect_timeout=_as_float(data.get("connect_timeout"), 8.0),
        command_timeout=_as_float(data.get("command_timeout"), 45.0),
        backend=str(data.get("backend") or "auto"),
    )


def _load_targets_json(raw: str, source: str) -> tuple[dict[str, TargetConfig], str | None]:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{source}: некорректный JSON ({exc})") from exc
    if not isinstance(payload, dict):
        raise ConfigError(f"{source}: ожидался JSON-объект")

    targets_raw = payload.get("targets", payload)
    if not isinstance(targets_raw, dict) or not targets_raw:
        raise ConfigError(f"{source}: нет ни одного target")
    targets = {name: _target_from_mapping(name, data) for name, data in targets_raw.items()}
    default = payload.get("default_target")
    if default is not None and default not in targets:
        raise ConfigError(f"{source}: default_target {default!r} отсутствует в targets")
    return targets, default


def load_settings(environ: Mapping[str, str] | None = None) -> Settings:
    """Собирает :class:`Settings` из окружения или JSON-файла.

    Пустая конфигурация допустима: сервер поднимется, а ``podkop_doctor``
    объяснит, чего не хватает. Это удобнее, чем падение stdio-сервера.
    """

    env = dict(os.environ if environ is None else environ)
    settings = Settings(
        read_only=_as_bool(env.get("PODKOP_MCP_READ_ONLY")),
        backup_dir=env.get("PODKOP_MCP_BACKUP_DIR", "/etc/podkop-mcp/backups"),
        backup_keep=_as_int(env.get("PODKOP_MCP_BACKUP_KEEP"), 30),
        max_output_bytes=_as_int(env.get("PODKOP_MCP_MAX_OUTPUT_BYTES"), 200_000),
        log_level=env.get("PODKOP_MCP_LOG_LEVEL", "INFO").upper(),
        profile_ttl=max(5.0, min(_as_float(env.get("PODKOP_MCP_PROFILE_TTL"), 30.0), 3600.0)),
    )

    config_path = env.get("PODKOP_MCP_CONFIG")
    targets_json = env.get("PODKOP_MCP_TARGETS")
    if config_path:
        path = Path(config_path).expanduser()
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise ConfigError(f"Не удалось прочитать PODKOP_MCP_CONFIG={path}: {exc}") from exc
        targets, default = _load_targets_json(raw, str(path))
        settings.targets = targets
        settings.default_target = default
    elif targets_json:
        targets, default = _load_targets_json(targets_json, "PODKOP_MCP_TARGETS")
        settings.targets = targets
        settings.default_target = default

    if not settings.targets:
        host = env.get("PODKOP_MCP_SSH_HOST")
        ssh_config_host = env.get("PODKOP_MCP_SSH_CONFIG_HOST")
        if host or ssh_config_host:
            name = env.get("PODKOP_MCP_TARGET_NAME", "router")
            key_path = env.get("PODKOP_MCP_SSH_KEY_PATH")
            key_inline = env.get("PODKOP_MCP_SSH_KEY")
            key_b64 = env.get("PODKOP_MCP_SSH_KEY_B64")
            if key_inline and not key_b64:
                key_b64 = base64.b64encode(key_inline.encode("utf-8")).decode("ascii")
            settings.targets[name] = TargetConfig(
                name=name,
                host=host,
                port=_as_int(env.get("PODKOP_MCP_SSH_PORT"), 22),
                user=env.get("PODKOP_MCP_SSH_USER", "root"),
                key_path=_expand(key_path),
                key_b64=key_b64,
                key_passphrase=env.get("PODKOP_MCP_SSH_KEY_PASSPHRASE"),
                password=env.get("PODKOP_MCP_SSH_PASSWORD"),
                ssh_config_host=ssh_config_host,
                known_hosts=_expand(
                    env.get("PODKOP_MCP_KNOWN_HOSTS") or str(Path.home() / ".ssh" / "known_hosts")
                ),
                host_key_policy=env.get("PODKOP_MCP_HOST_KEY_POLICY", "reject").lower(),  # type: ignore[arg-type]
                connect_timeout=_as_float(env.get("PODKOP_MCP_CONNECT_TIMEOUT"), 8.0),
                command_timeout=_as_float(env.get("PODKOP_MCP_COMMAND_TIMEOUT"), 45.0),
                backend=env.get("PODKOP_MCP_BACKEND", "auto"),
            )
            settings.default_target = name

    if settings.default_target is None and settings.targets:
        settings.default_target = next(iter(settings.targets))
    if settings.default_target and settings.default_target not in settings.targets:
        raise ConfigError(
            f"default_target {settings.default_target!r} отсутствует в targets"
        )
    import re

    if not re.fullmatch(r"/[A-Za-z0-9_./-]+", settings.backup_dir) or ".." in settings.backup_dir:
        raise ConfigError(
            f"PODKOP_MCP_BACKUP_DIR должен быть абсолютным путём без '..': {settings.backup_dir!r}"
        )
    return settings
