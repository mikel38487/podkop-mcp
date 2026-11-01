"""Тесты работы с доменами (``podkop_mcp.domains_core``)."""

from __future__ import annotations

import pytest

from podkop_mcp.domains_core import (
    coverage,
    entry_key,
    is_valid_entry,
    merge_entries,
    normalize_entry,
    parse_entries,
    remove_entries,
    render_value,
    summarize_entries,
)

# --------------------------------------------------------------------------- option_text (Forkop 1.x)


def test_parse_option_text_splits_by_whitespace(forkop1_config) -> None:
    """``section.domain`` — одна строка через пробел."""

    raw = forkop1_config.section("main").value("domain")
    parsed = parse_entries(raw, "option_text")

    assert "hornet.com" in parsed.entries
    assert parsed.entries[0] == "game8.co"
    assert parsed.entries[-1] == "cantstopthehnt.com"
    assert len(parsed.entries) == len(set(parsed.entries))
    assert parsed.comments == []
    # в 1.x значения без префиксов, invalid для domain_suffix-строк нет
    assert parsed.invalid == []


def test_parse_option_text_multiline_is_still_whitespace(forkop1_config) -> None:
    """В 1.x перевод строки в значении — тоже разделитель."""

    parsed = parse_entries(forkop1_config.section("AI").value("domain"), "option_text")
    assert parsed.entries == ["chatgpt.com", "openai.com", "grok.com"]


def test_parse_option_text_drops_duplicates_and_empty() -> None:
    parsed = parse_entries("  a.com  b.com\na.com,,  ", "option_text")
    assert parsed.entries == ["a.com", "b.com"]


def test_render_option_text_joins_with_space() -> None:
    assert render_value(["a.com", "b.com"], "option_text") == "a.com b.com"
    assert render_value([], "option_text") == ""


def test_merge_add_into_option_text_keeps_string_type(forkop1_config) -> None:
    """Добавление доменов в Forkop 1.x не превращает строку в список."""

    raw = forkop1_config.section("main").value("domain")
    existing = parse_entries(raw, "option_text").entries

    result, added, duplicates, invalid = merge_entries(existing, ["new.example", "HORNET.COM", "bad_domain"])

    assert added == ["new.example"]
    assert duplicates == ["HORNET.COM"]
    assert invalid == ["bad_domain"]
    assert isinstance(render_value(result, "option_text"), str)
    assert "new.example" in result
    assert len(result) == len(existing) + 1


def test_remove_from_option_text() -> None:
    result, removed, not_found = remove_entries(
        ["a.com", "b.com", "c.com"], ["b.com", "zzz.com", ".zone"]
    )
    assert result == ["a.com", "c.com"]
    assert removed == ["b.com"]
    assert not_found == ["zzz.com", ".zone"]


# --------------------------------------------------------------------------- list (Podkop)


def test_parse_list_storage(podkop_config) -> None:
    """Podkop: ``user_domains`` — UCI-список, списки разбираются поэлементно."""

    assert parse_entries(["a.com", "b.com"], "list").entries == ["a.com", "b.com"]
    assert parse_entries(None, "list").entries == []
    # элементы списка тоже могут содержать несколько доменов
    assert parse_entries(["a.com b.com", "c.com"], "list").entries == ["a.com", "b.com", "c.com"]
    # дефолтный конфиг Podkop: user_domains отсутствует
    assert podkop_config.section("main").value("user_domains") is None


def test_render_list_storage_returns_list() -> None:
    rendered = render_value(["a.com", "b.com"], "list")
    assert rendered == ["a.com", "b.com"]
    assert isinstance(rendered, list)
    assert render_value([], "list") == []


def test_merge_into_list_and_remove() -> None:
    result, added, duplicates, invalid = merge_entries(
        ["2ip.ru", "yandex.ru"], ["2ip.ru", "example.com", ".ru", ".co.uk"]
    )
    assert result == ["2ip.ru", "yandex.ru", "example.com", ".ru"]
    assert added == ["example.com", ".ru"]
    assert duplicates == ["2ip.ru"]
    assert invalid == [".co.uk"]

    result, removed, not_found = remove_entries(result, ["2ip.ru"])
    assert result == ["yandex.ru", "example.com", ".ru"]
    assert removed == ["2ip.ru"]
    assert not_found == []


