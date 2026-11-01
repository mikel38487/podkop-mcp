"""Ошибки домена podkop-mcp.

Все предсказуемые ошибки наследуются от :class:`PodkopError`; слой MCP
преобразует их в ``ToolError`` с понятным сообщением.
"""

from __future__ import annotations


class PodkopError(Exception):
    """Базовая ошибка сервера."""


class ConfigError(PodkopError):
    """Неверная или неполная конфигурация MCP-сервера."""


class BackendError(PodkopError):
    """Бэкенд Podkop/Forkop не найден или не поддерживает операцию."""


class SafetyError(PodkopError):
    """Операция запрещена политикой безопасности."""


class RemoteCommandError(PodkopError):
    """Команда на роутере завершилась с ненулевым кодом."""

    def __init__(self, message: str, *, exit_code: int, stdout: str = "", stderr: str = "") -> None:
        self.exit_code = exit_code
        self.stdout = stdout
        self.stderr = stderr
        super().__init__(message)


class SSHError(PodkopError):
    """Ошибка подключения или выполнения по SSH."""
