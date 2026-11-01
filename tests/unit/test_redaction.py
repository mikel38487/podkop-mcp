"""Тесты маскирования секретов (``podkop_mcp.redaction``)."""

from __future__ import annotations

from typing import Any

import pytest

from podkop_mcp.redaction import MASK, mask_value, redact_config, redact_text

FORKOP_URL = "https://sub.example.com/REDACTED"
SUBSCRIPTION_URL = "https://sub.example.com/abcdefghijklmnopqrstuvwxyz"
PROXY_URL = "vless://11111111-2222-3333-4444-555555555555@example.net:443?security=reality&pbk=secretkey"
LATENCY_URL = "https://www.gstatic.com/generate_204"


# --------------------------------------------------------------------------- URL


def test_subscription_url_keeps_host_only() -> None:
    """URL подписки превращается в ``scheme://host/***``."""

    assert mask_value("subscription_url", SUBSCRIPTION_URL) == "https://sub.example.com/***"
    assert mask_value("url", SUBSCRIPTION_URL) == "https://sub.example.com/***"
    # безопасные диагностические ключи не маскируются
    assert mask_value("latency_test_url", LATENCY_URL) == LATENCY_URL
    assert mask_value("testing_url", LATENCY_URL) == LATENCY_URL
    # запрос маскируется целиком
    assert mask_value("url", "https://sub.example.com/api?token=abc") == "https://sub.example.com/***"


def test_any_url_path_is_masked_for_secret_key() -> None:
    """Для ключа ``url`` путь — всегда секрет, даже если он короткий."""

    assert mask_value("url", "https://sub.example.com/path") == "https://sub.example.com/***"
    assert mask_value("url", FORKOP_URL) == "https://sub.example.com/***"


def test_subscription_metadata_keys_are_not_secrets() -> None:
    """``subscription_update_enabled`` — не секрет, в отличие от ``subscription_url``."""

    assert mask_value("subscription_update_enabled", "1") == "1"
    assert mask_value("subscription_update_interval", "1h") == "1h"
    assert mask_value("subscription_url", SUBSCRIPTION_URL) == "https://sub.example.com/***"


def test_secret_keys_are_case_insensitive() -> None:
    assert mask_value("Password", "secret") == MASK
    assert mask_value("Authorization", "Bearer abc") == MASK
    assert mask_value("URL", SUBSCRIPTION_URL) == "https://sub.example.com/***"


# --------------------------------------------------------------------------- proxy


def test_vless_proxy_string_is_fully_masked() -> None:
    """``proxy_string`` не раскрывает ни UUID, ни ключи."""

    assert mask_value("proxy_string", PROXY_URL) == MASK
    assert mask_value("proxy_string", "") == MASK
    for key in ("vmess://x", "ss://x", "trojan://x", "hysteria2://x"):
        assert mask_value("proxy_string", key) == MASK


def test_vless_in_free_text_is_masked() -> None:
    text = f"log: connect via {PROXY_URL} ok"
    masked = redact_text(text)

    assert "vless://***" in masked
    assert "555555555555" not in masked
    assert "pbk=secretkey" not in masked
    assert masked.startswith("log: connect via ")


def test_proxy_url_is_masked_by_value() -> None:
    """Proxy-ссылка маскируется даже под несекретным ключом (защита по данным)."""

    assert mask_value("domain", PROXY_URL) == MASK
    assert mask_value("notes", PROXY_URL) == MASK


def test_query_secrets_and_uuid_are_masked_in_text() -> None:
    """``token=``, ``uuid=`` и UUID без схемы не утекают в логи."""

    text = "token=abcdef uuid=11111111-2222-3333-4444-555555555555"
    masked = redact_text(text)
    assert "abcdef" not in masked
    assert "11111111" not in masked
    assert "token=***" in masked


# --------------------------------------------------------------------------- safe keys


def test_testing_url_is_not_masked() -> None:
    """``testing_url`` — диагностический адрес, его маскировать нельзя."""

    for key in ("testing_url", "latency_test_url", "download_url", "public_key"):
        assert mask_value(key, LATENCY_URL) == LATENCY_URL
    assert redact_text(f"latency {LATENCY_URL}") == f"latency {LATENCY_URL}"


def test_secret_keys_are_masked() -> None:
    for key in (
        "password",
        "passphrase",
        "secret",
        "token",
        "api_key",
        "uuid",
        "private_key",
        "pre_shared_key",
        "yacd_secret_key",
        "proxy_string",
        "outbound_json",
    ):
        assert mask_value(key, "value") == MASK, key


def test_key_hint_matches_partial_names() -> None:
    assert mask_value("my_secret_field", "x") == MASK
    assert mask_value("auth_header", "x") == MASK
    assert mask_value("label", "x") == "x"
    assert mask_value("domain", "example.com") == "example.com"
    assert mask_value("label", None) is None


# --------------------------------------------------------------------------- структуры


