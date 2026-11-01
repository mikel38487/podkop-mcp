"""Сборка зависимостей MCP-сервера."""

from __future__ import annotations

import logging
from threading import Lock

from .config import Settings, TargetConfig
from .errors import ConfigError
from .router import Router
from .ssh import SSHManager

log = logging.getLogger(__name__)


class Services:
    """Ленивый реестр роутеров и SSH-сессий."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.ssh = SSHManager(settings.targets, max_output_bytes=settings.max_output_bytes)
        self._routers: dict[str, Router] = {}
        self._lock = Lock()

    def target(self, name: str | None = None) -> TargetConfig:
        return self.settings.resolve(name)

    def router(self, name: str | None = None) -> Router:
        target = self.target(name)
        with self._lock:
            router = self._routers.get(target.name)
            if router is not None:
                return router
            session = self.ssh.session(target.name)
            router = Router(session, target, self.settings, name=target.name)
            self._routers[target.name] = router
            return router

    def doctor(self, name: str | None = None) -> dict:
        target = self.target(name)
        info = {
            "ok": False,
            "target": target.name,
            "target_config": target.public_dict(),
            "settings": self.settings.public_dict(),
            "checks": {},
        }
        try:
            router = self.router(target.name)
            ssh_result = router.session.run("echo 'ssh-ok'; uname -sr", timeout=15)
            info["checks"]["ssh"] = {
                "ok": ssh_result.ok,
                "output": ssh_result.stdout.strip(),
                "error": ssh_result.stderr.strip() or None,
            }
            if not ssh_result.ok:
                info["error"] = "SSH-команда завершилась с ошибкой"
                return info
            router.refresh_profile()
            profile = router.profile
            info["checks"]["backend"] = profile.public_dict()
            config = router.config()
            info["checks"]["config"] = {
                "package": config.package,
                "sections": len(config.sections),
                "top_sections": len(config.top_sections()),
            }
            info["read_only"] = self.settings.read_only
            info["ok"] = True
            return info
        except Exception as exc:
            info["error"] = str(exc)
            log.info("doctor для %s: %s", target.name, exc)
            return info

    def close(self) -> None:
        self.ssh.close_all()
        with self._lock:
            self._routers.clear()

    def require_targets(self) -> None:
        if not self.settings.targets:
            raise ConfigError(
                "Роутер не настроен. Задайте PODKOP_MCP_SSH_HOST или "
                "PODKOP_MCP_SSH_CONFIG_HOST в окружении MCP-сервера."
            )
