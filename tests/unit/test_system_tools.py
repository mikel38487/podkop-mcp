"""Тесты health/status: Forkop 2.0 не должен терять проверки или отдавать usage."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from fakes import FakeRouter, FakeSession, fail, json_ok, make_forkop2_profile
from mcp.server import MCPServer

from podkop_mcp.tools import system as system_mod


class FakeServices:
    settings = None

    def __init__(self, router: FakeRouter) -> None:
        self._router = router

    def require_targets(self) -> None:
        return None

    def router(self, name: str | None = None) -> FakeRouter:
        return self._router

    def doctor(self, name: str | None = None) -> dict[str, Any]:
        return {"ok": True}


def make_mcp(router: FakeRouter) -> MCPServer:
    mcp = MCPServer("test")
    system_mod.register(mcp, FakeServices(router))  # type: ignore[arg-type]
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
        text = result.content[0].text if result.content else ""
        return {"__tool_error__": text}
    structured = getattr(result, "structured_content", None)
    if structured is not None:
        return structured
    return json.loads(result.content[0].text)


def _router() -> FakeRouter:
    commands = {"get_status", "get_sing_box_status", "check_service", "check_dns", "check_traffic", "check_servers"}
    profile = make_forkop2_profile(commands=commands)
    session = FakeSession(
        responses={
            "forkop get_status": json_ok({"running": 1, "enabled": 1}),
            "forkop check_service": json_ok({"running": True}),
            "forkop check_dns": json_ok({"main": {"answered": True}}),
            "forkop check_traffic": json_ok({"exit": {"answered": True}}),
            "forkop check_servers": json_ok({"items": []}),
        },
        strict=False,
    )
    return FakeRouter(profile=profile, session=session)


def test_health_forkop2_uses_new_commands_and_marks_skipped() -> None:
    router = _router()
    result = call(make_mcp(router), "podkop_health", {"full": False})

    assert result["ok"] is True
    assert result["schema"] == "forkop2"
    assert set(result["checks"]) == {"service", "dns", "traffic", "servers"}
    assert result["skipped"] == []
    assert result["complete"] is True


def test_status_nonzero_command_is_error_not_usage_dump() -> None:
    profile = make_forkop2_profile(commands={"get_status", "get_sing_box_status"})
    session = FakeSession(
        responses={
            "forkop get_status": json_ok({"running": 1, "enabled": 1}),
            "forkop get_sing_box_status": fail(stderr="Usage: /usr/bin/forkop COMMAND ...", exit_code=1),
        },
        strict=False,
    )
    router = FakeRouter(profile=profile, session=session)

    result = call(make_mcp(router), "podkop_status", {})

    assert result["ok"] is False
    assert "sing_box" in result["errors"]
    assert result["sing_box"]["status"] == "error"
    assert "text" not in result["sing_box"]
    assert "Usage" in result["sing_box"]["error"]


def test_import_check_masks_structured_secrets() -> None:
    profile = make_forkop2_profile(commands={"import_config_check"})
    session = FakeSession(
        responses={
            "forkop import_config_check": json_ok(
                {"from": {"proxy_string": "vless://secret"}, "to": {"proxy_string": "vless://secret2"}}
            )
        },
        strict=False,
    )
    router = FakeRouter(profile=profile, session=session)

    result = call(make_mcp(router), "podkop_import_check", {"path": "/tmp/config.uci"})

    assert result["ok"] is True
    assert result["data"]["from"]["proxy_string"] == "***"
    assert result["data"]["to"]["proxy_string"] == "***"
