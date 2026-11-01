"""Тесты monitoring-инструментов: чистые helper-функции + fake router."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from podkop_mcp.backends import BackendProfile
from podkop_mcp.tools import monitor as monitor_mod
from podkop_mcp.tools.monitor import (
    _aggregate_connections,
    _build_snapshot,
    _compute_traffic,
    _filter_connections,
    _top_chains,
    _top_clients,
    _top_hosts,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def load_fixture(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


# ---------------------------------------------------------------- fake router
class FakeResult:
    def __init__(self, stdout: str = "", exit_code: int = 0) -> None:
        self.stdout = stdout
        self.stderr = ""
        self.exit_code = exit_code

    def tail(self) -> str:
        return self.stdout


class FakeRouter:
    """Минимальный двойник Router для helper-функций мониторинга."""

    def __init__(self, *, commands: set[str] | None = None) -> None:
        self.name = "fake"
        self.profile = BackendProfile(
            kind="forkop",
            version="1.0.5",
            config_version="1.0.5",
            schema="forkop1",
            package="forkop",
            init_script="/etc/init.d/forkop",
            cli="forkop",
            commands=commands if commands is not None else {"get_status", "get_system_info", "check_sing_box"},
        )
        self.status_payload: dict[str, Any] = {"running": 1}

    def cli_json(self, *args: str, timeout: float | None = None) -> dict[str, Any]:
        command = args[0]
        if command == "get_status":
            return dict(self.status_payload)
        if command == "get_system_info":
            return {"model": "Test Router"}
        raise AssertionError(f"неожиданная команда {command}")

    def cli(self, *args: str, timeout: float | None = None) -> FakeResult:
        command = args[0]
        if command == "check_sing_box":
            return FakeResult("ok sing-box")
        raise AssertionError(f"неожиданная команда {command}")


class FakeClash:
    """Двойник Clash с фиксированными ответами."""

    def __init__(self, summary: dict[str, Any]) -> None:
        self._summary = summary

    def proxies_summary(self) -> dict[str, Any]:
        return dict(self._summary["proxies"])

    def connections_summary(self, *, limit: int = 50) -> dict[str, Any]:
        return dict(self._summary["connections"])

    def connections(self) -> dict[str, Any]:
        return dict(self._summary["connections_raw"])


def make_summary_fixture() -> dict[str, Any]:
    items = load_fixture("forkop1-connections.json")["connections"]
    from podkop_mcp.clash import _normalize_connection

    normalized = [_normalize_connection(item) for item in items]
    return {
        "proxies": {
            "groups": [{"name": "main-out", "now": "RIX-01", "delay": 120, "all": ["RIX-01"]}],
            "selected": [{"name": "main-out", "now": "RIX-01", "delay": 120}],
            "nodes": [],
            "group_count": 1,
            "node_count": 0,
        },
        "connections": {
            "downloadTotal": 1000,
            "uploadTotal": 500,
            "count": len(normalized),
            "connections": normalized,
        },
        "connections_raw": {"downloadTotal": 1000, "uploadTotal": 500, "connections": items},
    }


# ---------------------------------------------------------------- aggregates
def test_top_hosts_counts_and_bytes():
    items = [
        {"host": "a.example", "upload": 10, "download": 20},
        {"host": "a.example", "upload": 5, "download": 5},
        {"host": "b.example", "upload": 100, "download": 0},
    ]
    top = _top_hosts(items, top=2)
    assert top[0]["host"] == "b.example" and top[0]["bytes"] == 100
    assert top[1]["host"] == "a.example" and top[1]["count"] == 2 and top[1]["bytes"] == 40


def test_top_clients_and_chains():
    items = [
        {"source_ip": "192.0.2.100", "chain": "main-out", "upload": 1, "download": 2},
        {"source_ip": "192.0.2.100", "chain": "exclusion-out", "upload": 3, "download": 4},
        {"source_ip": "192.0.2.101", "chain": "main-out", "upload": 0, "download": 50},
    ]
    clients = _top_clients(items, top=2)
    # Сортировка по байтам: .101 набрал 50, .100 — только 10.
    assert clients[0]["source_ip"] == "192.0.2.101"
    assert clients[1]["source_ip"] == "192.0.2.100" and clients[1]["count"] == 2
    chains = _top_chains(items, top=2)
    assert chains[0]["chain"] == "main-out" and chains[0]["count"] == 2


def test_aggregate_empty_is_safe():
    assert _aggregate_connections([], lambda item: item.get("host"), 5, key_name="host") == []


# ---------------------------------------------------------------- filters
def test_filter_by_host_substring_casefold():
    items = [
        {"host": "Spotify.com", "source_ip": "1.1.1.1", "chain": "x"},
        {"host": "telegram.org", "source_ip": "2.2.2.2", "chain": "y"},
    ]
    assert _filter_connections(items, host="SPOT") == [items[0]]


def test_filter_by_source_ip_exact_and_prefix():
    items = [
        {"host": "a", "source_ip": "192.0.2.100", "chain": "x"},
        {"host": "b", "source_ip": "192.0.2.101", "chain": "x"},
        {"host": "c", "source_ip": "10.0.0.1", "chain": "x"},
    ]
    assert len(_filter_connections(items, source_ip="192.0.2.100")) == 1
    assert len(_filter_connections(items, source_ip="192.0.2.")) == 2


def test_filter_by_chain_substring():
    items = [
        {"host": "a", "source_ip": "1", "chain": "exclusion-out"},
        {"host": "b", "source_ip": "1", "chain": "main-out"},
    ]
    assert _filter_connections(items, chain="main") == [items[1]]


# ---------------------------------------------------------------- traffic
def test_compute_traffic_deltas_and_rates():
    first = [
        {"id": "1", "host": "a", "source_ip": "c", "chain": "x", "upload": 100, "download": 200},
        {"id": "2", "host": "b", "source_ip": "c", "chain": "x", "upload": 10, "download": 20},
    ]
    second = [
        {"id": "1", "host": "a", "source_ip": "c", "chain": "x", "upload": 150, "download": 350},
        {"id": "3", "host": "c", "source_ip": "d", "chain": "y", "upload": 7, "download": 0},
    ]
    out = _compute_traffic(
        first,
        second,
        {"downloadTotal": 1000, "uploadTotal": 500},
        {"downloadTotal": 2000, "uploadTotal": 700},
        seconds=2.0,
    )
    assert out["rates"]["download_delta"] == 1000
    assert out["rates"]["upload_delta"] == 200
    assert out["rates"]["download_bps"] == 500.0
    assert out["reset_detected"] is False
    tops = out["top_connections"]
    assert tops[0]["id"] == "1" and tops[0]["bytes"] == 200
    assert tops[1]["new"] is True  # соединение появилось — не падаем


def test_compute_traffic_reset_detected():
    out = _compute_traffic(
        [], [], {"downloadTotal": 5000, "uploadTotal": 100}, {"downloadTotal": 10, "uploadTotal": 20}, seconds=1.0,
    )
    assert out["reset_detected"] is True
    assert out["rates"]["download_delta"] == 0


# ---------------------------------------------------------------- snapshot
def test_build_snapshot_full():
    router = FakeRouter()
    data = _build_snapshot(router, clash_factory=lambda _r: FakeClash(make_summary_fixture()), top=2)
    assert data["target"] == "fake"
    assert data["service"]["running"] == 1
    assert data["proxies"]["group_count"] == 1
    assert data["proxies"]["selected"][0]["now"] == "RIX-01"
    assert data["traffic"]["count"] >= 1
    assert data["checks"]["check_sing_box"]["output"] == "ok sing-box"
    assert data["check_errors"] == {}
    assert data["backend"]["config_package"] == "forkop"


def test_build_snapshot_survives_errors():
    class BrokenClash:
        def __init__(self, _router: Any) -> None: ...
        def proxies_summary(self) -> dict[str, Any]:
            raise RuntimeError("boom")

        def connections_summary(self, *, limit: int = 50) -> dict[str, Any]:
            raise RuntimeError("boom")

    router = FakeRouter()
    data = _build_snapshot(router, clash_factory=BrokenClash, include_connections=False)
    assert data["proxies"]["group_count"] == 0  # пустая сводка вместо падения
    assert "proxies" in data["check_errors"]
    assert data["service"]["running"] == 1  # остальные части не пострадали


def test_build_snapshot_skips_unsupported_commands():
    # commands содержит только check_sing_box: get_status/get_system_info пропускаются.
    router = FakeRouter(commands={"check_sing_box"})
    data = _build_snapshot(router, clash_factory=lambda _r: FakeClash(make_summary_fixture()))
    assert data["service"] == {}
    assert data["system"] == {}
    assert "check_sing_box" in data["checks"]


# ---------------------------------------------------------------- live tool internals
def test_fixture_normalized_hosts_present():
    """Фикстура реального роутера агрегируется без ошибок."""

    items = load_fixture("forkop1-connections.json")["connections"]
    from podkop_mcp.clash import _normalize_connection

    normalized = [_normalize_connection(item) for item in items]
    hosts = _top_hosts(normalized, top=5)
    assert hosts and all("host" in row and "bytes" in row for row in hosts)


def test_sample_seconds_bounds_documented():
    assert monitor_mod.MIN_SAMPLE_SECONDS == 0.5
    assert monitor_mod.MAX_SAMPLE_SECONDS == 10.0
    assert monitor_mod.MAX_CONNECTIONS == 500


def test_snapshot_filters_top():
    data_full = _build_snapshot(
        FakeRouter(), clash_factory=lambda _r: FakeClash(make_summary_fixture()), top=2,
    )
    assert len(data_full["connections"]["items"]) <= 2


@pytest.mark.parametrize("top", [1, 3])
def test_top_helpers_respect_n(top: int):
    items = [{"host": f"h{i}", "upload": i, "download": 0} for i in range(10)]
    assert len(_top_hosts(items, top=top)) == top


# ---------------------------------------------------------------- wrappers (MCP)
class FakeServices:
    """Подменяет Services: router_for получает FakeRouter."""

    def __init__(self, router: FakeRouter) -> None:
        self._router = router
        self.settings = type("S", (), {"read_only": False, "backup_dir": "/tmp", "backup_keep": 5, "max_output_bytes": 200_000})()

    def require_targets(self) -> None: ...

    def router(self, name: str | None = None) -> FakeRouter:
        return self._router


def make_mcp(router: FakeRouter, clash_factory: Any = None) -> Any:
    from mcp.server import MCPServer

    from podkop_mcp.tools import monitor as monitor_mod

    mcp = MCPServer("test")
    monitor_mod.register(mcp, FakeServices(router))  # type: ignore[arg-type]
    if clash_factory is not None:
        monitor_mod.Clash = clash_factory  # type: ignore[assignment]
    return mcp


def call(mcp: Any, name: str, arguments: dict[str, Any]) -> Any:
    async def _run() -> Any:
        return await mcp.call_tool(name, arguments)

    result = asyncio.run(_run())
    structured = getattr(result, "structured_content", None)
    if structured is not None:
        return structured
    return json.loads(result.content[0].text)


def test_wrapper_monitor_snapshot():
    router = FakeRouter()
    mcp = make_mcp(router, clash_factory=lambda _r: FakeClash(make_summary_fixture()))
    data = call(mcp, "podkop_monitor_snapshot", {"top": 2})
    assert data["target"] == "fake"
    assert data["service"]["running"] == 1
    assert len(data["connections"]["items"]) <= 2


def test_wrapper_connections_live_filters():
    router = FakeRouter()
    mcp = make_mcp(router, clash_factory=lambda _r: FakeClash(make_summary_fixture()))
    result = call(
        mcp,
        "podkop_connections_live",
        {"source_ip": "192.0.2.", "limit": 3},
    )
    assert result["count"] >= 1
    assert all(str(item["source_ip"]).startswith("192.0.2.") for item in result["items"])


def test_wrapper_traffic_with_tiny_sample(monkeypatch: pytest.MonkeyPatch):
    import podkop_mcp.tools.monitor as monitor_mod

    summary = make_summary_fixture()
    # Второй сэмпл: счётчики выросли на 600/200 байт.
    second = json.loads(json.dumps(summary["connections_raw"]))
    second["downloadTotal"] += 600
    second["uploadTotal"] += 200
    for item in second["connections"]:
        item["download"] += 50
        item["upload"] += 50

    class TwoSampleClash:
        def __init__(self, _router: Any) -> None:
            self._n = 0

        def connections(self) -> dict[str, Any]:
            self._n += 1
            return dict(summary["connections_raw"]) if self._n == 1 else second

    monkeypatch.setattr(monitor_mod.time, "sleep", lambda _s: None)
    mcp = make_mcp(FakeRouter(), clash_factory=TwoSampleClash)
    data = call(mcp, "podkop_traffic", {"sample_seconds": 1.0, "top": 5})
    assert data["sample_seconds"] == 1.0
    assert data["rates"]["download_delta"] == 600
    assert data["rates"]["download_bps"] == 600.0
    assert data["rates"]["upload_bps"] == 200.0
    assert data["reset_detected"] is False
    assert len(data["top_connections"]) <= 5


def test_wrapper_traffic_clamps_sample_seconds(monkeypatch: pytest.MonkeyPatch):
    import podkop_mcp.tools.monitor as monitor_mod

    summary = make_summary_fixture()

    class OneClash:
        def __init__(self, _router: Any) -> None: ...
        def connections(self) -> dict[str, Any]:
            return dict(summary["connections_raw"])

    sleeps: list[float] = []
    monkeypatch.setattr(monitor_mod.time, "sleep", lambda s: sleeps.append(s))
    mcp = make_mcp(FakeRouter(), clash_factory=OneClash)
    data = call(mcp, "podkop_traffic", {"sample_seconds": 99.0})
    assert data["sample_seconds"] == monitor_mod.MAX_SAMPLE_SECONDS
    assert sleeps and sleeps[0] == monitor_mod.MAX_SAMPLE_SECONDS