# --------------------------------------------------------------------------- option_multiline (Forkop 2.x)


FORKOP2_DESTINATIONS = """\
# Основные домены
example.com
full:exact.example.org   # точное совпадение
// старая запись
keyword:torrent
regex:^ads?\\d+\\.example\\.com$
.zoneonly

sub.example.net
"""


def test_parse_multiline_keeps_comments_and_regex() -> None:
    """Forkop 2.x: одна запись на строку, комментарии ``#``/``//`` сохраняются."""

    parsed = parse_entries(FORKOP2_DESTINATIONS, "option_multiline")

    assert parsed.comments == ["# Основные домены", "// старая запись"]
    assert "example.com" in parsed.entries
    assert "sub.example.net" in parsed.entries
    assert "full:exact.example.org" in parsed.entries
    assert "keyword:torrent" in parsed.entries
    assert ".zoneonly" in parsed.entries
    # '#' внутри regex не обрезается как комментарий
    assert any(item.startswith("regex:") for item in parsed.entries)
    assert "regex:^ads?\\d+\\.example\\.com$" in parsed.entries
    assert parsed.invalid == []


def test_multiline_invalid_lines_and_multiword_legacy() -> None:
    parsed = parse_entries(
        "example.com\nnot a domain\na.com b.com\nfull:*.wild.com\n",
        "option_multiline",
    )
    # строка с несколькими валидными доменами разбирается на части
    assert "a.com" in parsed.entries
    assert "b.com" in parsed.entries
    assert "not a domain" in parsed.invalid
    assert "full:*.wild.com" in parsed.invalid


def test_render_multiline_preserves_comments_and_order() -> None:
    parsed = parse_entries(FORKOP2_DESTINATIONS, "option_multiline")
    result, _added, _dups, _invalid = merge_entries(parsed.entries, ["newly.example"])

    rendered = render_value(result, "option_multiline", original=FORKOP2_DESTINATIONS)

    assert isinstance(rendered, str)
    assert rendered.startswith("# Основные домены")
    assert "// старая запись" in rendered
    assert "newly.example" in rendered
    # порядок существующих записей сохранён
    assert rendered.index("example.com") < rendered.index("sub.example.net")
    # повторный разбор даёт тот же набор
    assert set(parse_entries(rendered, "option_multiline").entries) == set(result)


def test_render_multiline_removes_deleted_lines_only() -> None:
    parsed = parse_entries(FORKOP2_DESTINATIONS, "option_multiline")
    result, _removed, _not_found = remove_entries(parsed.entries, ["sub.example.net"])
    rendered = render_value(result, "option_multiline", original=FORKOP2_DESTINATIONS)

    assert "sub.example.net" not in rendered
    assert "example.com" in rendered
    assert "# Основные домены" in rendered


def test_render_multiline_without_original() -> None:
    assert render_value(["a.com", "b.com"], "option_multiline") == "a.com\nb.com"


def test_multiline_duplicates_are_dropped_on_parse() -> None:
    """Известное поведение: дубли в тексте схлопываются и не возвращаются при записи."""

    parsed = parse_entries("a.com\na.com\n# c\nb.com\n", "option_multiline")
    assert parsed.entries == ["a.com", "b.com"]

    rendered = render_value(parsed.entries, "option_multiline", original="a.com\na.com\n# c\nb.com\n")
    assert rendered.count("a.com") == 1
    assert "# c" in rendered


def test_multiline_comment_with_hash_inside_regex() -> None:
    """``#`` внутри regex-строки не считается началом комментария."""

    parsed = parse_entries("regex:^a#b$\n", "option_multiline")
    assert parsed.entries == ["regex:^a#b$"]
    assert parsed.comments == []


# --------------------------------------------------------------------------- валидация


@pytest.mark.parametrize(
    "entry",
    [
        "example.com",
        "sub.example.co.uk",
        "xn--80ak6aa92e.com",
        "full:example.com",
        "keyword:torrent",
        "regex:^ads\\.",
        ".ru",
        "192.0.2.1",
        "2001:db8::1",
        "10.0.0.0/8",
    ],
)
def test_is_valid_entry_accepts(entry: str) -> None:
    assert is_valid_entry(entry) is True


