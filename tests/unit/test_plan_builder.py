"""Тесты планировщика изменений на фейковом роутере Forkop 1.x."""

from __future__ import annotations

import pytest
from fakes import FakeRouter, FakeSession, assert_uci_path, make_forkop1_profile, plan_paths

from podkop_mcp.config import Settings
from podkop_mcp.domains_core import parse_entries, render_value
from podkop_mcp.errors import ConfigError, SafetyError
from podkop_mcp.redaction import MASK
from podkop_mcp.tools.common import preview_or_apply
from podkop_mcp.uci import parse_path

SUBSCRIPTION_URL = "https://sub.example.com/abcdefghijklmnopqrstuvwxyz"


def _ops(plan) -> list[tuple[str, str, object]]:
    return [(op.op, op.path, op.value) for op in plan.ops]


# --------------------------------------------------------------------------- option vs list


def test_set_option_domain_has_no_add_list(forkop1_router: FakeRouter) -> None:
    """Forkop 1.x: домены пишутся как option, ``uci add_list`` в плане нет."""

    config = forkop1_router.config()
    current = parse_entries(config.section("main").value("domain"), "option_text").entries
    new_value = render_value([*current, "new.example"], "option_text")

    builder = forkop1_router.builder("domains-add")
    builder.set_option("main", "domain", new_value)
    plan = builder.build()

    ops = _ops(plan)
    assert [op[0] for op in ops] == ["set"]
    assert ops[0][1] == "forkop.main.domain"
    assert isinstance(ops[0][2], str)
    assert "add_list" not in [change.op for change in plan.changes]
    assert all("add_list" not in line for line in plan.preview if "domain" in line)
    assert plan.package == "forkop"
    assert plan.target == forkop1_router.name
    assert len(plan.changes) == 1
    assert plan.to_dict()["change_count"] == 1
    # diff показывает тип до и после
    change = plan.changes[0]
    assert change.path == "forkop.main.domain"
    assert change.op == "set"
    assert "new.example" in change.after
    assert "hornet.com" in change.before
    # предпросмотр заканчивается коммитом и reload
    assert plan.preview[-2:] == ["uci commit forkop", "/etc/init.d/forkop reload"]


def test_set_option_keeps_string_when_only_one_value(forkop1_router: FakeRouter) -> None:
    """Даже одно значение остаётся строкой — иначе станет точным domain."""

    builder = forkop1_router.builder("domains-replace")
    builder.set_option("main", "domain", "only.example")
    plan = builder.build()

    (op,) = plan.ops
    assert op.op == "set"
    assert op.value == "only.example"
    assert "only.example" in plan.preview[0]


def test_set_list_generates_delete_and_add_list(forkop1_router: FakeRouter) -> None:
    """``set_list`` = delete + add_list по каждому значению."""

    builder = forkop1_router.builder("option-list")
    builder.set_list("main", "community_lists", ["discord", "youtube"])
    plan = builder.build()

    ops = _ops(plan)
    assert [op[0] for op in ops] == ["set_list"]
    preview = [line for line in plan.preview if "community_lists" in line]
    assert preview[0] == "uci delete 'forkop.main.community_lists'"
    assert preview[1] == "uci add_list 'forkop.main.community_lists=discord'"
    assert preview[2] == "uci add_list 'forkop.main.community_lists=youtube'"
    assert len(preview) == 3

    change = plan.changes[0]
    assert change.before[0] == "telegram"
    assert change.after == ["discord", "youtube"]


def test_empty_set_list_emits_delete_only(forkop1_router: FakeRouter) -> None:
    builder = forkop1_router.builder("option-list-empty")
    builder.set_list("AI", "community_lists", [])
    plan = builder.build()

    preview = [line for line in plan.preview if "community_lists" in line]
    assert preview == ["uci delete 'forkop.AI.community_lists'"]
    assert plan.changes[0].after == []


