"""Интеграционный тест против OpenWrt в Docker (реальный SSH + ubus + uci).

Запуск:
    .\\scripts\\openwrt-docker.ps1
    . .\\.tmp\\openwrt-env.ps1
    .\\.venv\\Scripts\\python.exe -m pytest -q -m docker_ssh

Контейнер использует ядро хоста и stub forkop CLI/init, но транспорт SSH,
`ubus call uci get`, `uci` и backup/apply — настоящие. Это не VM; см.
docs/testing.md.
"""

from __future__ import annotations

import json
import os
from typing import Any

import pytest

from podkop_mcp.server import create_server

pytestmark = pytest.mark.docker_ssh


def _enabled() -> bool:
    return os.environ.get("PODKOP_MCP_DOCKER_SSH", "").strip() in {"1", "true", "yes"}


@pytest.fixture(scope="module")
def server():
    if not _enabled():
        pytest.skip("docker_ssh: запустите scripts/openwrt-docker.ps1 и source .tmp/openwrt-env.ps1")
    return create_server()


def _call(server: Any, name: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
    async def run() -> dict[str, Any]:
        result = await server.call_tool(name, args or {})
        if getattr(result, "structured_content", None):
            return result.structured_content
        parts = [getattr(item, "text", str(item)) for item in (getattr(result, "content", []) or [])]
        return json.loads("\n".join(parts))

    import asyncio

    return asyncio.run(run())


def test_doctor_and_real_ubus(server: Any) -> None:
    doctor = _call(server, "podkop_doctor")
    assert doctor["ok"] is True
    assert doctor["checks"]["backend"]["schema"] == "forkop1"
    assert doctor["checks"]["config"]["top_sections"] >= 2

    # ubus-путь проверяется тем, что config прочитан; uci export fallback
    # тоже допустим, но на этом образе rpcd установлен и ubus доступен.
    config = _call(server, "podkop_config_get")
    assert "main" in config["sections"]
    assert config["sections"]["main"]["domain"] == "example.com hornet.com"


def test_apply_add_and_remove_domain(server: Any) -> None:
    before = _call(server, "podkop_domains_list", {"section": "main"})["entries"]
    assert "mcp-docker-test.example" not in before

    added = _call(
        server,
        "podkop_domains_add",
        {"section": "main", "domains": ["mcp-docker-test.example"], "apply": True, "verify": False},
    )
    assert added["applied"] is True
    assert added["backup_id"]

    after = _call(server, "podkop_domains_list", {"section": "main"})["entries"]
    assert "mcp-docker-test.example" in after

    backups = _call(server, "podkop_backup_list")["items"]
    assert any(item["id"] == added["backup_id"] for item in backups)

    removed = _call(
        server,
        "podkop_domains_remove",
        {"section": "main", "domains": ["mcp-docker-test.example"], "apply": True},
    )
    assert removed["applied"] is True
    final = _call(server, "podkop_domains_list", {"section": "main"})["entries"]
    assert "mcp-docker-test.example" not in final


def test_apply_option_and_restore(server: Any) -> None:
    original = _call(server, "podkop_option_get", {"path": "forkop.settings.log_level"})["value"]
    try:
        changed = _call(
            server,
            "podkop_option_set",
            {"path": "forkop.settings.log_level", "value": "debug", "apply": True},
        )
        assert changed["applied"] is True
        assert _call(server, "podkop_option_get", {"path": "forkop.settings.log_level"})["value"] == "debug"
    finally:
        restored = _call(
            server,
            "podkop_option_set",
            {"path": "forkop.settings.log_level", "value": original, "apply": True},
        )
        assert restored["applied"] is True
        assert _call(server, "podkop_option_get", {"path": "forkop.settings.log_level"})["value"] == original