@pytest.mark.parametrize(
    "entry",
    [
        "",
        "example",
        "example..com",
        "http://example.com",
        "example com",
        "full:",
        "full:*.example.com",
        "keyword:",
        "regex:[",
        ".a",
        "com",
        "-bad.com",
        "bad-.com",
    ],
)
def test_is_valid_entry_rejects(entry: str) -> None:
    assert is_valid_entry(entry) is False


def test_entry_key_is_case_insensitive_except_regex_and_keyword() -> None:
    assert entry_key("Example.COM") == entry_key("example.com")
    assert entry_key("FULL:Example.com") == entry_key("full:example.com")
    assert entry_key("keyword:Torrent") != entry_key("keyword:torrent")
    assert entry_key("regex:^A") != entry_key("regex:^a")
    assert normalize_entry(" FULL:Example.COM ") == "full:example.com"


# --------------------------------------------------------------------------- покрытие


@pytest.mark.parametrize(
    ("entry", "domain", "expected"),
    [
        # обычный домен покрывает себя и поддомены
        ("example.com", "example.com", True),
        ("example.com", "api.example.com", True),
        ("example.com", "notexample.com", False),
        ("example.com", "example.com.evil.net", False),
        # full: — только точное совпадение
        ("full:example.com", "example.com", True),
        ("full:example.com", "api.example.com", False),
        # зона .xx
        (".ru", "example.ru", True),
        (".ru", "ru", False),
        (".ru", "example.com", False),
        # keyword:
        ("keyword:torrent", "my-torrent-site.net", True),
        ("keyword:torrent", "example.com", False),
        # regex:
        ("regex:^ads?\\d*\\.", "ads1.example.com", True),
        ("regex:^ads?\\d*\\.", "example.com", False),
        ("regex:[", "example.com", False),
        # ip / cidr
        ("10.0.0.0/8", "10.1.2.3", True),
        ("10.0.0.0/8", "192.0.2.1", False),
        ("192.0.2.1", "192.0.2.1", True),
        ("192.0.2.1", "example.com", False),
        # ipv6
        ("2001:db8::/32", "2001:db8::1", True),
        ("2001:db8::/32", "2001:dba::1", False),
        ("2001:db8::1", "2001:db8::1", True),
        # зона в верхнем регистре (entry регистронезависим)
        (".RU", "example.ru", True),
        # регистр и точка в конце
        ("Example.COM", "API.EXAMPLE.COM.", True),
        ("full:Example.COM", "example.com", True),
    ],
)
def test_coverage(entry: str, domain: str, expected: bool) -> None:
    assert coverage(entry, domain) is expected


def test_coverage_keyword_with_padding_and_empty() -> None:
    """``keyword:`` ищется подстрокой; пустой keyword не покрывает ничего."""

    assert coverage("keyword: torrent", "my-torrent.net") is True
    assert is_valid_entry("keyword:") is False
    assert coverage("keyword:", "example.com") is False


def test_coverage_empty_inputs() -> None:
    assert coverage("", "example.com") is False
    assert coverage("example.com", "") is False


# --------------------------------------------------------------------------- сводка


def test_summarize_entries() -> None:
    entries = [
        "a.com",
        "b.org",
        ".ru",
        "full:x.com",
        "keyword:torrent",
        "regex:^ads",
        "10.0.0.0/8",
        "192.0.2.1",
    ]
    summary = summarize_entries(entries)

    assert summary == {"domains": 2, "zones": 1, "full": 1, "keyword": 1, "regex": 1, "ip": 2}
    assert summarize_entries([]) == {"domains": 0, "zones": 0, "full": 0, "keyword": 0, "regex": 0, "ip": 0}


def test_summarize_forkop1_main(forkop1_config) -> None:
    parsed = parse_entries(forkop1_config.section("main").value("domain"), "option_text")
    summary = summarize_entries(parsed.entries)

    assert summary["domains"] == len(parsed.entries)
    assert summary["full"] == summary["zones"] == summary["regex"] == 0
