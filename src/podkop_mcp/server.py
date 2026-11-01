"""MCP-сервер podkop-mcp."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from mcp.server import MCPServer

from . import __version__
from .config import Settings, load_settings
from .services import Services
from .tools import config, connections, domains, lists, monitor, sections, service, system

log = logging.getLogger(__name__)

INSTRUCTIONS = """
podkop-mcp управляет Podkop и Forkop на OpenWrt-роутере по SSH.

Типичный порядок работы:
1. podkop_doctor — проверить SSH, бэкенд и версию.
2. podkop_status / podkop_sections_list — понять текущее состояние.
3. Изменяющие инструменты по умолчанию работают в dry-run и возвращают diff.
4. Повторите вызов с apply=true, чтобы записать конфиг (бэкап и reload выполняются автоматически).
5. podkop_domains_add/remove — основной способ менять домены без ловушек UCI.
6. podkop_monitor_snapshot / podkop_connections_live / podkop_traffic — мониторинг.

Секреты (URL подписок, proxy_string, ключи) маскируются по умолчанию.
""".strip()


def create_server(settings: Settings | None = None) -> MCPServer:
    settings = settings or load_settings()
    services = Services(settings)

    @asynccontextmanager
    async def lifespan(_server: MCPServer):
        try:
            yield services
        finally:
            services.close()

    mcp = MCPServer(
        "podkop-mcp",
        title="Podkop / Forkop MCP",
        description="Manage Podkop and Forkop on OpenWrt over SSH",
        instructions=INSTRUCTIONS,
        version=__version__,
        log_level=settings.log_level if settings.log_level in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"} else "INFO",
        lifespan=lifespan,
    )

    for module in (system, config, sections, domains, lists, connections, monitor, service):
        module.register(mcp, services)
    return mcp
