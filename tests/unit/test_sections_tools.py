"""Тесты секций Forkop 2.0: action allowlist, tunnel/child dry-run."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from fakes import FakeRouter, make_forkop2_profile
from mcp.server import MCPServer

from podkop_mcp.tools import sections as sections_mod
from podkop_mcp.uci import UciConfig, UciSection


class FakeServices:
    def __init__(self, router: FakeRouter) -> None:
        self._router = router

    def require_targets(self) -> None:
        return None

    def router(self, name: str | None = None) -> FakeRouter:
        return self._router


def make_mcp(router: FakeRouter) -> MCPServer:
    mcp = MCPServer("test")
    sections_mod.register(mcp, FakeServices(router))  # type: ignore[arg-type]
    return mcp


def call(mcp: MCPServer, name: str, args: dict[str, Any]) -> dict[str, Any]:
    async def _run() -> Any:
        try:
            return await mcp.call_tool(name, args)
        except Exception as exc:  # noqa: BLE001
            return {"__tool_error__": str(exc)}

    result = asyncio.run(_run())
    if isinstance(result, dict):
        return result
    if getattr(result, "is_error", False):
        return {"__tool_error__": result.content[0].text if result.content else ""}
    structured = getattr(result, "structured_content", None)
    if structured is not None:
        return structured
    return json.loads(result.content[0].text)


def _router() -> FakeRouter:
    config = UciConfig(package="forkop")
    config.sections["main"] = UciSection(name="main", type="section", options={"enabled": "1", "action": "connection"})
    return FakeRouter(profile=make_forkop2_profile(), config=config)


def test_forkop2_rejects_old_action() -> None:
    router = _router()
    result = call(make_mcp(router), "podkop_section_create", {"name": "legacy", "action": "zapret2"})
    assert "__tool_error__" in result
    assert "action" in result["__tool_error__"]


def test_forkop2_section_update_rejects_old_action() -> None:
    router = _router()
    result = call(make_mcp(router), "podkop_section_update", {"section": "main", "options": {"action": "byedpi"}})
    assert "__tool_error__" in result
    assert "action" in result["__tool_error__"]


def test_tunnel_create_dry_run_sets_kind_and_parent() -> None:
    router = _router()
    result = call(
        make_mcp(router),
        "podkop_tunnel_create",
        {"section": "main", "name": "warp1", "kind": "warp", "label": "WARP"},
    )
    assert result["applied"] is False
    assert result["dry_run"] is True
    assert any(change["path"].startswith("forkop.") for change in result["changes"])
    assert "warp_profile_" in " ".join(result["warnings"])


def test_child_create_dry_run_supports_urltest() -> None:
    router = _router()
    result = call(
        make_mcp(router),
        "podkop_child_create",
        {"section": "main", "child_type": "urltest", "name": "auto", "label": "Auto"},
    )
    assert result["applied"] is False
    assert result["dry_run"] is True
    assert any("urltest" in line for line in result["preview"])
