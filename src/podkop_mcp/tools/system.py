"""Инструменты статуса, диагностики и логов."""

from __future__ import annotations

import json
import re
from typing import Any, Literal

from mcp.server import MCPServer

from ..errors import BackendError, ConfigError, SafetyError
from ..redaction import redact_config, redact_text
from ..services import Services
from .common import guard, router_for

SYSTEM_SCRIPT = r"""
echo '@@LOADAVG'
cat /proc/loadavg 2>/dev/null || true
echo '@@MEMINFO'
grep -E '^(MemTotal|MemFree|MemAvailable|Buffers|Cached|SwapTotal|SwapFree):' /proc/meminfo 2>/dev/null || true
echo '@@UPTIME'
cat /proc/uptime 2>/dev/null || true
echo '@@DISK'
df -k / /tmp /overlay 2>/dev/null || true
echo '@@IFACES'
ip -br address show 2>/dev/null || true
echo '@@WAN'
ubus call network.interface.wan status 2>/dev/null | head -c 4000 || true
echo '@@PROCS'
ps w 2>/dev/null | grep -E 'sing-box|nfqws2|podkop|forkop' | grep -v grep || true
echo '@@END'
"""

_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


def _marker(text: str, name: str) -> str:
    marker = f"@@{name}"
    start = text.find(marker)
    if start < 0:
        return ""
    start += len(marker)
    end = text.find("@@", start)
    return (text[start:end] if end >= 0 else text[start:]).strip()


def _parse_meminfo(text: str) -> dict[str, int]:
    result: dict[str, int] = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[1].isdigit():
            result[parts[0].rstrip(":")] = int(parts[1])
    return result


def _json_or_text(router: Any, command: str, *, timeout: float = 25.0, max_text: int = 8000) -> dict[str, Any]:
    result = router.cli(command, timeout=timeout)
    payload: dict[str, Any] = {"exit_code": result.exit_code}
    text = result.stdout.strip()
    if text:
        try:
            parsed = json.loads(text)
            payload["data"] = parsed if isinstance(parsed, (dict, list)) else {"value": parsed}
        except json.JSONDecodeError:
            payload["text"] = redact_text(text[-max_text:])
    if result.stderr.strip():
        payload["stderr"] = redact_text(result.stderr[-2000:])
    if result.exit_code != 0:
        # Не выдаём usage-дамп как полезные данные: вызывающий код должен
        # видеть явную ошибку, а полный вывод доступен через podkop_logs.
        payload["status"] = "error"
        payload["error"] = redact_text(
            (result.stderr.strip() or result.stdout.strip() or f"exit code {result.exit_code}")[-2000:]
        )
        payload.pop("text", None)
    else:
        payload["status"] = "ok"
    return payload


