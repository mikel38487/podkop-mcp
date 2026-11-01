"""Инструменты управления сервисом и обновлениями."""

from __future__ import annotations

import time
from typing import Any, Literal

from mcp.server import MCPServer

from ..errors import BackendError, SafetyError
from ..redaction import redact_text
from ..services import Services
from .common import guard, router_for


def _wait_running(router: Any, timeout: float) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        try:
            last = router.cli_json("get_status", timeout=10)
        except Exception:
            last = {}
        if router.is_running() is True:
            return {"running": 1, "status": last.get("status"), "dns_configured": last.get("dns_configured")}
        time.sleep(1.5)
    raise BackendError(f"Сервис не поднялся за {timeout:.0f} с: {last}")


def _wait_stopped(router: Any, timeout: float) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        try:
            last = router.cli_json("get_status", timeout=10)
        except Exception:
            return {"running": 0, "status": "stopped"}
        if router.is_running() is False:
            return {"running": 0, "status": last.get("status")}
        time.sleep(1.0)
    raise BackendError(f"Сервис не остановился за {timeout:.0f} с: {last}")


def register(mcp: MCPServer, services: Services) -> None:
    @mcp.tool()
    @guard
    def podkop_service_action(
        action: Literal["start", "stop", "restart", "reload", "enable", "disable"],
        target: str | None = None,
        apply: bool = False,
        wait: bool = True,
        timeout: float = 45.0,
    ) -> dict[str, Any]:
        """Start, stop, restart, reload or toggle autostart of Podkop/Forkop.

        Dry-run by default. ``stop`` is refused in read-only mode. Use
        podkop_apply-style tools for config changes; this tool only controls the
        service lifecycle.
        """

        router = router_for(services, target)
        command = f"{router.profile.init_script} {action}"
        if not apply:
            return {
                "applied": False,
                "dry_run": True,
                "hint": f"Будет выполнено: {command}. Повторите с apply=true.",
                "action": action,
                "target": router.name,
            }
        result = router.service_action(action, timeout=max(10.0, min(timeout, 120.0)))
        status: dict[str, Any] = result.get("status") or {}
        if wait:
            if action == "restart":
                # Дать procd время действительно остановить старый процесс.
                time.sleep(1.5)
            if action in {"start", "restart", "reload"}:
                status = _wait_running(router, max(10.0, min(timeout, 90.0)))
            elif action == "stop":
                status = _wait_stopped(router, max(10.0, min(timeout, 90.0)))
        return {
            "applied": True,
            "action": action,
            "target": router.name,
            "exit_code": result.get("exit_code"),
            "output": result.get("output"),
            "status": status,
        }

    @mcp.tool()
    @guard
    def podkop_lists_update(
        kind: Literal["lists", "subscriptions", "all"] = "lists",
        target: str | None = None,
        apply: bool = False,
        wait: bool = True,
        timeout: float = 180.0,
    ) -> dict[str, Any]:
        """Update built-in lists and/or subscriptions.

        This changes the router's downloaded rule sets and subscription cache,
        so apply=true is required. Long operations are bounded by ``timeout``.
        """

        router = router_for(services, target)
        profile = router.profile
        plan: list[str] = []
        if kind in {"lists", "all"}:
            if profile.commands and "list_update" not in profile.commands:
                if profile.clash_mode != "http":
                    raise BackendError(f"{profile.display_name} не поддерживает list_update")
                plan.append("refresh_rules")
            else:
                plan.append("list_update")
        if kind in {"subscriptions", "all"}:
            if profile.commands and "subscription_update" not in profile.commands:
                if profile.clash_mode != "http":
                    raise BackendError(f"{profile.display_name} не поддерживает subscription_update")
                plan.append("refresh_providers")
            else:
                plan.append("subscription_update")
        if not apply:
            return {
                "applied": False,
                "dry_run": True,
                "hint": "Повторите с apply=true, чтобы обновить списки/подписки.",
                "commands": plan,
                "target": router.name,
            }
        if router.settings.read_only:
            raise SafetyError("PODKOP_MCP_READ_ONLY=1: обновление списков запрещено")

        from ..clash import Clash

        clash = Clash(router)
        results: dict[str, Any] = {}
        for command in plan:
            if command == "refresh_rules":
                results[command] = clash.refresh_rules()
                continue
            if command == "refresh_providers":
                results[command] = clash.refresh_providers()
                continue
            result = router.cli(command, timeout=max(30.0, min(timeout, 300.0)))
            results[command] = {
                "exit_code": result.exit_code,
                "stdout": redact_text(result.stdout[-12000:]),
                "stderr": redact_text(result.stderr[-4000:]),
            }
        status: dict[str, Any] = {}
        if wait:
            try:
                status = router.cli_json("get_status", timeout=15)
            except Exception:
                status = {}
        return {"applied": True, "kind": kind, "target": router.name, "results": results, "status": status}
