"""Разбор UCI-конфигурации.

Основной источник — JSON от ``ubus call uci get``. Он отдаёт значения
с сохранением типа (строка/список), поэтому не нужно угадывать формат.
Резервный парсер ``uci export`` нужен для офлайн-фикстур и старых прошивок.
"""

from __future__ import annotations

import re
import shlex
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any, Literal

from .errors import ConfigError

PACKAGE_RE = re.compile(r"^[a-z0-9_]+$")
SECTION_RE = re.compile(r"^[A-Za-z0-9_@\[\]-]+$")
OPTION_RE = re.compile(r"^[A-Za-z0-9_]+$")
PATH_RE = re.compile(
    r"^(?P<package>[a-z0-9_]+)\.(?P<section>[A-Za-z0-9_@\[\]-]+)(?:\.(?P<option>[A-Za-z0-9_]+))?$"
)


def validate_package(package: str) -> str:
    if not PACKAGE_RE.fullmatch(package):
        raise ConfigError(f"Недопустимое имя UCI-пакета: {package!r}")
    return package


def parse_path(path: str) -> tuple[str, str, str | None]:
    """``forkop.main.domain`` → ``("forkop", "main", "domain")``."""

    match = PATH_RE.fullmatch(path.strip())
    if not match:
        raise ConfigError(
            f"Недопустимый UCI-путь: {path!r}. Ожидается pkg.section[.option]"
        )
    return match.group("package"), match.group("section"), match.group("option")


@dataclass
class UciSection:
    name: str
    type: str
    anonymous: bool = False
    index: int = 0
    options: dict[str, Any] = field(default_factory=dict)

    def value(self, key: str, default: Any = None) -> Any:
        return self.options.get(key, default)

    def is_list(self, key: str) -> bool:
        return isinstance(self.options.get(key), list)

    def list_value(self, key: str) -> list[str]:
        value = self.options.get(key)
        if value is None:
            return []
        if isinstance(value, list):
            return [str(item) for item in value]
        return [str(value)] if str(value) != "" else []

    def to_dict(self, *, include_meta: bool = True) -> dict[str, Any]:
        result: dict[str, Any] = {}
        if include_meta:
            result.update(
                {
                    ".name": self.name,
                    ".type": self.type,
                    ".anonymous": self.anonymous,
                    ".index": self.index,
                }
            )
        result.update(self.options)
        return result


@dataclass
class UciConfig:
    package: str
    sections: dict[str, UciSection] = field(default_factory=dict)
    aliases: dict[str, str] = field(default_factory=dict)

    def resolve_section_name(self, name: str) -> str:
        """Приводит имя секции к каноническому.

        ``ubus`` отдаёт анонимные секции как ``cfg076a7c``, а ``uci show`` —
        как ``@subscription_url[0]``. Наружу мы показываем второй вариант,
        но принимаем оба.
        """

        return self.aliases.get(name, name)

    def section(self, name: str) -> UciSection:
        key = self.resolve_section_name(name)
        try:
            return self.sections[key]
        except KeyError as exc:
            known = ", ".join(sorted(self.sections)[:20]) or "нет"
            raise ConfigError(f"Секция {name!r} не найдена в {self.package}. Есть: {known}") from exc

    def top_sections(self) -> list[UciSection]:
        return [section for section in self.sections.values() if section.type == "section"]

    def sections_of_type(self, type_name: str) -> list[UciSection]:
        return [section for section in self.sections.values() if section.type == type_name]

    def children(self, type_name: str, parent: str) -> list[UciSection]:
        parent = self.resolve_section_name(parent)
        return [
            section
            for section in self.sections_of_type(type_name)
            if str(section.value("section", "")) == parent
        ]

    def get_path(self, path: str) -> Any:
        _package, section_name, option = parse_path(path)
        section = self.section(section_name)
        if option is None:
            return section.to_dict()
        return section.value(option)

    def to_public_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "package": self.package,
            "sections": {name: section.to_dict() for name, section in self.sections.items()},
            "count": len(self.sections),
        }
        if self.aliases:
            result["aliases"] = dict(self.aliases)
        return result


def parse_ubus_uci(payload: dict[str, Any], package: str) -> UciConfig:
    """Разбирает ответ ``ubus call uci get``.

    Анонимные секции получают канонические имена ``@type[index]`` — те же,
    что показывает ``uci show``. Старое имя ``cfg...`` сохраняется в
    ``aliases``, чтобы пути из отладочных дампов продолжали работать.
    """

    if "values" in payload:
        values = payload["values"]
    elif "error" in payload:
        raise ConfigError(f"ubus uci get: {payload['error']}")
    else:
        values = payload
    if not isinstance(values, dict):
        raise ConfigError("ubus uci get вернул неожиданный формат")

    config = UciConfig(package=package)
    anonymous_counters: dict[str, int] = {}
    for raw_name, raw in values.items():
        if not isinstance(raw, dict):
            continue
        section_type = str(raw.get(".type") or "")
        if not section_type:
            continue
        anonymous = bool(raw.get(".anonymous", False))
        if anonymous:
            index = anonymous_counters.get(section_type, 0)
            anonymous_counters[section_type] = index + 1
            name = f"@{section_type}[{index}]"
            config.aliases[str(raw_name)] = name
        else:
            name = str(raw_name)
        options = {
            key: value
            for key, value in raw.items()
            if not key.startswith(".") and key not in {"anonymous", "index"}
        }
        config.sections[name] = UciSection(
            name=name,
            type=section_type,
            anonymous=anonymous,
            index=int(raw.get(".index", 0) or 0),
            options=options,
        )
    return config



