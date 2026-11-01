"""Тесты разбора UCI (``podkop_mcp.uci``)."""

from __future__ import annotations

from typing import Any

import pytest

from podkop_mcp.errors import ConfigError
from podkop_mcp.uci import (
    UciConfig,
    UciSection,
    diff_configs,
    parse_path,
    parse_ubus_uci,
    parse_uci_export,
    split_words,
)

# --------------------------------------------------------------------------- ubus


def test_parse_ubus_forkop1_keeps_types(forkop1_ubus: dict[str, Any]) -> None:
    """Типы из ubus сохраняются: ``domain`` — строка, ``community_lists`` — список."""

    config = parse_ubus_uci(forkop1_ubus, "forkop")

    assert config.package == "forkop"
    assert "main" in config.sections
    assert len(config.sections) == len(forkop1_ubus["values"])

    main = config.section("main")
    assert main.type == "section"
    assert main.index == 5
    assert main.anonymous is False
    assert isinstance(main.value("domain"), str)
    assert main.value("domain").startswith("game8.co ")
    assert main.list_value("community_lists")[:2] == ["telegram", "tiktok"]
    assert main.is_list("community_lists") is True
    assert main.is_list("domain") is False
    # служебные ключи не попадают в options
    assert all(not key.startswith(".") for key in main.options)

    settings = config.section("settings")
    assert settings.type == "settings"
    assert settings.value("config_version") == "1.0.5"
    assert settings.list_value("dns_server") == ["77.88.8.8"]


def test_parse_ubus_anonymous_child_sections_and_children(forkop1_config: UciConfig) -> None:
    """Дочерние секции находятся по полю ``section``."""

    assert forkop1_config.children("subscription_url", "main")
    assert len(forkop1_config.children("subscription_url", "main")) == 1
    assert len(forkop1_config.children("urltest", "main")) == 1

    subscriptions = forkop1_config.children("subscription_url", "main")
    assert subscriptions[0].anonymous is True
    assert subscriptions[0].value("url").startswith("https://sub.example.com/")

    assert len(forkop1_config.top_sections()) == 5
    assert {section.name for section in forkop1_config.top_sections()} == {
        "AI",
        "youtube",
        "zapret",
        "exclusion",
        "main",
    }


def test_parse_ubus_wrapper_and_errors() -> None:
    """Принимается и обёртка values, и «плоский» словарь; ошибки — ConfigError."""

    flat = {"main": {".type": "section", "domain": "a.com"}}
    assert parse_ubus_uci(flat, "forkop").section("main").value("domain") == "a.com"

    wrapped = {"values": {"main": {".type": "section", "domain": "b.com"}}}
    assert parse_ubus_uci(wrapped, "forkop").section("main").value("domain") == "b.com"

    # секции без .type пропускаются
    assert parse_ubus_uci({"values": {"x": {"domain": "y"}}}, "p").sections == {}

    with pytest.raises(ConfigError, match="ubus uci get"):
        parse_ubus_uci({"error": "Object not found"}, "forkop")
    with pytest.raises(ConfigError, match="неожиданный формат"):
        parse_ubus_uci({"values": []}, "forkop")


def test_get_path_and_public_dict(forkop1_config: UciConfig) -> None:
    assert forkop1_config.get_path("forkop.main.enabled") == "1"
    assert forkop1_config.get_path("forkop.main")[".type"] == "section"

    public = forkop1_config.to_public_dict()
    assert public["package"] == "forkop"
    assert public["count"] == len(forkop1_config.sections)
    assert public["sections"]["main"][".name"] == "main"

    with pytest.raises(ConfigError, match="не найдена"):
        forkop1_config.section("no-such")
    with pytest.raises(ConfigError, match="не найдена"):
        forkop1_config.get_path("forkop.no-such.enabled")


# --------------------------------------------------------------------------- uci export


