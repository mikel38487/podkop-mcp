"""Тесты HTTP-транспорта Clash API Forkop 2.x."""

from __future__ import annotations

import json
from typing import Any

from podkop_mcp.clash import Clash


class HttpRouter:
    """Минимальный роутер с clash_mode=http и записью вызовов."""

    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

        class Profile:
            clash_mode = "http"

        class Settings:
            read_only = False

        self.profile = Profile()
        self.settings = Settings()

    def cli_json(self, command: str, *args: Any, timeout: float = 0) -> dict[str, Any]:
        self.calls.append((command, args))
        return self.responses.pop(0)


def test_http_clash_unwraps_nested_json_body() -> None:
    body = {"proxies": {"main": {"type": "Selector", "now": "auto", "all": ["a"]}}}
    router = HttpRouter([{"status": 200, "body": json.dumps(body)}])
    clash = Clash(router)  # type: ignore[arg-type]

    assert clash.proxies() == body
    assert router.calls == [("clash_api", ("GET", "/proxies"))]


def test_http_clash_reports_status_error() -> None:
    router = HttpRouter([{"status": 500, "body": "broken"}])
    clash = Clash(router)  # type: ignore[arg-type]

    try:
        clash.proxies()
    except Exception as exc:
        assert "HTTP 500" in str(exc)
    else:
        raise AssertionError("ожидалась ошибка HTTP 500")


def test_refresh_providers_lists_and_refreshes_each() -> None:
    router = HttpRouter(
        [
            {"status": 200, "body": json.dumps({"providers": {"main/sub": {}, "other": {}}})},
            {"status": 204, "body": ""},
            {"status": 204, "body": ""},
        ]
    )
    clash = Clash(router)  # type: ignore[arg-type]

    result = clash.refresh_providers()

    assert result["providers"] == ["main/sub", "other"]
    assert result["refreshed"] == ["main/sub", "other"]
    assert result["errors"] == {}
    assert router.calls[0] == ("clash_api", ("GET", "/providers/proxies"))
    assert router.calls[1] == ("clash_api", ("PUT", "/providers/proxies/main%2Fsub"))
    assert router.calls[2] == ("clash_api", ("PUT", "/providers/proxies/other"))