def test_add_list_and_del_list_preview(forkop1_router: FakeRouter) -> None:
    builder = forkop1_router.builder("lists")
    builder.add_list("main", "community_lists", ["new_list"])
    builder.del_list("main", "community_lists", ["anime"])
    plan = builder.build()

    preview = "\n".join(plan.preview)
    assert "uci add_list 'forkop.main.community_lists=new_list'" in preview
    assert "uci del_list 'forkop.main.community_lists=anime'" in preview
    assert plan.changes[0].after[:2] == ["telegram", "tiktok"]
    assert "new_list" in plan.changes[0].after
    assert "anime" not in plan.changes[0].after


def test_empty_add_del_list_does_not_create_apply_script(forkop1_router: FakeRouter) -> None:
    """Пустые add_list/del_list не должны давать «холостой» commit+reload."""

    builder = forkop1_router.builder("lists-noop")
    builder.add_list("main", "community_lists", [])
    builder.del_list("main", "community_lists", ["no-such-list"])
    assert builder.ops == []
    with pytest.raises(ConfigError, match="План пуст"):
        builder.build()


def test_duplicate_add_list_is_noop(forkop1_router: FakeRouter) -> None:
    builder = forkop1_router.builder("lists-dup")
    builder.add_list("main", "community_lists", ["telegram"])
    assert builder.ops == []


# --------------------------------------------------------------------------- dry-run и маскирование


def test_dry_run_masks_subscription_url(forkop1_router: FakeRouter) -> None:
    """Предпросмотр не раскрывает URL подписки и путь к ключу."""

    builder = forkop1_router.builder("subscription")
    builder.set_option("cfg076a7c", "url", SUBSCRIPTION_URL)
    response = preview_or_apply(forkop1_router, builder.build(), apply=False)

    assert response["applied"] is False
    assert response["dry_run"] is True
    assert response["package"] == "forkop"
    assert response["target"] == forkop1_router.name

    preview = "\n".join(response["preview"])
    assert f"https://sub.example.com/{MASK}" in preview
    assert "abcdefghijklmnopqrstuvwxyz" not in preview
    # на роутер ничего не уходило и план не применялся
    assert forkop1_router.applied == []
    assert forkop1_router.session.calls == []


def test_dry_run_masks_proxy_string(forkop1_router: FakeRouter) -> None:
    builder = forkop1_router.builder("proxy")
    builder.set_option("main", "proxy_string", "vless://uuid@host:443?pbk=key")
    plan = builder.build()

    assert MASK in "\n".join(plan.preview)
    assert "pbk=key" not in "\n".join(plan.preview)


def test_long_value_is_truncated_in_preview(forkop1_router: FakeRouter) -> None:
    long_value = "a" * 400
    builder = forkop1_router.builder("long")
    builder.set_option("main", "label", long_value)
    plan = builder.build()

    line = next(line for line in plan.preview if "label" in line)
    assert line.endswith("...'")
    assert len(line) < 200


# --------------------------------------------------------------------------- diff


def test_diff_records_all_changes(forkop1_router: FakeRouter) -> None:
    """Diff фиксирует каждое изменённое/удалённое/добавленное значение."""

    builder = forkop1_router.builder("multi-change")
    builder.set_option("main", "enabled", "0")
    builder.set_option("main", "label", "мой-роутер")
    builder.delete_option("main", "dashboard_filter_mode")
    builder.set_option("AI", "domain", "chatgpt.com")
    plan = builder.build()

    paths = {change.path: change for change in plan.changes}
    assert set(paths) == {
        "forkop.main.enabled",
        "forkop.main.label",
        "forkop.main.dashboard_filter_mode",
        "forkop.AI.domain",
    }
    assert paths["forkop.main.enabled"].before == "1"
    assert paths["forkop.main.enabled"].after == "0"
    assert paths["forkop.main.dashboard_filter_mode"].op == "delete"
    assert paths["forkop.main.dashboard_filter_mode"].after is None
    assert paths["forkop.AI.domain"].after == "chatgpt.com"
    assert paths["forkop.AI.domain"].before == "chatgpt.com\nopenai.com\ngrok.com"

    serialized = plan.to_dict()
    assert serialized["change_count"] == len(plan.changes)
    assert serialized["reason"] == "multi-change"
    assert serialized["package"] == "forkop"