def test_parse_uci_export_podkop_defaults(podkop_default_text: str) -> None:
    """Podkop: список user_domains закомментирован — секция только с community_lists."""

    config = parse_uci_export(podkop_default_text, "podkop")

    assert set(config.sections) == {"settings", "main"}
    settings = config.section("settings")
    assert settings.value("dns_type") == "udp"
    assert settings.list_value("source_network_interfaces") == ["br-lan"]

    main = config.section("main")
    assert main.type == "section"
    assert main.value("connection_type") == "proxy"
    assert main.value("proxy_config_type") == "url"
    assert main.value("proxy_string") == ""
    assert main.list_value("community_lists") == ["russia_inside"]
    # закомментированные строки не разбираются
    assert main.value("user_domains") is None
    assert settings.value("output_network_interface") is None


def test_parse_uci_export_forkop2_defaults(forkop2_default_text: str) -> None:
    """Forkop 2.x: ``config_version`` в settings, списки dns_server."""

    config = parse_uci_export(forkop2_default_text, "forkop")

    assert set(config.sections) == {"settings"}
    settings = config.section("settings")
    assert settings.value("config_version") == "2.0.0"
    assert settings.list_value("dns_server") == ["77.88.8.8"]
    assert settings.list_value("source_network_interfaces") == ["br-lan"]
    assert settings.value("log_level") == "warn"
    # доменов в дефолтном конфиге 2.x нет — схема сменилась на destinations
    assert settings.value("destinations") is None


def test_parse_uci_export_multiline_values(forkop1_export_config) -> None:
    """Многострочное значение в одинарных кавычках и анонимные секции."""

    config = forkop1_export_config

    assert set(config.sections) == {"settings", "AI", "youtube", "zapret", "exclusion", "main"} | {
        "@subscription_url[0]",
        "@subscription_url[1]",
        "@urltest[0]",
        "@urltest[1]",
    }
    assert config.section("AI").value("domain") == "chatgpt.com\nopenai.com\ngrok.com"
    assert config.section("main").value("domain").startswith("game8.co ")

    # анонимные секции получают синтетические имена
    subscription = [section for section in config.sections.values() if section.type == "subscription_url"]
    assert len(subscription) == 2
    assert all(section.anonymous for section in subscription)
    assert config.children("subscription_url", "main")[0].value("url") == "MASKED"
    assert config.children("urltest", "exclusion")[0].value("name") == "тест2"


def test_parse_uci_export_escaped_quotes() -> None:
    """Пары одинарных кавычек в значении не ломают построчный разбор."""

    text = "\n".join(
        [
            "config section 'test'",
            "\toption plain 'value'",
            "\toption quoted 'it''s ok'",
            "\tlist items 'a'",
            "\tlist items 'b'",
            "\toption multiline 'line1",
            "line2'",
            "\toption after 'tail'",
        ]
    )
    config = parse_uci_export(text, "pkg")
    section = config.section("test")

    assert section.value("plain") == "value"
    assert section.value("quoted") == "it''s ok"
    assert section.list_value("items") == ["a", "b"]
    assert section.value("multiline") == "line1\nline2"
    assert section.value("after") == "tail"


def test_parse_uci_export_uci_escaped_single_quote() -> None:
    """UCI-экранирование ``'\\''`` разбирается в обычную кавычку."""

    text = "config section 'test'\n\toption quoted 'it'\\''s ok'\n"
    section = parse_uci_export(text, "pkg").section("test")

    assert section.value("quoted") == "it's ok"


def test_parse_uci_export_empty_and_garbage() -> None:
    assert parse_uci_export("", "pkg").sections == {}
    # option до config игнорируется
    assert parse_uci_export("option x 'y'", "pkg").sections == {}


# --------------------------------------------------------------------------- misc


def test_parse_uci_export_tab_separator_is_known_limitation() -> None:
    """Известное ограничение парсера: табуляция после ``option`` не разбирается.

    UCI export на роутере разделяет пробелом, поэтому парсер это не
    поддерживает; тест фиксирует поведение, чтобы изменение было заметно.
    """

    section = parse_uci_export("config section 'test'\n\toption\tkey 'v'\n", "pkg").section("test")

    assert section.options == {}