def test_nested_dict_and_list_masking() -> None:
    """Вложенные структуры маскируются рекурсивно, структура сохраняется.

    Известное ограничение ``mask_value``: секретный ключ, значение которого —
    словарь, обходит только первый уровень (ключи верхнего уровня получают
    ``***``). Инструменты маскируют такие секции поэлементно, поэтому для
    конфига это не проблема, но тест фиксирует фактическое поведение.
    """

    data: dict[str, Any] = {
        "settings": {
            "dns_server": ["77.88.8.8"],
            "password": "hunter2",
            "latency_test_url": LATENCY_URL,
        },
        "subscription_url": {"sec1": {"url": SUBSCRIPTION_URL, "hdr": "x"}},
        "urltest": {"testing_url": LATENCY_URL, "name": "тест"},
    }
    masked = redact_config(data)

    assert masked["settings"]["dns_server"] == ["77.88.8.8"]
    assert masked["settings"]["password"] == MASK
    assert masked["settings"]["latency_test_url"] == LATENCY_URL
    # секция с секретным именем: маскируются листовые секреты, структура сохраняется
    assert masked["subscription_url"] == {"sec1": {"url": "https://sub.example.com/***", "hdr": "x"}}
    assert masked["urltest"]["testing_url"] == LATENCY_URL
    assert masked["urltest"]["name"] == "тест"
    # исходные данные не изменяются
    assert data["settings"]["password"] == "hunter2"


def test_secret_key_with_dict_value_masks_first_level_only() -> None:
    """Граница ``mask_value``: вложенный в секрет словарь маскируется на 1 уровень.

    Для инструментов это не опасно: секции и дочерние элементы маскируются
    по именам ключей (см. ``test_tools_redact_children_by_key``).
    """

    masked = mask_value("token", {"outer": {"url": SUBSCRIPTION_URL}})
    assert masked == {"outer": MASK}


def test_tools_redact_children_by_key() -> None:
    """Инструменты маскируют дочерние секции по ключам (``sections._section_summary``)."""

    child = {"section": "main", "url": SUBSCRIPTION_URL, "testing_url": LATENCY_URL}
    masked = {key: mask_value(key, value) for key, value in child.items()}

    assert masked == {
        "section": "main",
        "url": "https://sub.example.com/***",
        "testing_url": LATENCY_URL,
    }


def test_nested_dict_leaf_secrets_are_masked() -> None:
    """Секретные ключи находятся и на вложенном уровне."""

    # секретное имя ключа маскирует и вложенные значения
    assert mask_value("secret", {"hdr": "x"}) == {"hdr": MASK}

    nested = mask_value("wrapper", {"inner": {"password": "p", "url": SUBSCRIPTION_URL}})
    assert nested["inner"]["password"] == MASK
    assert nested["inner"]["url"] == "https://sub.example.com/***"


def test_list_of_secrets_is_masked_item_by_item() -> None:
    assert mask_value("headers", ["a", "b"]) == [MASK, MASK]
    assert mask_value("user_domains", ["a.com", "b.com"]) == ["a.com", "b.com"]
    # dict внутри list получает маску всех ключей
    assert mask_value("secret", [{"a": 1}]) == [MASK]


def test_non_dict_sections_pass_through() -> None:
    assert redact_config({"count": 3}) == {"count": 3}  # type: ignore[dict-item]


# --------------------------------------------------------------------------- текст


def test_redact_text_keeps_plain_urls_and_masks_token_paths() -> None:
    plain = f"check {LATENCY_URL}"
    token = f"sub {SUBSCRIPTION_URL}"
    query = "api https://example.com/api?x=1"

    assert redact_text(plain) == plain
    assert redact_text(token) == "sub https://sub.example.com/***"
    assert redact_text(query) == "api https://example.com/***"
    assert redact_text("") == ""
    assert redact_text("без секретов") == "без секретов"
    assert redact_text("see vless://a@b:1") == "see vless://***"


def test_redact_text_full_config_dump(read_fixture) -> None:
    """Маскирование вывода show_config не ломает структуру UCI."""

    text = read_fixture("forkop1-showconfig-masked.txt")
    masked = redact_text(text)
    assert "config section 'main'" in masked
    assert "list community_lists 'telegram'" in masked
    # домены — не секреты, они остаются читаемыми
    assert "hornet.com" in masked


def test_redact_text_masks_subscription_url_in_uci_export() -> None:
    """В тексте UCI-экспорта URL подписки теряет длинный путь."""

    text = "\n".join(
        [
            "config subscription_url",
            f"\toption url '{SUBSCRIPTION_URL}'",
            "\toption testing_url 'https://www.gstatic.com/generate_204'",
        ]
    )
    masked = redact_text(text)

    assert f"option url 'https://sub.example.com/{MASK}'" in masked
    assert "abcdefghijklmnopqrstuvwxyz" not in masked
    # диагностический URL остаётся как есть
    assert "https://www.gstatic.com/generate_204" in masked


def test_redact_text_with_proxy_urls_in_logs() -> None:
    text = f"2026-01-01 error dial {PROXY_URL}: timeout; retry {PROXY_URL}"
    masked = redact_text(text)

    assert masked.count("vless://***") == 2
    assert "example.net" not in masked
    assert "timeout" in masked


@pytest.mark.parametrize(("value", "expected"), [("", ""), ("plain text", "plain text")])
def test_redact_text_trivial(value: str, expected: str) -> None:
    assert redact_text(value) == expected
    assert redact_text(None) is None
