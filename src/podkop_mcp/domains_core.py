"""Чистые функции работы с доменами.

Модуль не знает про SSH и MCP: только парсинг значений, нормализация,
валидация, объединение/удаление и проверка покрытия. Это позволяет
тестировать опасную логику без роутера.
"""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass
from typing import Any, Literal

DomainStorage = Literal["option_text", "option_multiline", "list"]

DOMAIN_RE = re.compile(
    r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$"
)
ZONE_RE = re.compile(r"^\.[a-z]{2,63}$")
PREFIXES = ("full:", "keyword:", "regex:")
MAX_REGEX_LENGTH = 512
_NESTED_QUANTIFIER = re.compile(r"\((?:[^()]|\\.)*[+*]\)[+*?]")


def _regex_is_safe(pattern: str) -> bool:
    """Грубая защита от катастрофического backtracking."""

    if len(pattern) > MAX_REGEX_LENGTH:
        return False
    return _NESTED_QUANTIFIER.search(pattern) is None


@dataclass
class EntryParse:
    entries: list[str]
    comments: list[str]
    invalid: list[str]


def _clean_line(line: str) -> str:
    line = line.strip()
    if not line or line.startswith("#") or line.startswith("//"):
        return ""
    # Комментарий в конце строки: "example.com # комментарий".
    # Для regex: не трогаем — там '#' может быть частью выражения.
    if not line.lower().startswith("regex:"):
        line = re.split(r"\s+#\s*", line, maxsplit=1)[0].strip()
        line = re.split(r"\s+//\s*", line, maxsplit=1)[0].strip()
    return line


def _split_words(text: str) -> list[str]:
    return [item for item in re.split(r"[\s,]+", text.strip()) if item]


def parse_entries(value: Any, storage: DomainStorage) -> EntryParse:
    """Возвращает значения в порядке появления, без дублей."""

    entries: list[str] = []
    comments: list[str] = []
    invalid: list[str] = []

    def add(item: str) -> None:
        item = item.strip()
        if not item:
            return
        if item not in entries:
            entries.append(item)

    if value is None:
        return EntryParse([], [], [])
    if isinstance(value, list):
        for item in value:
            for word in _split_words(str(item)):
                if is_valid_entry(word):
                    add(word)
                else:
                    invalid.append(word)
        return EntryParse(entries, comments, invalid)

    text = str(value)
    if storage == "option_text":
        for word in _split_words(text):
            add(word)
        return EntryParse(entries, comments, invalid)

    # option_multiline: одна запись на строку, комментарии сохраняем.
    for raw_line in text.splitlines():
        stripped = raw_line.strip()
        if stripped.startswith(("#", "//")):
            comments.append(stripped)
            continue
        line = _clean_line(raw_line)
        if not line:
            continue
        if is_valid_entry(line):
            add(line)
            continue
        # Старые данные могли хранить несколько доменов в одной строке.
        words = _split_words(line)
        if len(words) > 1 and all(is_valid_entry(word) for word in words):
            for word in words:
                add(word)
        else:
            invalid.append(line)
    return EntryParse(entries, comments, invalid)


def is_valid_entry(entry: str) -> bool:
    entry = entry.strip()
    if not entry:
        return False
    lower = entry.lower()
    if lower.startswith("regex:"):
        pattern = entry[6:]
        if not _regex_is_safe(pattern):
            return False
        try:
            re.compile(pattern)
        except re.error:
            return False
        return len(pattern) > 0
    if lower.startswith("keyword:"):
        return bool(entry[8:].strip())
    if lower.startswith("full:"):
        return bool(DOMAIN_RE.fullmatch(lower[5:].strip()))
    if entry.startswith("."):
        return bool(ZONE_RE.fullmatch(lower))
    if _is_ip_or_cidr(entry):
        return True
    return bool(DOMAIN_RE.fullmatch(lower))


def _is_ip_or_cidr(value: str) -> bool:
    try:
        ipaddress.ip_network(value, strict=False)
        return "/" in value or _is_ip(value)
    except ValueError:
        return False


def _is_ip(value: str) -> bool:
    try:
        ipaddress.ip_address(value)
        return True
    except ValueError:
        return False


def normalize_entry(entry: str) -> str:
    entry = entry.strip()
    lower = entry.lower()
    if lower.startswith(("regex:", "keyword:")):
        return entry
    if lower.startswith("full:"):
        return "full:" + lower[5:].strip()
    if entry.startswith("."):
        return lower
    return lower