def test_parse_uci_export_odd_quotes_swallow_next_line() -> None:
    """Известное ограничение: непарная кавычка в значении съедает следующую строку."""

    text = "config section 'test'\n\toption a 'it's broken'\n\toption b 'tail'\n"
    section = parse_uci_export(text, "pkg").section("test")

    assert "b" not in section.options
    assert "tail" in section.value("a")


def test_split_words() -> None:
    assert split_words(None) == []
    assert split_words("") == []
    assert split_words("  a  b\tc ") == ["a", "b", "c"]
    assert split_words("a,b;c") == ["a", "b;c"]
    assert split_words("a, b") == ["a", "b"]
    assert split_words(["a b", "c"]) == ["a", "b", "c"]
    assert split_words(["a", ["b,c"]]) == ["a", "b", "c"]
    assert split_words(42) == ["42"]


def test_parse_path_valid() -> None:
    assert parse_path("forkop.main.domain") == ("forkop", "main", "domain")
    assert parse_path("podkop.settings") == ("podkop", "settings", None)
    assert parse_path("  forkop.main.enabled  ") == ("forkop", "main", "enabled")
    assert parse_path("forkop.[1].domain") == ("forkop", "[1]", "domain")
    assert parse_path("forkop.cfg_0A2b.url") == ("forkop", "cfg_0A2b", "url")


@pytest.mark.parametrize(
    "path",
    [
        "",
        "forkop",
        "forkop..domain",
        "forkop.main.bad-name",
        "forkop.main; rm -rf /",
        "Forkop.main.domain",
        "forkop.main.domain.extra",
        " forkop.main.$domain",
        "forkop.main.do main",
    ],
)
def test_parse_path_invalid(path: str) -> None:
    with pytest.raises(ConfigError, match="UCI-путь"):
        parse_path(path)


def _config(**sections: dict[str, Any]) -> UciConfig:
    config = UciConfig(package="forkop")
    for name, options in sections.items():
        config.sections[name] = UciSection(name=name, type="section", options=options)
    return config


def test_diff_configs_detects_all_change_kinds() -> None:
    before = _config(
        main={"domain": "a.com b.com", "enabled": "1", "label": "main"},
        old={"x": "1"},
    )
    after = {
        "main": {"domain": "a.com c.com", "enabled": "1", "extra": "new"},
        "new": {"enabled": "1"},
    }

    changes = diff_configs(before, after)
    by_path = {change.path: change for change in changes}

    assert set(by_path) == {
        "forkop.main.domain",
        "forkop.main.label",
        "forkop.main.extra",
        "forkop.old",
        "forkop.new.enabled",
    }
    assert by_path["forkop.main.domain"].op == "set"
    assert by_path["forkop.main.domain"].before == "a.com b.com"
    assert by_path["forkop.main.domain"].after == "a.com c.com"
    assert by_path["forkop.main.label"].op == "delete"
    assert by_path["forkop.old"].op == "delete"
    assert by_path["forkop.new.enabled"].before is None
    # неизменённые опции не попадают в diff
    assert "forkop.main.enabled" not in by_path
    assert by_path["forkop.main.extra"].to_dict()["path"] == "forkop.main.extra"


def test_diff_configs_ignores_whitespace_only_list_rewrites() -> None:
    before = _config(main={"list": ["a", "b"]})
    same = {"main": {"list": ["a", "b"]}}
    assert diff_configs(before, same) == []

    numeric = _config(main={"list": ["1", "2"]})
    assert diff_configs(numeric, {"main": {"list": [1, 2]}}) == []

    changed = diff_configs(before, {"main": {"list": ["a"]}})
    assert len(changed) == 1
    assert changed[0].before == ["a", "b"]
    assert changed[0].after == ["a"]


def test_diff_configs_skips_dot_keys() -> None:
    before = _config(main={".type": "section", "enabled": "1"})
    assert diff_configs(before, {"main": {".type": "section", "enabled": "1"}}) == []
