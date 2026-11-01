"""Протокольный smoke-тест MCP-сервера podkop-mcp.

Запускает ``python -m podkop_mcp`` как отдельный процесс и общается с ним
по stdio через официальный клиент ``mcp``. Сеть и настоящий роутер не нужны:
target указывает на ``127.0.0.1:1`` с несуществующим ключом, поэтому
``podkop_doctor`` должен вернуть диагностику с ошибкой SSH, а не упасть.

Проверяется:

1. ``initialize`` — сервер отвечает и сообщает имя/версию;
2. ``tools/list`` — есть ``podkop_doctor`` (и другие инструменты);
3. ``podkop_doctor`` — вызов завершается ответом (ok=true или error), процесс жив.

Код выхода 0 — если ``tools/list`` успешен, иначе 1.

Примеры:

    .\\.venv\\Scripts\\python.exe scripts\\smoke_mcp.py
    .\\.venv\\Scripts\\python.exe scripts\\smoke_mcp.py --timeout 40 --verbose
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = REPO_ROOT / "src"
SMOKE_TMP = Path(tempfile.mkdtemp(prefix="podkop-mcp-smoke-"))

# Инструмент, который обязан быть в tools/list.
REQUIRED_TOOL = "podkop_doctor"


def build_env() -> dict[str, str]:
    """Окружение сервера: локальный недоступный порт и фиктивный ключ."""

    env = dict(os.environ)
    # Чужой конфиг/цель из окружения пользователя не должен влиять на smoke.
    for key in list(env):
        if key.startswith("PODKOP_MCP_"):
            env.pop(key, None)
    fixture_dir = REPO_ROOT / "tests" / "fixtures"
    env.update(
        {
            "PODKOP_MCP_SSH_HOST": "127.0.0.1",
            "PODKOP_MCP_SSH_PORT": "1",
            "PODKOP_MCP_SSH_USER": "root",
            # Ключа нет специально: doctor должен вежливо сообщить об ошибке.
            "PODKOP_MCP_SSH_KEY_PATH": str(fixture_dir / "no-such-key"),
            "PODKOP_MCP_KNOWN_HOSTS": str(SMOKE_TMP / "known_hosts"),
            "PODKOP_MCP_HOST_KEY_POLICY": "reject",
            "PODKOP_MCP_CONNECT_TIMEOUT": "3",
            "PODKOP_MCP_COMMAND_TIMEOUT": "10",
            "PODKOP_MCP_LOG_LEVEL": "DEBUG",
            "PYTHONPATH": str(SRC_DIR) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else ""),
            "PYTHONIOENCODING": "utf-8",
            "PYTHONUNBUFFERED": "1",
        }
    )
    return env


def server_params() -> StdioServerParameters:
    return StdioServerParameters(
        command=sys.executable,
        args=["-m", "podkop_mcp"],
        env=build_env(),
        cwd=str(REPO_ROOT),
        encoding="utf-8",
        encoding_error_handler="replace",
    )


def _tool_names(result: Any) -> list[str]:
    tools = getattr(result, "tools", None) or []
    names: list[str] = []
    for tool in tools:
        name = getattr(tool, "name", None)
        if name:
            names.append(str(name))
    return names


def _call_result_text(result: Any) -> str:
    """Достаёт удобочитаемый текст из ``CallToolResult``."""

    structured = getattr(result, "structured_content", None)
    if isinstance(structured, dict) and structured:
        return json.dumps(structured, ensure_ascii=False)[:4000]
    for item in getattr(result, "content", None) or []:
        text = getattr(item, "text", None)
        if text:
            return str(text)[:4000]
    return repr(result)[:2000]


async def run_smoke(timeout: float, verbose: bool) -> int:
    """Основной сценарий; возвращает код выхода."""

    params = server_params()
    print(f"[smoke] запуск: {params.command} {' '.join(params.args)}", flush=True)
    print("[smoke] target: 127.0.0.1:1 (ожидается ошибка SSH, но не падение)", flush=True)

    async def scenario() -> int:
        async with stdio_client(params) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                init = await session.initialize()
                info = getattr(init, "serverInfo", None) or getattr(init, "server_info", None)
                server_name = getattr(info, "name", "?")
                server_version = getattr(info, "version", "?")
                protocol = getattr(init, "protocolVersion", None) or getattr(init, "protocol_version", "?")
                print(f"[smoke] initialize: ok, server={server_name} {server_version}, protocol={protocol}", flush=True)

                listing = await session.list_tools()
                names = _tool_names(listing)
                print(f"[smoke] tools/list: {len(names)} инструментов", flush=True)
                if verbose:
                    for name in sorted(names):
                        print(f"          - {name}", flush=True)
                if REQUIRED_TOOL not in names:
                    print(f"[smoke] FAIL: в tools/list нет {REQUIRED_TOOL}", flush=True)
                    return 1
                print(f"[smoke] tools/list: ok, {REQUIRED_TOOL} найден", flush=True)

                try:
                    result = await session.call_tool(REQUIRED_TOOL, {}, read_timeout_seconds=timeout)
                except Exception as exc:  # noqa: BLE001 — smoke должен объяснить проблему
                    # Протокольный вызывающий контракт: инструмент обязан вернуть ответ,
                    # даже если внутри SSH недоступен. Исключение = сбой сервера.
                    print(f"[smoke] FAIL: {REQUIRED_TOOL} бросил {type(exc).__name__}: {exc}", flush=True)
                    return 1

                is_error = bool(getattr(result, "is_error", False) or getattr(result, "isError", False))
                said = _call_result_text(result)
                # Ошибка SSH ожидаема: доктор обязан вернуть json с "ok": false, а не Traceback.
                print(f"[smoke] {REQUIRED_TOOL}: {'error' if is_error else 'ok'}", flush=True)
                print(f"[smoke] ответ: {said}", flush=True)
                if "Traceback" in said:
                    print(f"[smoke] FAIL: {REQUIRED_TOOL} вернул трассировку вместо диагностики", flush=True)
                    return 1
                if is_error:
                    print(
                        f"[smoke] WARN: {REQUIRED_TOOL} вернул ошибку инструмента; "
                        "ожидалась диагностика с ok/error",
                        flush=True,
                    )
                elif '"ok"' not in said:
                    print(f"[smoke] WARN: в ответе {REQUIRED_TOOL} нет поля ok", flush=True)

                print("[smoke] PASS: сервер ответил на initialize, tools/list и tools/call", flush=True)
                return 0

    try:
        return await asyncio.wait_for(scenario(), timeout=timeout * 3)
    except TimeoutError:
        print(f"[smoke] FAIL: таймаут {timeout * 3:.0f} с", flush=True)
        return 1
    except Exception as exc:  # noqa: BLE001 — smoke должен объяснить проблему
        print(f"[smoke] FAIL: {type(exc).__name__}: {exc}", flush=True)
        return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="MCP stdio smoke test для podkop-mcp")
    parser.add_argument("--timeout", type=float, default=20.0, help="таймаут вызова инструмента, с (по умолчанию 20)")
    parser.add_argument("--verbose", action="store_true", help="печатать все имена инструментов и полный ответ")
    args = parser.parse_args(argv)

    if not SRC_DIR.is_dir():
        print(f"[smoke] FAIL: нет каталога {SRC_DIR}", flush=True)
        return 1
    try:
        return asyncio.run(run_smoke(args.timeout, args.verbose))
    finally:
        shutil.rmtree(SMOKE_TMP, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
