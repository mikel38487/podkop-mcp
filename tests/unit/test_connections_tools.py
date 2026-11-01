"""Тесты connections-инструментов: чистые helper-функции + fake router."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from podkop_mcp.backends import BackendProfile
from podkop_mcp.errors import BackendError, SafetyError
from podkop_mcp.tools.connections import (
    _find_subscription_children,
    _mask,
    _next_free_child_name,
    _plan_subscription_add,
    _plan_subscription_delete,
    _summarize_section,
    _validate_subscription_url,
)
from podkop_mcp.uci import UciConfig, UciSection, parse_ubus_uci

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


class FakePlan:
    """Двойник ChangePlan: только то, что читает preview_or_apply."""

    def __init__(self, builder: FakeBuilder) -> None:
        self.ops = builder.ops
        self.warnings = builder.warnings
        self.changes: list[Any] = []

    def to_dict(self) -> dict[str, Any]:
        return {
            "changes": [],
            "preview": [f"{op}: {args}" for op, args in self.ops],
            "warnings": list(self.warnings),
            "change_count": len(self.ops),
        }


class FakeBuilder:
    """Записывает вызовы PlanBuilder — без реального Router."""

    def __init__(self) -> None:
        self.ops: list[tuple[str, tuple[Any, ...]]] = []
        self.warnings: list[str] = []

    def build(self) -> FakePlan:
        if not self.ops:
            raise ValueError("План пуст")
        return FakePlan(self)

    def create_section(self, name: str, section_type: str) -> None:
        self.ops.append(("create_section", (name, section_type)))

    def set_option(self, section: str, option: str, value: Any) -> None:
        self.ops.append(("set_option", (section, option, value)))

    def set_list(self, section: str, option: str, values: list[str]) -> None:
        self.ops.append(("set_list", (section, option, values)))

    def delete_option(self, section: str, option: str) -> None:
        self.ops.append(("delete_option", (section, option)))

    def delete_section(self, name: str) -> None:
        self.ops.append(("delete_section", (name,)))


class FakeRouter:
    """Двойник Router для helper-функций connections."""

    def __init__(self, *, schema: str = "forkop1", config: UciConfig | None = None) -> None:
        self.name = "fake"
        self.profile = BackendProfile(
            kind="forkop" if schema != "podkop" else "podkop",
            version="1.0",
            config_version="1.0",
            schema=schema,  # type: ignore[arg-type]
            package="forkop" if schema != "podkop" else "podkop",
            init_script="/etc/init.d/forkop",
            cli="forkop",
            commands=set(),
        )
        self._config = config or UciConfig(package="forkop")
        self.builder_log: list[FakeBuilder] = []

    def config(self, package: str | None = None, refresh: bool = False) -> UciConfig:
        return self._config

    def builder(self, reason: str, package: str | None = None) -> FakeBuilder:
        builder = FakeBuilder()
        self.builder_log.append(builder)
        return builder


def load_router_from_fixture() -> FakeRouter:
    payload = json.loads((FIXTURES / "forkop1-uci.json").read_text(encoding="utf-8"))
    return FakeRouter(schema="forkop1", config=parse_ubus_uci(payload, "forkop"))


def config_with_subscriptions() -> UciConfig:
    config = UciConfig(package="forkop")
    config.sections["main"] = UciSection(name="main", type="section", options={"enabled": "1", "action": "proxy"})
    config.sections["sub1"] = UciSection(
        name="main_sub_1",
        type="subscription_url",
        options={"section": "main", "url": "https://sub1.example/token123", "tag": "alpha", "subscription_update_interval": "1h"},
    )
    config.sections["sub2"] = UciSection(
        name="main_sub_2",
        type="subscription_url",
        options={"section": "main", "url": "https://sub2.example/list"},
    )
    config.sections["sub3"] = UciSection(
        name="alt_sub_1",
        type="subscription_url",
        options={"section": "alt", "url": "https://alt.example/x"},
    )
    return config


# ---------------------------------------------------------------- url validation
@pytest.mark.parametrize(
    "url",
    ["https://example.com/sub?token=abc", "http://example.com/x", "/etc/forkop/local.yaml"],
)
def test_valid_urls_pass(url: str):
    assert _validate_subscription_url(url) == url


@pytest.mark.parametrize("url", ["", "   ", "ftp://x", "example.com/sub", "/etc/../secret", "https://x/" + "a" * 5000])
def test_invalid_urls_rejected(url: str):
    with pytest.raises(SafetyError):
        _validate_subscription_url(url)


# ---------------------------------------------------------------- child names / lookup
def test_next_free_child_name():
    config = config_with_subscriptions()
    assert _next_free_child_name(config, "main") == "main_sub_3"
    assert _next_free_child_name(config, "new") == "new_sub_1"


def test_find_by_name_index_tag_url():
    config = config_with_subscriptions()
    assert _find_subscription_children(config, "main_sub_2", None)[0].name == "main_sub_2"
    assert _find_subscription_children(config, "2", "main")[0].name == "main_sub_2"
    assert _find_subscription_children(config, "ALPHA", None)[0].name == "main_sub_1"
    assert _find_subscription_children(config, "https://alt.example/x", None)[0].name == "alt_sub_1"
    assert _find_subscription_children(config, "nope", None) == []


def test_find_scoped_to_section():
    config = config_with_subscriptions()
    # Индекс считается внутри области поиска (children секции main).
    assert _find_subscription_children(config, "1", "main")[0].name == "main_sub_1"
    assert _find_subscription_children(config, "1", "alt")[0].name == "alt_sub_1"


# ---------------------------------------------------------------- add plan
def test_plan_add_forkop_creates_child_with_defaults():
    router = load_router_from_fixture()
    builder = _plan_subscription_add(router, "AI", "https://sub.example/x", None)
    kinds = [op for op, _ in builder.ops]
    assert "create_section" in kinds
    sets = {option: value for op, args in builder.ops if op == "set_option" for option, value in [(args[1], args[2])]}
    child = builder.ops[0][1][0]
    assert child == "AI_sub_1"
    assert sets["section"] == "AI"
    assert sets["tag"] == "AI_sub_1"
    assert sets["url"] == "https://sub.example/x"
    assert sets["subscription_update_enabled"] == "1"
    assert sets["auto_user_agent"] == "1"
    assert sets["include_urltest_groups"] == "1"


def test_plan_add_forkop_skips_taken_names():
    router = FakeRouter(schema="forkop1", config=config_with_subscriptions())
    builder = _plan_subscription_add(router, "main", "https://n.example", None)
    assert builder.ops[0][1][0] == "main_sub_3"


def test_plan_add_forkop_options_override_defaults():
    router = FakeRouter(schema="forkop1", config=config_with_subscriptions())
    builder = _plan_subscription_add(router, "main", "https://n.example", {"subscription_update_interval": "6h"})
    sets = {args[1]: args[2] for op, args in builder.ops if op == "set_option"}
    assert sets["subscription_update_interval"] == "6h"


def test_plan_add_podkop_inline_options():
    config = UciConfig(package="podkop")
    config.sections["main"] = UciSection(name="main", type="section", options={"enabled": "1"})
    router = FakeRouter(schema="podkop", config=config)
    builder = _plan_subscription_add(router, "main", "/etc/local.yaml", {"custom": "v"})
    sets = {args[1]: args[2] for op, args in builder.ops if op == "set_option"}
    assert sets["subscription_url"] == "/etc/local.yaml"
    assert sets["subscription_update_enabled"] == "1"
    assert sets["custom"] == "v"
    assert not any(op == "create_section" for op, _ in builder.ops)


def test_plan_add_unknown_section_raises():
    from podkop_mcp.errors import ConfigError

    router = FakeRouter(schema="forkop1", config=config_with_subscriptions())
    with pytest.raises(ConfigError):
        _plan_subscription_add(router, "ghost", "https://x.example", None)


# ---------------------------------------------------------------- delete plan
def test_plan_delete_forkop_by_identifier():
    router = FakeRouter(schema="forkop1", config=config_with_subscriptions())
    builder, removed = _plan_subscription_delete(router, "main_sub_1", None)
    assert removed == ["main_sub_1"]
    assert ("delete_section", ("main_sub_1",)) in builder.ops


def test_plan_delete_scoped_by_section():
    router = FakeRouter(schema="forkop1", config=config_with_subscriptions())
    # Индекс 2 внутри секции main — это main_sub_2, а не alt_sub_*.
    _, removed = _plan_subscription_delete(router, "2", "main")
    assert removed == ["main_sub_2"]


def test_plan_delete_not_found():
    router = FakeRouter(schema="forkop1", config=config_with_subscriptions())
    with pytest.raises(BackendError):
        _plan_subscription_delete(router, "missing", None)


def test_plan_delete_podkop_removes_options():
    config = UciConfig(package="podkop")
    config.sections["main"] = UciSection(
        name="main", type="section", options={"subscription_url": "https://x", "subscription_update_enabled": "1"}
    )
    router = FakeRouter(schema="podkop", config=config)
    builder, removed = _plan_subscription_delete(router, "main", None)
    assert removed == ["main.subscription_url"]
    ops = {(op, args[:2]) for op, args in builder.ops if op == "delete_option"}
    assert ("delete_option", ("main", "subscription_url")) in ops
    assert ("delete_option", ("main", "subscription_update_enabled")) in ops


def test_plan_delete_podkop_without_url_raises():
    config = UciConfig(package="podkop")
    config.sections["main"] = UciSection(name="main", type="section", options={"enabled": "1"})
    router = FakeRouter(schema="podkop", config=config)
    with pytest.raises(BackendError):
        _plan_subscription_delete(router, "main", None)


# ---------------------------------------------------------------- summarize / mask
def test_summarize_section_lists_children():
    config = config_with_subscriptions()
    summary = _summarize_section(config, "main", config.section("main"), reveal=False)
    assert summary["enabled"] is True
    assert len(summary["subscriptions"]) == 2
    masked = summary["subscriptions"][0]["url"]
    assert "token123" not in str(masked)
    revealed = _summarize_section(config, "main", config.section("main"), reveal=True)
    assert revealed["subscriptions"][0]["url"] == "https://sub1.example/token123"


def test_urltest_options_are_masked():
    """Опции urltest прогоняются через mask_value при reveal=False."""

    config = UciConfig(package="forkop")
    config.sections["main"] = UciSection(name="main", type="section", options={"enabled": "1"})
    config.sections["ut"] = UciSection(
        name="main_urltest_1",
        type="urltest",
        options={"section": "main", "url": "https://probe.example/longsecrettoken123456"},
    )
    summary = _summarize_section(config, "main", config.section("main"), reveal=False)
    assert "longsecrettoken123456" not in json.dumps(summary)
    revealed = _summarize_section(config, "main", config.section("main"), reveal=True)
    assert revealed["urltest"][0]["options"]["url"] == "https://probe.example/longsecrettoken123456"


def test_podkop_add_warns_on_overwrite():
    """Перезапись существующей подписки podkop даёт warning в плане."""

    config = UciConfig(package="podkop")
    config.sections["main"] = UciSection(
        name="main", type="section", options={"subscription_url": "https://old.example/sub"}
    )
    router = FakeRouter(schema="podkop", config=config)
    builder = _plan_subscription_add(router, "main", "https://new.example/sub", None)
    assert builder.warnings and "ПЕРЕЗАПИСАН" in builder.warnings[0]
    # Тот же URL повторно — без предупреждения.
    builder2 = _plan_subscription_add(router, "main", "https://old.example/sub", None)
    assert builder2.warnings == []


def test_mask_respects_reveal():
    # URL с токеном в query маскируется redaction-модулем.
    assert _mask("https://host/sub?token=abc", "url", reveal=False) != "https://host/sub?token=abc"
    assert _mask("plain", "enabled", reveal=False) == "plain"
    assert _mask("https://x/y", "url", reveal=True) == "https://x/y"


# ---------------------------------------------------------------- wrappers (MCP)
class FakeResult:
    def __init__(self, stdout: str = "", exit_code: int = 0) -> None:
        self.stdout = stdout
        self.stderr = ""
        self.exit_code = exit_code

    @property
    def ok(self) -> bool:
        return self.exit_code == 0

    def tail(self) -> str:
        return self.stdout


class FakeSession:
    """Пустышка: FakeRouter не использует SSH-сессию."""

    def __init__(self) -> None: ...


class FakeSettings:
    """Настройки, нужные инструментам: read_only и target."""

    def __init__(self, *, read_only: bool = False) -> None:
        self.read_only = read_only
        self.backup_dir = "/etc/podkop-mcp/backups"
        self.backup_keep = 5
        self.max_output_bytes = 200_000


class FakeTarget:
    def __init__(self) -> None:
        self.name = "fake"


class FakeServices:
    """Подменяет Services: router_for получает FakeRouter."""

    def __init__(self, router: FakeRouter, settings: FakeSettings | None = None) -> None:
        self._router = router
        self.settings = settings or FakeSettings()

    def require_targets(self) -> None: ...

    def router(self, name: str | None = None) -> FakeRouter:
        return self._router


def make_mcp(router: FakeRouter) -> Any:
    from mcp.server import MCPServer

    from podkop_mcp.tools import connections as connections_mod

    mcp = MCPServer("test")
    connections_mod.register(mcp, FakeServices(router))  # type: ignore[arg-type]
    return mcp


def call(mcp: Any, name: str, arguments: dict[str, Any]) -> Any:
    from mcp.server.mcpserver.exceptions import ToolError

    async def _run() -> Any:
        try:
            return await mcp.call_tool(name, arguments)
        except ToolError as exc:
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


def test_wrapper_connections_config_masks_urls():
    router = FakeRouter(schema="forkop1", config=config_with_subscriptions())
    result = call(make_mcp(router), "podkop_connections_config", {})
    assert result["counts"]["sections"] == 1
    assert "token123" not in json.dumps(result["sections"])
    revealed = call(make_mcp(router), "podkop_connections_config", {"reveal_secrets": True})
    assert "token123" in json.dumps(revealed["sections"])


def test_wrapper_subscription_add_dry_run_and_plan():
    router = FakeRouter(schema="forkop1", config=config_with_subscriptions())
    mcp = make_mcp(router)
    result = call(mcp, "podkop_subscription_add", {"section": "main", "url": "https://n.example/sub"})
    assert result["applied"] is False and result["dry_run"] is True
    # Повторный вызов не менял конфиг.
    assert len(router.config().sections_of_type("subscription_url")) == 3


def test_wrapper_subscription_add_invalid_url_is_tool_error():
    router = FakeRouter(schema="forkop1", config=config_with_subscriptions())
    result = call(make_mcp(router), "podkop_subscription_add", {"section": "main", "url": "not-a-url"})
    assert "__tool_error__" in result and "http" in result["__tool_error__"]


def test_wrapper_subscription_update_dry_run_is_structural():
    router = CliRouter(commands={"subscription_update"})
    result = call(make_mcp(router), "podkop_subscription_update", {})
    assert result["applied"] is False
    assert result["dry_run"] is True
    assert result["mode"] == "sync"


def test_wrapper_subscription_delete_by_index_dry_run():
    router = FakeRouter(schema="forkop1", config=config_with_subscriptions())
    result = call(make_mcp(router), "podkop_subscription_delete", {"identifier": "1", "section": "main"})
    assert result["applied"] is False
    assert result["removed"] == ["main_sub_1"]
    # Dry-run не меняет конфиг: child на месте (ключ sub1, имя main_sub_1).
    assert any(child.name == "main_sub_1" for child in router.config().sections.values())


def test_wrapper_proxies_list_group_and_missing():
    router = FakeRouter(schema="forkop1", config=config_with_subscriptions())
    mcp = make_mcp(router)

    # Подменяем Clash в модуле connections на мини-двойник.
    import podkop_mcp.tools.connections as connections_mod

    class MiniClash:
        def __init__(self, _router: Any) -> None: ...
        def proxies_summary(self) -> dict[str, Any]:
            return {
                "groups": [{"name": "main-out", "now": "RIX", "all": ["RIX"], "delay": 10}],
                "nodes": [],
                "group_count": 1,
                "node_count": 0,
                "selected": [],
            }

    connections_mod.Clash = MiniClash  # type: ignore[assignment]
    result = call(mcp, "podkop_proxies_list", {"group": "main-out"})
    assert result["now"] == "RIX" and result["all"] == ["RIX"]
    missing = call(mcp, "podkop_proxies_list", {"group": "ghost"})
    assert "__tool_error__" in missing and "ghost" in missing["__tool_error__"]


class CliRouter(FakeRouter):
    """FakeRouter с ответами CLI для тестов обновления подписок."""

    def __init__(self, *, commands: set[str], start_ok: bool = True, status: str = '{"state":"done"}') -> None:
        super().__init__(schema="forkop1")
        self.profile.commands = commands
        self.profile.kind = "forkop"
        self.settings = FakeSettings()
        self._start_ok = start_ok
        self._status = status
        self.calls: list[tuple[str, tuple[str, ...]]] = []

    def cli(self, *args: str, timeout: float | None = None) -> Any:
        self.calls.append((args[0], tuple(args[1:])))
        if args[0] == "subscription_update_async":
            exit_code = 0 if self._start_ok else 1
            return FakeResult("started" if self._start_ok else "", exit_code=exit_code)
        if args[0] == "subscription_update_status":
            return FakeResult(self._status)
        if args[0] == "subscription_update":
            return FakeResult("updated")
        raise AssertionError(f"неожиданная команда {args[0]}")


@pytest.fixture()
def fast_poll(monkeypatch: pytest.MonkeyPatch) -> None:
    import podkop_mcp.tools.connections as connections_mod

    monkeypatch.setattr(connections_mod, "SUBSCRIPTION_POLL_INTERVAL", 0.001)


def test_async_update_completed(fast_poll: None):
    router = CliRouter(commands={"subscription_update_async", "subscription_update_status"})
    result = call(make_mcp(router), "podkop_subscription_update", {"apply": True, "timeout": 10})
    assert result["mode"] == "async"
    assert result["ok"] is True
    assert result["timed_out"] is False
    assert result["status"]["state"] == "done"


def test_async_update_json_with_leading_noise(fast_poll: None):
    """JSON со сторонним префиксом в stdout всё равно распознаётся."""

    router = CliRouter(commands={"subscription_update_async", "subscription_update_status"}, status='log line {"state":"ok"}')
    result = call(make_mcp(router), "podkop_subscription_update", {"apply": True, "timeout": 10})
    assert result["ok"] is True and result["status"]["state"] == "ok"


def test_async_update_start_failure_is_error(fast_poll: None):
    router = CliRouter(commands={"subscription_update_async", "subscription_update_status"}, start_ok=False)
    result = call(make_mcp(router), "podkop_subscription_update", {"apply": True, "timeout": 10})
    assert "__tool_error__" in result and "async" in result["__tool_error__"]


def test_async_update_error_state_reported(fast_poll: None):
    router = CliRouter(commands={"subscription_update_async", "subscription_update_status"}, status='{"state":"failed"}')
    result = call(make_mcp(router), "podkop_subscription_update", {"apply": True, "timeout": 10})
    # Цикл завершился (терминальное состояние), но ok=false.
    assert result["timed_out"] is False and result["ok"] is False


def test_sync_update_success_and_failure():
    router = CliRouter(commands={"subscription_update"})
    result = call(make_mcp(router), "podkop_subscription_update", {"apply": True, "wait": False})
    assert result["mode"] == "sync" and result["ok"] is True

    class FailingRouter(CliRouter):
        def cli(self, *args: str, timeout: float | None = None) -> Any:
            if args[0] == "subscription_update":
                return FakeResult("", exit_code=2)
            return super().cli(*args, timeout=timeout)

    failing = FailingRouter(commands={"subscription_update"})
    result = call(make_mcp(failing), "podkop_subscription_update", {"apply": True, "wait": False})
    assert "__tool_error__" in result and "2" in result["__tool_error__"]


def test_read_only_blocks_subscription_update():
    router = CliRouter(commands={"subscription_update"})
    router.settings = FakeSettings(read_only=True)  # инструмент читает router.settings
    services = FakeServices(router, settings=FakeSettings(read_only=True))
    from mcp.server import MCPServer

    from podkop_mcp.tools import connections as connections_mod

    mcp = MCPServer("test")
    connections_mod.register(mcp, services)  # type: ignore[arg-type]
    result = call(mcp, "podkop_subscription_update", {"apply": True, "wait": False})
    assert "__tool_error__" in result and "READ_ONLY" in result["__tool_error__"]