def register(mcp: MCPServer, services: Services) -> None:
    @mcp.tool()
    @guard
    def podkop_doctor(target: str | None = None) -> dict[str, Any]:
        """Check MCP configuration, SSH connectivity, backend and capabilities.

        Use this first when something does not work: it explains missing
        environment variables, SSH key problems and the detected Podkop/Forkop
        version without changing the router.
        """

        try:
            return services.doctor(target)
        except Exception as exc:  # doctor обязан вернуть диагностику, а не упасть
            return {"ok": False, "error": str(exc), "settings": services.settings.public_dict()}

    @mcp.tool()
    @guard
    def podkop_status(target: str | None = None) -> dict[str, Any]:
        """Return service, sing-box, DNS and version status of the router."""

        router = router_for(services, target)
        profile = router.profile
        data: dict[str, Any] = {
            "target": router.name,
            "backend": profile.public_dict(),
            "read_only": router.settings.read_only,
        }
        commands = [
            ("get_status", "service"),
            ("get_system_info", "system"),
            ("get_sing_box_status", "sing_box"),
            ("get_ui_state", "ui_state"),
            ("get_zapret2_status", "zapret2"),
        ]
        skipped: list[str] = []
        errors: list[str] = []
        for command, key in commands:
            if profile.commands and command not in profile.commands:
                skipped.append(key)
                continue
            value = _json_or_text(router, command)
            data[key] = value
            if value.get("exit_code"):
                errors.append(key)
        if "sing_box" not in data and (not profile.commands or "check_sing_box" in profile.commands):
            # Forkop 1.x не имеет get_sing_box_status, но умеет check_sing_box.
            value = _json_or_text(router, "check_sing_box")
            data["sing_box"] = value
            if value.get("exit_code"):
                errors.append("sing_box")
        data["skipped"] = skipped
        data["errors"] = errors
        data["ok"] = not errors
        return data

    @mcp.tool()
    @guard
    def podkop_core_features(target: str | None = None) -> dict[str, Any]:
        """Return sing-box features compiled into the installed Forkop core (read-only)."""

        router = router_for(services, target)
        profile = router.profile
        if "get_core_features" not in profile.commands:
            raise BackendError(f"{profile.display_name} не поддерживает get_core_features")
        payload = _json_or_text(router, "get_core_features", timeout=30)
        return {"target": router.name, "backend": profile.public_dict(), **payload}

    @mcp.tool()
    @guard
    def podkop_import_check(path: str, target: str | None = None) -> dict[str, Any]:
        """Validate a Forkop config file on the router without applying it (read-only)."""

        router = router_for(services, target)
        profile = router.profile
        if "import_config_check" not in profile.commands:
            raise BackendError(f"{profile.display_name} не поддерживает import_config_check")
        if not path.startswith("/") or ".." in path or len(path) > 4096:
            raise ConfigError("path должен быть абсолютным путём на роутере без '..'")
        result = router.cli("import_config_check", path, timeout=60)
        payload: dict[str, Any] = {"target": router.name, "path": path, "exit_code": result.exit_code}
        text = (result.stdout or "").strip()
        if text:
            try:
                parsed = json.loads(text)
                if isinstance(parsed, dict):
                    parsed = redact_config({"data": parsed})["data"]
                payload["data"] = parsed if isinstance(parsed, (dict, list)) else {"value": parsed}
            except json.JSONDecodeError:
                payload["text"] = redact_text(text[-8000:])
        if result.stderr.strip():
            payload["stderr"] = redact_text(result.stderr[-4000:])
        payload["ok"] = result.exit_code == 0
        return payload

    @mcp.tool()
    @guard
    def podkop_installer_status(target: str | None = None, offset: int = 0) -> dict[str, Any]:
        """Return the status of the last Forkop installer run (read-only)."""

        router = router_for(services, target)
        profile = router.profile
        if "installer_status" not in profile.commands:
            raise BackendError(f"{profile.display_name} не поддерживает installer_status")
        offset = max(0, min(int(offset), 100000))
        args = ["installer_status"]
        if offset:
            args.append(str(offset))
        result = router.cli(*args, timeout=30)
        payload: dict[str, Any] = {"exit_code": result.exit_code}
        text = (result.stdout or "").strip()
        if text:
            try:
                parsed = json.loads(text)
                if isinstance(parsed, dict):
                    parsed = redact_config({"data": parsed})["data"]
                payload["data"] = parsed if isinstance(parsed, (dict, list)) else {"value": parsed}
            except json.JSONDecodeError:
                payload["text"] = redact_text(text[-8000:])
        if result.stderr.strip():
            payload["stderr"] = redact_text(result.stderr[-4000:])
        payload["ok"] = result.exit_code == 0
        return {"target": router.name, "backend": profile.public_dict(), **payload}

    @mcp.tool()
    @guard
    def podkop_installer_log(target: str | None = None) -> dict[str, Any]:
        """Return the last Forkop installer log, redacted (read-only)."""

        router = router_for(services, target)
        profile = router.profile
        if "installer_log" not in profile.commands:
            raise BackendError(f"{profile.display_name} не поддерживает installer_log")
        result = router.cli("installer_log", timeout=30)
        return {
            "target": router.name,
            "exit_code": result.exit_code,
            "ok": result.exit_code == 0,
            "log": redact_text((result.stdout or result.stderr or "")[-16000:]),
        }

    @mcp.tool()
    @guard
    def podkop_system_info(target: str | None = None) -> dict[str, Any]:
        """Return model, OpenWrt version, kernel, load, memory, disk and WAN info."""

        router = router_for(services, target)
        result = router.run(SYSTEM_SCRIPT, timeout=30)
        text = result.stdout
        uci = router.cli_json("get_system_info", timeout=20) if (
            not router.profile.commands or "get_system_info" in router.profile.commands
        ) else {}

        load = _marker(text, "LOADAVG").split()
        uptime_seconds = None
        uptime_raw = _marker(text, "UPTIME").split()
        if uptime_raw:
            try:
                uptime_seconds = float(uptime_raw[0])
            except ValueError:
                pass
        wan_text = _marker(text, "WAN")
        wan: Any = wan_text
        try:
            wan = json.loads(wan_text) if wan_text else {}
        except json.JSONDecodeError:
            pass
        return {
            "target": router.name,
            "system": uci,
            "load": {"1m": load[0], "5m": load[1], "15m": load[2]} if len(load) >= 3 else {},
            "uptime_seconds": uptime_seconds,
            "memory_kb": _parse_meminfo(_marker(text, "MEMINFO")),
            "disk": _marker(text, "DISK").splitlines(),
            "interfaces": _marker(text, "IFACES").splitlines(),
            "wan": wan,
            "processes": _marker(text, "PROCS").splitlines(),
            "raw_exit_code": result.exit_code,
        }

    @mcp.tool()
    @guard
    def podkop_health(
        target: str | None = None,
        full: bool = False,
        timeout: float = 60.0,
    ) -> dict[str, Any]:
        """Run read-only health checks: sing-box, nftables, FakeIP and DNS.

        ``full=true`` additionally runs the global network check, which can
        take up to a minute because it tests real connectivity.
        """

        router = router_for(services, target)
        profile = router.profile
        checks: dict[str, Any] = {}
        skipped: list[str] = []
        if profile.schema == "forkop2":
            candidates = [
                ("check_service", "service"),
                ("check_dns", "dns"),
                ("check_traffic", "traffic"),
                ("check_servers", "servers"),
            ]
        else:
            candidates = [
                ("check_sing_box", "sing_box"),
                ("check_nft", "nft"),
                ("check_nft_rules", "nft_rules"),
                ("check_fakeip", "fakeip"),
                ("check_dns_available", "dns"),
                ("check_service", "service"),
                ("check_traffic", "traffic"),
            ]
        for command, key in candidates:
            if command not in profile.commands:
                skipped.append(key)
                continue
            # nftables/check_* могут печатать огромный дамп правил — для health
            # достаточно хвоста, полный вывод доступен через podkop_logs/podkop_cli.
            checks[key] = _json_or_text(router, command, timeout=min(timeout, 45.0), max_text=800)
        if full:
            if "global_check" in profile.commands:
                checks["global"] = _json_or_text(router, "global_check", timeout=timeout, max_text=4000)
            else:
                skipped.append("global")
        errors = [key for key, value in checks.items() if isinstance(value, dict) and value.get("exit_code")]
        return {
            "target": router.name,
            "backend": profile.display_name,
            "version": profile.version,
            "schema": profile.schema,
            "ok": not errors,
            "complete": not skipped,
            "failed": errors,
            "skipped": skipped,
            "checks": checks,
        }

    @mcp.tool()
    @guard
    def podkop_logs(
        target: str | None = None,
        source: Literal["forkop", "sing-box", "system"] = "forkop",
        lines: int = 80,
        filter: str | None = None,
    ) -> dict[str, Any]:
        """Return recent router logs, filtered and redacted.

        ``source`` selects Forkop/Podkop journal, sing-box journal or the whole
        system log. Secrets in URLs are masked before returning.
        """

        router = router_for(services, target)
        lines = max(1, min(int(lines), 500))
        profile = router.profile
        raw: str

        def logread() -> str:
            pattern = {"forkop": "forkop", "sing-box": "sing-box", "system": ""}[source]
            command = "logread"
            if pattern:
                command += f" | grep -i -F {_sh(pattern)}"
            command += f" | tail -n {lines * 2}"
            result = router.run(command, timeout=30)
            return result.stdout or result.stderr

        if source == "forkop" and (not profile.commands or "check_logs" in profile.commands):
            result = router.cli("check_logs", timeout=30)
            raw = result.stdout or result.stderr
            if not raw.strip():
                # Журнал мог прокрутиться — пробуем logread напрямую.
                raw = logread()
        elif source == "sing-box" and (not profile.commands or "check_sing_box_logs" in profile.commands):
            result = router.cli("check_sing_box_logs", timeout=30)
            raw = result.stdout or result.stderr
            if not raw.strip():
                raw = logread()
        else:
            raw = logread()
        items = [line for line in raw.splitlines() if line.strip()]
        if filter:
            needle = filter.lower()
            items = [line for line in items if needle in line.lower()]
        items = [_ANSI_RE.sub("", redact_text(line)) for line in items[-lines:]]
        return {
            "target": router.name,
            "source": source,
            "lines": items,
            "count": len(items),
            "filter": filter,
        }

    @mcp.tool()
    @guard
    def podkop_cli(
        command: str,
        args: list[str] | None = None,
        target: str | None = None,
        parse_json: bool = True,
        reveal_secrets: bool = False,
        timeout: float = 45.0,
    ) -> dict[str, Any]:
        """Run a read-only Forkop/Podkop CLI command from the allowlist.

        Mutating commands (start, stop, reload, list_update, subscription_update,
        component actions) are rejected: use dedicated tools so that backups and
        rollback are applied. Output is redacted unless reveal_secrets=true.
        """

        router = router_for(services, target)
        profile = router.profile
        command = command.strip()
        allowed = profile.allowed_cli_commands()
        if command not in allowed:
            raise SafetyError(
                f"Команда {command!r} не разрешена. Доступные: {', '.join(sorted(allowed))}"
            )
        arg_list = [str(item) for item in (args or [])]
        if not reveal_secrets:
            if command in {"show_config", "show_sing_box_config"}:
                arg_list = [arg if arg != "raw" else "masked" for arg in arg_list] or ["masked"]
            elif command == "report":
                # report <masked|raw> [checks] — принудительно masked,
                # не теряя остальные аргументы.
                if arg_list and arg_list[0] in {"masked", "raw"}:
                    arg_list[0] = "masked"
                else:
                    arg_list = ["masked", *arg_list]
                if len(arg_list) == 1:
                    arg_list.append("checks")
        result = router.cli(command, *arg_list, timeout=max(5.0, min(timeout, 120.0)))
        text = result.stdout
        if not reveal_secrets:
            text = redact_text(text)
        payload: dict[str, Any] = {
            "target": router.name,
            "command": command,
            "args": arg_list,
            "exit_code": result.exit_code,
            "stdout": text[: router.settings.max_output_bytes],
            "stderr": redact_text(result.stderr)[:4000],
        }
        if parse_json:
            try:
                # Парсим уже замаскированный текст, иначе поле json обходило бы
                # redaction и отдавало секреты при reveal_secrets=false.
                payload["json"] = json.loads(text)
            except json.JSONDecodeError:
                payload["json"] = None
        return payload


def _sh(value: str) -> str:
    return "'" + value.replace("'", "'\\''") + "'"