def test_diff_ignores_unchanged_option(forkop1_router: FakeRouter) -> None:
    config = forkop1_router.config()
    same_domain = config.section("main").value("domain")

    builder = forkop1_router.builder("noop")
    builder.set_option("main", "domain", same_domain)
    builder.set_option("main", "enabled", "0")
    plan = builder.build()

    assert [change.path for change in plan.changes] == ["forkop.main.enabled"]


def test_delete_missing_option_warns_and_keeps_plan_valid(forkop1_router: FakeRouter) -> None:
    builder = forkop1_router.builder("cleanup")
    builder.delete_option("main", "no_such_option")
    builder.set_option("main", "enabled", "0")
    plan = builder.build()

    assert plan.warnings and "no_such_option" in plan.warnings[0]
    assert [change.path for change in plan.changes] == ["forkop.main.enabled"]


def test_empty_plan_is_rejected(forkop1_router: FakeRouter) -> None:
    builder = forkop1_router.builder("empty")
    with pytest.raises(ConfigError, match="План пуст"):
        builder.build()


def test_reason_is_sanitized_for_backup_name(forkop1_router: FakeRouter) -> None:
    """``Router.builder`` санитизирует reason: он попадает в имя бэкапа."""

    builder = forkop1_router.builder("Domain ADD: example.com!!")
    builder.set_option("main", "enabled", "0")
    plan = builder.build()

    assert plan.reason == "domain-add-example-com"
    assert forkop1_router.backup(plan.reason)["id"].endswith("-domain-add-example-com.uci")


def test_plan_uses_real_uci_paths(forkop1_router: FakeRouter) -> None:
    """Все пути из плана — валидные UCI-пути пакета forkop."""

    builder = forkop1_router.builder("paths")
    builder.set_option("main", "enabled", "0")
    builder.set_list("main", "community_lists", ["discord"])
    plan = builder.build()

    assert plan_paths(plan)
    for path in plan_paths(plan) + [op.path for op in plan.ops]:
        package, _section, _option = assert_uci_path(path)
        assert package == "forkop"


# --------------------------------------------------------------------------- обработка ошибок


def test_invalid_option_and_unknown_section_are_rejected(forkop1_router: FakeRouter) -> None:
    """Планировщик не даёт выйти за пределы пакета и валидирует имена."""

    builder = forkop1_router.builder("bad-option")

    with pytest.raises(ConfigError, match="не найдена"):
        builder.set_option("no-such-section", "enabled", "1")
    with pytest.raises(ConfigError, match="Недопустимое имя UCI-опции"):
        builder.set_option("main", "bad-option", "1")
    with pytest.raises(ConfigError, match="Недопустимое имя UCI-опции"):
        builder.set_option("main", "../escape", "1")


def test_create_and_delete_section(forkop1_router: FakeRouter) -> None:
    builder = forkop1_router.builder("section-crud")
    builder.create_section("newsec", "section")
    builder.set_option("newsec", "enabled", "1")
    builder.set_option("newsec", "action", "connection")
    builder.delete_section("youtube")
    plan = builder.build()

    preview = "\n".join(plan.preview)
    assert "uci set 'forkop.newsec=section'" in preview
    assert "uci delete 'forkop.youtube'" in preview
    paths = {change.path: change for change in plan.changes}
    assert paths["forkop.newsec.enabled"].after == "1"
    assert paths["forkop.youtube"].op == "delete"

    with pytest.raises(ConfigError, match="уже существует"):
        builder.create_section("main", "section")
    with pytest.raises(ConfigError, match="не найдена"):
        builder.delete_section("ghost")
    with pytest.raises(ConfigError, match="Недопустимое имя секции"):
        builder.create_section("bad name", "section")