def entry_key(entry: str) -> str:
    """Ключ сравнения: домены регистронезависимы, regex/keyword — нет."""

    normalized = normalize_entry(entry)
    return normalized if normalized.lower().startswith(("regex:", "keyword:")) else normalized.lower()


def merge_entries(
    existing: list[str], additions: list[str]
) -> tuple[list[str], list[str], list[str], list[str]]:
    """Возвращает ``(result, added, duplicates, invalid)``."""

    result = list(existing)
    seen = {entry_key(item) for item in result}
    added: list[str] = []
    duplicates: list[str] = []
    invalid: list[str] = []
    for item in additions:
        item = item.strip()
        if not item:
            continue
        if not is_valid_entry(item):
            invalid.append(item)
            continue
        key = entry_key(item)
        if key in seen:
            duplicates.append(item)
            continue
        seen.add(key)
        result.append(normalize_entry(item))
        added.append(normalize_entry(item))
    return result, added, duplicates, invalid


def remove_entries(existing: list[str], removals: list[str]) -> tuple[list[str], list[str], list[str]]:
    """Возвращает ``(result, removed, not_found)``."""

    remove_keys = {entry_key(item) for item in removals if item.strip()}
    result: list[str] = []
    removed: list[str] = []
    for item in existing:
        key = entry_key(item)
        if key in remove_keys:
            removed.append(item)
        else:
            result.append(item)
    removed_keys = {entry_key(item) for item in removed}
    not_found = [item for item in removals if entry_key(item) not in removed_keys]
    return result, removed, not_found


def render_value(
    entries: list[str],
    storage: DomainStorage,
    *,
    original: Any = None,
    preserve_comments: bool = True,
) -> Any:
    """Собирает значение для UCI с сохранением формата бэкенда."""

    if storage == "list":
        return list(entries)
    if storage == "option_text":
        return " ".join(entries)
    # option_multiline: сохраняем комментарии и пустые строки по возможности.
    if preserve_comments and isinstance(original, str) and original.strip():
        kept: list[str] = []
        rendered = {entry_key(item) for item in entries}
        for raw_line in original.splitlines():
            stripped = raw_line.strip()
            if not stripped:
                kept.append("")
                continue
            if stripped.startswith(("#", "//")):
                kept.append(raw_line.rstrip())
                continue
            line = _clean_line(raw_line)
            if not line:
                continue
            if entry_key(line) in rendered:
                kept.append(line)
                rendered.discard(entry_key(line))
            # Удалённые строки исчезают, их комментарии не восстанавливаем.
        for entry in entries:
            if entry_key(entry) in rendered:
                kept.append(entry)
        while kept and kept[-1] == "":
            kept.pop()
        return "\n".join(kept)
    return "\n".join(entries)


def coverage(entry: str, domain: str) -> bool:
    """Покрывает ли запись конфига указанный домен."""

    entry = entry.strip()
    domain = domain.strip().lower().rstrip(".")
    if not entry or not domain:
        return False
    lower = entry.lower()
    if lower.startswith("full:"):
        return domain == lower[5:].strip().rstrip(".")
    if lower.startswith("keyword:"):
        needle = lower[8:].strip()
        return bool(needle) and needle in domain
    if lower.startswith("regex:"):
        pattern = entry[6:]
        if not _regex_is_safe(pattern):
            return False
        try:
            return re.search(pattern, domain, re.IGNORECASE) is not None
        except re.error:
            return False
    if entry.startswith("."):
        return domain.endswith(lower)
    try:
        network = ipaddress.ip_network(entry, strict=False)
        address = ipaddress.ip_address(domain)
        return address in network
    except ValueError:
        pass
    entry = lower.rstrip(".")
    return domain == entry or domain.endswith("." + entry)


def summarize_entries(entries: list[str]) -> dict[str, int]:
    result = {"domains": 0, "zones": 0, "full": 0, "keyword": 0, "regex": 0, "ip": 0}
    for entry in entries:
        lower = entry.lower()
        if lower.startswith("full:"):
            result["full"] += 1
        elif lower.startswith("keyword:"):
            result["keyword"] += 1
        elif lower.startswith("regex:"):
            result["regex"] += 1
        elif entry.startswith("."):
            result["zones"] += 1
        elif _is_ip_or_cidr(entry):
            result["ip"] += 1
        else:
            result["domains"] += 1
    return result
