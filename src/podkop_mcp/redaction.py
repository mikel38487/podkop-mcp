"""Маскирование секретов.

Правило простое: наружу (в ответы MCP и логи) не должны попадать
URL подписок, proxy-строки, приватные ключи, пароли и токены. При этом
обычные URL вроде ``https://www.gstatic.com/generate_204`` остаются
читаемыми, иначе диагностика становится бесполезной.
"""

from __future__ import annotations

import re
from typing import Any

MASK = "***"

# Ключи, значение которых маскируется целиком.
_SECRET_KEYS = {
    "proxy_string",
    "subscription_url",
    "url",
    "outbound_json",
    "private_key",
    "pre_shared_key",
    "password",
    "passwd",
    "passphrase",
    "secret",
    "token",
    "auth_key",
    "api_key",
    "license_key",
    "uuid",
    "key",
    "warp_profile",
    "header",
    "headers",
    "http_headers",
    "extra_headers",
    "authorization",
    "cookie",
    "subscription",
    "access_token",
    "refresh_token",
    "api_token",
    "auth",
}

# Ключи, которые похожи на секреты, но безопасны.
_SAFE_KEYS = {
    "testing_url",
    "latency_test_url",
    "health_check_url",
    "download_url",
    "update_url",
    "url_test",
    "public_key",
    "config_url",
}

_KEY_HINT = re.compile(
    r"(^|_)(secret|passw|token|auth|private|proxy_string|uuid|api_?key|license_key)($|_)",
    re.I,
)

_URL_WITH_TOKEN = re.compile(
    r"(?P<scheme>https?://)(?P<host>[^/\s\"'<>]+)(?P<path>/[^\s\"'<>]*)?",
    re.I,
)
_PROXY_SCHEME = re.compile(
    r"\b(?:vless|vmess|ss|ssr|trojan|hysteria2?|tuic|awg|wireguard|socks|socks5)://[^\s\"'<>]+",
    re.I,
)
_TOKENY_PATH = re.compile(r"[A-Za-z0-9_\-]{18,}")
_QUERY_SECRET = re.compile(
    r"(?i)\b(token|uuid|password|passwd|secret|api_key|key|auth)=([^&\s\"'<>]+)"
)
_UUID = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I)


def _shorten_url(url: str, *, force: bool = False) -> str:
    match = _URL_WITH_TOKEN.fullmatch(url.strip())
    if not match:
        return MASK
    host = match.group("host")
    path = match.group("path") or ""
    if not path or path == "/":
        return f"{match.group('scheme')}{host}/"
    if force or _TOKENY_PATH.search(path) or "?" in path or "token" in path.lower():
        return f"{match.group('scheme')}{host}/{MASK}"
    return url


def mask_value(key: str, value: Any) -> Any:
    """Возвращает безопасную копию значения для указанного ключа."""

    if value is None:
        return None
    key_l = key.lower()
    if key_l in _SAFE_KEYS:
        return value
    if isinstance(value, str) and _PROXY_SCHEME.search(value):
        # Значение выглядит как proxy-ссылка — маскируем независимо от имени ключа.
        return MASK
    if key_l in _SECRET_KEYS or _KEY_HINT.search(key_l):
        if isinstance(value, (list, tuple)):
            return [MASK for _ in value]
        if isinstance(value, dict):
            return {k: MASK for k in value}
        text = str(value)
        if key_l in {"url", "subscription_url"} and text.lower().startswith(("http://", "https://")):
            # Для конфига URL подписки — всегда секрет, даже если путь короткий.
            return _shorten_url(text, force=True)
        return MASK
    if isinstance(value, dict):
        return {k: mask_value(k, v) for k, v in value.items()}
    if isinstance(value, list):
        return [mask_value(key, item) for item in value]
    return value


def redact_config(config: dict[str, Any]) -> dict[str, Any]:
    """Маскирует секции/опции конфига, сохраняя структуру."""

    result: dict[str, Any] = {}
    for section_name, section in config.items():
        if not isinstance(section, dict):
            result[section_name] = section
            continue
        cleaned: dict[str, Any] = {}
        for key, value in section.items():
            cleaned[key] = mask_value(key, value)
        result[section_name] = cleaned
    return result


def redact_text(text: str, *, keep_plain_urls: bool = True) -> str:
    """Маскирует секреты в свободном тексте (логи, вывод CLI)."""

    if not text:
        return text
    text = _PROXY_SCHEME.sub(lambda m: m.group(0).split("://", 1)[0] + "://" + MASK, text)
    text = _QUERY_SECRET.sub(lambda m: f"{m.group(1)}={MASK}", text)
    text = _UUID.sub(MASK, text)

    def _url(match: re.Match[str]) -> str:
        url = match.group(0)
        path_match = _URL_WITH_TOKEN.fullmatch(url)
        if not path_match:
            return url
        path = path_match.group("path") or ""
        if keep_plain_urls and not (_TOKENY_PATH.search(path) or "?" in path):
            return url
        return _shorten_url(url)

    return _URL_WITH_TOKEN.sub(_url, text)