def test_plan_builder_reads_config_through_router(forkop1_config) -> None:
    """Планировщик берёт исходное состояние через ``router.config()``."""

    session = FakeSession(strict=True)
    router = FakeRouter(profile=make_forkop1_profile(), config=forkop1_config, session=session)

    builder = router.builder("check")
    builder.set_option("main", "enabled", "0")
    plan = builder.build()

    assert plan.package == "forkop"
    # план не ходит на роутер: конфиг взят из фейка
    assert session.calls == []


# --------------------------------------------------------------------------- применение


def test_apply_plan_does_not_touch_ssh(forkop1_router: FakeRouter) -> None:
    """apply_plan на фейке не отправляет ни одной команды."""

    builder = forkop1_router.builder("apply")
    builder.set_option("main", "enabled", "0")
    plan = builder.build()

    result = forkop1_router.apply_plan(plan)

    assert result["ok"] is True
    assert result["target"] == forkop1_router.name
    assert result["package"] == "forkop"
    assert result["backup_id"]
    assert forkop1_router.applied == [plan]
    assert forkop1_router.session.calls == []
    assert forkop1_router.run_calls == []


def test_apply_via_preview_or_apply_returns_applied(forkop1_router: FakeRouter) -> None:
    builder = forkop1_router.builder("apply")
    builder.set_option("main", "enabled", "0")

    response = preview_or_apply(forkop1_router, builder.build(), apply=True)

    assert response["applied"] is True
    assert response["dry_run"] is False
    assert response["ok"] is True
    assert len(forkop1_router.applied) == 1


def test_apply_plan_respects_read_only(forkop1_config) -> None:
    session = FakeSession(strict=False)
    router = FakeRouter(
        profile=make_forkop1_profile(),
        config=forkop1_config,
        session=session,
        settings=Settings(read_only=True),
    )
    builder = router.builder("read-only")
    builder.set_option("main", "enabled", "0")
    plan = builder.build()

    with pytest.raises(SafetyError, match="READ_ONLY"):
        router.apply_plan(plan)
    assert router.applied == []


def test_apply_empty_plan_is_rejected(forkop1_router: FakeRouter) -> None:
    from podkop_mcp.router import ChangePlan

    empty = ChangePlan(
        target=forkop1_router.name,
        package="forkop",
        reason="empty",
        changes=[],
        preview=[],
    )
    with pytest.raises(ConfigError, match="план пуст"):
        forkop1_router.apply_plan(empty)


# --------------------------------------------------------------------------- пути


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("forkop.main.domain", ("forkop", "main", "domain")),
        ("forkop.settings", ("forkop", "settings", None)),
    ],
)
def test_plan_paths_are_uci_compatible(path: str, expected: tuple[str, str, str | None]) -> None:
    """Пути в плане разбираются тем же ``parse_path``, что и в инструментах."""

    assert parse_path(path) == expected


def test_alias_cfg_is_written_as_anonymous_uci_section(forkop1_router: FakeRouter) -> None:
    """``cfg...`` из ubus превращается в ``@subscription_url[N]`` в UCI-команде."""

    builder = forkop1_router.builder("subscription")
    builder.set_option("cfg076a7c", "url", SUBSCRIPTION_URL)
    plan = builder.build()

    assert plan.changes[0].path == "forkop.@subscription_url[0].url"
    assert "forkop.@subscription_url[0].url" in "\n".join(plan.preview)


def test_render_ops_matches_preview_for_list_and_empty_list(forkop1_router: FakeRouter) -> None:
    """Apply-скрипт не должен добавлять ``uci set ''`` для пустого списка."""

    from podkop_mcp.router import Router

    builder = forkop1_router.builder("render")
    builder.set_list("AI", "community_lists", [])
    builder.add_list("main", "community_lists", ["new_list"])
    plan = builder.build()
    script = Router._render_ops(object.__new__(Router), plan)

    assert "uci delete 'forkop.AI.community_lists'" in script
    assert "uci add_list 'forkop.AI.community_lists'" not in script
    assert "uci add_list 'forkop.main.community_lists'=" in script
    assert script.rstrip().endswith("uci commit 'forkop'")
    assert "uci revert" not in script
