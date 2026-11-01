"""Метаданные MCP-сервера.

Версия в метаданных должна совпадать с версией пакета, чтобы они не могли
разойтись.
"""

from __future__ import annotations

import podkop_mcp
from podkop_mcp.server import create_server


def test_server_advertises_package_version(settings) -> None:
    server = create_server(settings)

    assert server.version == podkop_mcp.__version__
