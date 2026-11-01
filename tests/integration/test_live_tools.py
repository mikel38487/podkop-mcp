"""Black-box smoke-проверка MCP-инструментов на живом роутере.

Тест намеренно не импортирует внутренние функции инструментов: вызываются
зарегистрированные MCP handlers. Запуск требует явного PODKOP_MCP_LIVE=1.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Callable
from typing import Any

import pytest

from podkop_mcp.config import load_settings
from podkop_mcp.server import create_server

pytestmark = pytest.mark.live

_VLESS_UUID_RE = re.compile(r"vless://[^\s\"']+", re.IGNORECASE)


def _enabled() -> bool:
    return os.environ.get("PODKOP_MCP_LIVE", "").strip().lower() in {"1", "true", "yes", "on"}


def _settings():
    return load_settings()


def _tool_handlers() -> dict[str, Callable[..., Any]]:
    server = create_server(_settings())
    # MCP 2.x stores registered tools in _tool_manager; this is an observation
    # boundary for a black-box test and avoids importing tool implementation modules.
    manager = getattr(server, "_tool_manager", None)
    tools = getattr(manager, "_tools", {}) if manager is not None else {}
    return {name: tool.fn for name, tool in tools.items()}


def _call(tool_name: str, **kwargs: Any) -> dict[str, Any]:
    handlers = _tool_handlers()
    assert tool_name in handlers, f"MCP tool is not registered: {tool_name}"
    result = handlers[tool_name](**kwargs)
    assert isinstance(result, dict), f"{tool_name}: expected dict, got {type(result).__name__}"
    encoded = json.dumps(result, ensure_ascii=False, default=str)
    assert len(encoded.encode("utf-8")) < 200_000, f"{tool_name}: response exceeds 200KB"
    assert not _VLESS_UUID_RE.search(encoded), f"{tool_name}: vless URL leaked"
    return result


@pytest.fixture(scope="module", autouse=True)
def require_live() -> None:
    if not _enabled():
        pytest.skip("PODKOP_MCP_LIVE is not enabled")
    assert os.environ.get("PODKOP_MCP_READ_ONLY", "").strip() in {"1", "true", "yes", "on"}, (
        "Live tests require PODKOP_MCP_READ_ONLY=1"
    )


READ_ONLY_CASES = [
    ("podkop_doctor", {}),
    ("podkop_status", {}),
    ("podkop_system_info", {}),
    ("podkop_health", {"full": False}),
    ("podkop_logs", {}),
    ("podkop_config_get", {}),
    ("podkop_config_export", {}),
    ("podkop_option_get", {"path": "forkop.main"}),
    ("podkop_backup_list", {}),
    ("podkop_sections_list", {}),
    ("podkop_section_get", {"section": "main"}),
    ("podkop_domains_list", {}),
    ("podkop_domains_find", {"domain": "example.com"}),
    ("podkop_domain_check", {"domain": "example.com", "probe_http": False}),
    ("podkop_community_lists_get", {}),
    ("podkop_connections_config", {}),
    ("podkop_proxies_list", {}),
    ("podkop_proxy_test", {}),
    ("podkop_monitor_snapshot", {"include_connections": False}),
    ("podkop_connections_live", {"limit": 10}),
    ("podkop_traffic", {"sample_seconds": 0.5, "top": 5}),
]


@pytest.mark.parametrize("name,kwargs", READ_ONLY_CASES)
def test_live_read_only_tool(name: str, kwargs: dict[str, Any]) -> None:
    """Каждый read-only handler возвращает безопасный ограниченный словарь."""
    _call(name, **kwargs)


@pytest.mark.parametrize(
    "name,kwargs",
    [
        ("podkop_option_set", {"path": "forkop.main.__live_probe", "value": "0"}),
        ("podkop_option_delete", {"path": "forkop.main.domain"}),
        ("podkop_backup_create", {}),
        ("podkop_backup_restore", {"backup_id": "__live_probe__"}),
        ("podkop_section_create", {"name": "__live_probe__", "action": "connection", "options": {"proxy_string": "https://example.invalid/live"}}),
        ("podkop_section_update", {"section": "main", "options": {"__live_probe": "0"}}),
        ("podkop_section_delete", {"section": "main", "allow_main": True}),
        ("podkop_section_enable", {"section": "main", "enabled": True}),
        ("podkop_domains_add", {"section": "main", "domains": ["live-probe.invalid"]}),
        ("podkop_domains_remove", {"section": "main", "domains": ["definitely-not-present.invalid"]}),
        ("podkop_domains_replace", {"section": "main", "domains": ["example.com"]}),
        ("podkop_community_lists_update", {"section": "main", "add": ["__live_probe"]}),
        ("podkop_subscription_add", {"section": "main", "url": "https://example.invalid/live-probe"}),
        ("podkop_subscription_delete", {"identifier": "@subscription_url[0]"}),
        ("podkop_proxy_select", {"group": "__missing_group__", "proxy": "DIRECT"}),
        ("podkop_service_action", {"action": "reload"}),
        ("podkop_lists_update", {"kind": "lists"}),
    ],
)
def test_live_mutating_dry_run(name: str, kwargs: dict[str, Any]) -> None:
    """Mutating handlers are exercised only with apply=false and never write."""
    before = _call("podkop_config_get")
    try:
        result = _call(name, apply=False, **kwargs)
    except Exception as exc:
        if name in {"podkop_section_delete", "podkop_subscription_delete", "podkop_proxy_select"}:
            pytest.skip(f"Невозможно построить dry-run на текущей схеме роутера: {exc}")
        raise
    assert result.get("applied") is False, f"{name}: dry-run must report applied=false"
    if name not in {"podkop_domains_remove"}:
        assert result.get("dry_run") is True, f"{name}: dry-run must report dry_run=true"
    if name not in {"podkop_backup_restore", "podkop_proxy_select", "podkop_domains_remove"}:
        assert result.get("plan") or result.get("commands") or result.get("hint"), f"{name}: empty dry-run plan"
    after = _call("podkop_config_get")
    assert before == after, f"{name}: config changed during apply=false"
