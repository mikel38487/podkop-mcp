"""Снять санитизированные фикстуры с реального роутера.

Скрипт только читает (``ubus uci get``, ``forkop show_config masked``,
``get_status``/``get_system_info``/``get_ui_state`` и Clash connections/proxies).
Он не пишет на роутер и не требует ``apply``.

Перед записью:
- URL с длинным токеном заменяются на ``https://sub.example.com/REDACTED``;
- sourceIP клиентов заменяются на ``192.0.2.100..104``;
- приватный ключ и SSH-конфиг в фикстуры не попадают.

Пример:
    .\\.venv\\Scripts\\python.exe scripts\\capture_fixtures.py \\
        --ssh-config-host router-home \\
        --key-path C:/Users/<user>/.ssh/router_key \\
        --out tests/fixtures
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

from podkop_mcp.config import Settings, TargetConfig  # noqa: E402
from podkop_mcp.ssh import SSHSession  # noqa: E402

TOKEN_URL_RE = re.compile(r"https?://[^\s\"'<>]+")


def sanitize(value: Any, *, ip_counter: list[int] | None = None) -> Any:
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            if key == "sourceIP" and isinstance(item, str):
                index = (ip_counter or [0])[0]
                if ip_counter is not None:
                    ip_counter[0] = (index + 1) % 5
                result[key] = f"192.0.2.{100 + index}"
            else:
                result[key] = sanitize(item, ip_counter=ip_counter)
        return result
    if isinstance(value, list):
        return [sanitize(item, ip_counter=ip_counter) for item in value]
    if isinstance(value, str):
        def replace(match: re.Match[str]) -> str:
            url = match.group(0)
            path = url.split("/", 3)[-1] if url.count("/") >= 3 else ""
            token = re.search(r"[A-Za-z0-9_\-]{18,}", path)
            return "https://sub.example.com/REDACTED" if token or "?" in path else url

        return TOKEN_URL_RE.sub(replace, value)
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Снять санитизированные фикстуры podkop-mcp")
    parser.add_argument("--ssh-config-host", default=None, help="алиас из ~/.ssh/config")
    parser.add_argument("--host", default=None, help="адрес роутера")
    parser.add_argument("--user", default="root")
    parser.add_argument("--port", type=int, default=22)
    parser.add_argument("--key-path", default=None, help="приватный SSH-ключ")
    parser.add_argument("--known-hosts", default=None)
    parser.add_argument("--out", default="tests/fixtures")
    parser.add_argument("--force", action="store_true", help="перезаписать существующие файлы")
    args = parser.parse_args(argv)

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    if not args.force and any(out.iterdir()):
        print(f"{out} не пуст; используйте --force", file=sys.stderr)
        return 2

    target = TargetConfig(
        name="router",
        host=args.host,
        port=args.port,
        user=args.user,
        ssh_config_host=args.ssh_config_host,
        key_path=args.key_path,
        known_hosts=args.known_hosts,
        host_key_policy="reject",
    )
    settings = Settings(targets={"router": target}, default_target="router")
    session = SSHSession(target, max_output_bytes=settings.max_output_bytes)

    def run(script: str) -> str:
        return session.run(script, timeout=45, check=True).stdout

    try:
        uci = json.loads(run("ubus call uci get '{\"config\":\"forkop\"}'"))
        (out / "forkop1-uci.json").write_text(
            json.dumps(sanitize(uci), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (out / "forkop1-showconfig-masked.txt").write_text(
            run("forkop show_config masked"), encoding="utf-8"
        )
        for name, command in (
            ("forkop1-status.json", "forkop get_status"),
            ("forkop1-system.json", "forkop get_system_info"),
            ("forkop1-uistate.json", "forkop get_ui_state"),
            ("forkop1-connections.json", "forkop clash_api get_connections"),
            ("forkop1-proxies.json", "forkop clash_api get_proxies"),
        ):
            payload = json.loads(run(command))
            (out / name).write_text(
                json.dumps(sanitize(payload), ensure_ascii=False, indent=2), encoding="utf-8"
            )
    finally:
        session.close()

    print(f"Готово: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