def _unquote(token: str) -> str:
    token = token.strip()
    if len(token) >= 2 and token[0] == "'" and token[-1] == "'":
        inner = token[1:-1]
        # UCI экранирует одинарную кавычку как '\''
        return inner.replace("'\\''", "'")
    if len(token) >= 2 and token[0] == '"' and token[-1] == '"':
        try:
            return bytes(token[1:-1], "utf-8").decode("unicode_escape")
        except Exception:
            return token[1:-1]
    return token


def _split_uci_line(line: str) -> tuple[str, str, str | None]:
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return "", "", None
    parts = stripped.split(None, 1)
    command = parts[0]
    rest = parts[1].strip() if len(parts) > 1 else ""
    if command == "config":
        if not rest:
            return command, "", None
        parts = rest.split(None, 1)
        section_type = parts[0]
        name = _unquote(parts[1]) if len(parts) > 1 else ""
        return command, section_type, name
    if command in {"option", "list"}:
        if not rest:
            return command, "", None
        parts = rest.split(None, 1)
        key = parts[0]
        value = _unquote(parts[1]) if len(parts) > 1 else ""
        return command, key, value
    return "", "", None



def parse_uci_export(text: str, package: str) -> UciConfig:
    """Резервный парсер ``uci export``.

    Поддерживает многострочные значения в одинарных кавычках; этого
    достаточно для реальных конфигов Podkop/Forkop.
    """

    config = UciConfig(package=package)
    current: UciSection | None = None
    buffer: list[str] = []
    collecting = False
    anonymous_counters: dict[str, int] = {}


    def finish() -> None:
        nonlocal buffer, collecting
        if not buffer:
            return
        line = "\n".join(buffer)
        buffer = []
        collecting = False
        command, key, value = _split_uci_line(line)
        if current is None:
            return
        if command == "option" and key:
            current.options[key] = value
        elif command == "list" and key:
            existing = current.options.setdefault(key, [])
            if isinstance(existing, list):
                existing.append(value)

    for raw_line in text.splitlines():
        if collecting:
            buffer.append(raw_line)
            # Чётное число неэкранированных кавычек закрывает значение.
            if buffer[-1].count("'") % 2 == 1:
                finish()
            continue

        stripped = raw_line.strip()
        if stripped.startswith("config "):
            finish()
            parts = stripped.split(None, 2)
            section_type = parts[1] if len(parts) > 1 else "section"
            if len(parts) > 2:
                name = _unquote(parts[2])
                anonymous = False
            else:
                index = anonymous_counters.get(section_type, 0)
                anonymous_counters[section_type] = index + 1
                name = f"@{section_type}[{index}]"
                anonymous = True
            current = UciSection(name=name, type=section_type, anonymous=anonymous)
            config.sections[name] = current
            continue

        if stripped.startswith(("option ", "list ")) and current is not None:
            # Значение может занимать несколько строк.
            if stripped.count("'") % 2 == 1:
                buffer = [stripped]
                collecting = True
            else:
                command, key, value = _split_uci_line(stripped)
                if command == "option" and key:
                    current.options[key] = value
                elif command == "list" and key:
                    existing = current.options.setdefault(key, [])
                    if isinstance(existing, list):
                        existing.append(value)

    finish()
    return config


def split_words(value: Any) -> list[str]:
    """Разбивает строку/список на слова, не теряя порядок."""

    if value is None:
        return []
    if isinstance(value, list):
        result: list[str] = []
        for item in value:
            result.extend(split_words(item))
        return result
    return [word for word in re.split(r"[\s,]+", str(value).strip()) if word]


def shell_quote(value: str) -> str:
    return shlex.quote(value)


@dataclass
class UciChange:
    path: str
    op: Literal["set", "delete", "add_list", "del_list"]
    before: Any = None
    after: Any = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "op": self.op,
            "before": self.before,
            "after": self.after,
        }


def _normalize(value: Any) -> Any:
    if isinstance(value, list):
        return [str(item) for item in value]
    return value


def diff_configs(before: UciConfig, after: dict[str, dict[str, Any]]) -> list[UciChange]:
    """Сравнивает два снимка конфига (без учёта служебных ``.``-ключей)."""

    changes: list[UciChange] = []

    def clean(section: dict[str, Any]) -> dict[str, Any]:
        return {key: value for key, value in section.items() if not key.startswith(".")}

    before_map = {name: clean(section.to_dict()) for name, section in before.sections.items()}
    after_map = {name: clean(section) for name, section in after.items()}

    for section_name in sorted(set(before_map) | set(after_map)):
        old = before_map.get(section_name, {})
        new = after_map.get(section_name, {})
        if section_name not in after_map:
            changes.append(UciChange(f"{before.package}.{section_name}", "delete", old, None))
            continue
        if section_name not in before_map:
            for option, value in new.items():
                changes.append(UciChange(f"{before.package}.{section_name}.{option}", "set", None, value))
            continue
        for option in sorted(set(old) | set(new)):
            old_value = old.get(option)
            new_value = new.get(option)
            if _normalize(old_value) == _normalize(new_value):
                continue
            if option not in new:
                changes.append(UciChange(f"{before.package}.{section_name}.{option}", "delete", old_value, None))
            else:
                op: Literal["set", "add_list", "del_list"] = "set"
                changes.append(
                    UciChange(f"{before.package}.{section_name}.{option}", op, old_value, new_value)
                )
    return changes


def flatten_changes(changes: Iterable[UciChange]) -> list[dict[str, Any]]:
    return [change.to_dict() for change in changes]
