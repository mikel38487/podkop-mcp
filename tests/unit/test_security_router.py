"""Регрессионные тесты безопасности: пакет UCI и маскирование изменений."""

from __future__ import annotations

import time

import pytest
from fakes import FakeRouter, FakeSession, make_forkop1_profile, make_forkop2_profile

from podkop_mcp.config import Settings, TargetConfig
from podkop_mcp.errors import ConfigError
from podkop_mcp.router import ChangePlan, PlanBuilder, Router
from podkop_mcp.uci import UciConfig, UciSection


def _router() -> Router:
    target = TargetConfig(name="test", host="router.invalid")
    router = Router(FakeSession(), target, Settings(targets={"test": target}), name="test")
    router._profile = make_forkop1_profile()
    router._profile_checked_at = time.monotonic()
    return router


def _fake_router() -> FakeRouter:
    config = UciConfig(package="forkop")
    config.sections["main"] = UciSection(name="main", type="section", options={"enabled": "1"})
    return FakeRouter(profile=make_forkop1_profile(), config=config)


def test_config_rejects_foreign_or_injected_package() -> None:
    router = _router()

    with pytest.raises(ConfigError, match="только для пакета"):
        router.config("dnsmasq")
    with pytest.raises(ConfigError):
        router.config("forkop; reboot")
    with pytest.raises(ConfigError):
        router.config("Forkop")


def test_profile_revalidates_after_version_change(monkeypatch: pytest.MonkeyPatch) -> None:
    target = TargetConfig(name="test", host="router.invalid")
    calls = 0

    def fake_detect(runner: object, *, prefer: str = "auto") -> object:
        nonlocal calls
        calls += 1
        return make_forkop2_profile()

    monkeypatch.setattr("podkop_mcp.router.detect_profile", fake_detect)
    settings = Settings(targets={"test": target}, profile_ttl=1.0)
    router = Router(FakeSession(responses={"uci -q get": "2.0.0"}), target, settings, name="test")
    router._profile = make_forkop1_profile()
    router._profile_checked_at = 0.0

    profile = router.profile
    assert profile.schema == "forkop2"
    assert calls == 1
    # в пределах TTL повторной проверки нет
    assert router.profile.schema == "forkop2"
    assert calls == 1


def test_profile_probe_failure_keeps_cached_profile() -> None:
    target = TargetConfig(name="test", host="router.invalid")
    router = Router(FakeSession(strict=True), target, Settings(profile_ttl=1.0), name="test")
    router._profile = make_forkop1_profile()
    router._profile_checked_at = 0.0

    assert router.profile.schema == "forkop1"


def test_render_ops_rejects_injected_plan_package() -> None:
    plan = ChangePlan(
        target="test",
        package="forkop; reboot",
        reason="evil",
        changes=[],
        preview=[],
    )
    with pytest.raises(ConfigError):
        Router._render_ops(object.__new__(Router), plan)  # type: ignore[arg-type]


def test_domain_field_prefers_factual_destinations() -> None:
    config = UciConfig(package="forkop")
    config.sections["main"] = UciSection(
        name="main",
        type="section",
        options={"enabled": "1", "destinations": "a.com\nb.com", "domain": "old.com"},
    )
    router = FakeRouter(profile=make_forkop1_profile(), config=config)

    data = router.read_domains("main")

    assert data["option"] == "destinations"
    assert data["storage"] == "option_multiline"
    assert data["entries"] == ["a.com", "b.com"]


def test_forkop2_refuses_legacy_domain_field() -> None:
    config = UciConfig(package="forkop")
    config.sections["main"] = UciSection(
        name="main",
        type="section",
        options={"enabled": "1", "domain": "legacy.com"},
    )
    router = FakeRouter(profile=make_forkop2_profile(), config=config)

    with pytest.raises(ConfigError, match="legacy"):
        router.read_domains("main")


def test_changes_mask_url_values() -> None:
    router = _fake_router()
    builder = PlanBuilder(router, "forkop", "url")
    builder.set_option("main", "domain", "example.com")
    data = builder.build().to_dict()
    assert data["changes"][0]["after"] == "example.com"

    builder = PlanBuilder(router, "forkop", "url")
    builder.set_option("main", "url", "https://sub.example.com/secret-token-123")
    data = builder.build().to_dict()
    assert data["changes"][0]["after"] == "https://sub.example.com/***"
