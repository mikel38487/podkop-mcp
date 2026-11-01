"""Общие фикстуры тестов podkop-mcp.

Тесты не ходят в сеть и не подключаются к роутеру: используются только
санитизированные фикстуры из ``tests/fixtures`` и фейки из ``tests.fakes``.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
FIXTURES = Path(__file__).resolve().parent / "fixtures"

# В этой среде tempfile.gettempdir() может указывать на корень репозитория
# (если TEMP переопределён песочницей): pytest тогда создаёт здесь
# ``pytest-of-<user>``. Уводим временные каталоги в ``.pytest_cache``.
if Path(tempfile.gettempdir()).resolve() == ROOT:
    _cache = ROOT / ".pytest_cache" / "tmp"
    _cache.mkdir(parents=True, exist_ok=True)
    os.environ["TMPDIR"] = str(_cache)
    os.environ["TEMP"] = str(_cache)
    os.environ["TMP"] = str(_cache)
    tempfile.tempdir = str(_cache)

# ``src`` может быть не установлен в editable-режиме — добавляем вручную.
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fakes import (  # noqa: E402
    FakeRouter,
    FakeSession,
    make_forkop1_profile,
    make_forkop2_profile,
    make_podkop_profile,
)

from podkop_mcp.backends import BackendProfile  # noqa: E402
from podkop_mcp.config import Settings, TargetConfig  # noqa: E402
from podkop_mcp.uci import UciConfig, parse_ubus_uci, parse_uci_export  # noqa: E402

# --------------------------------------------------------------------------- пути


@pytest.fixture(scope="session")
def fixtures_dir() -> Path:
    """Каталог с санитизированными фикстурами."""

    return FIXTURES


@pytest.fixture(scope="session")
def repo_root() -> Path:
    """Корень репозитория (там лежат pyproject.toml и src)."""

    return ROOT


@pytest.fixture(scope="session")
def read_fixture():
    """Фабрика: возвращает текст фикстуры по имени файла."""

    def _read(name: str) -> str:
        path = FIXTURES / name
        if not path.is_file():
            raise FileNotFoundError(f"Нет фикстуры {path}")
        return path.read_text(encoding="utf-8")

    return _read


@pytest.fixture(scope="session")
def read_fixture_json():
    """Фабрика: возвращает JSON-фикстуру по имени файла."""

    def _read(name: str) -> Any:
        return json.loads((FIXTURES / name).read_text(encoding="utf-8"))

    return _read


# --------------------------------------------------------------------------- конфиги


@pytest.fixture(scope="session")
def forkop1_ubus(read_fixture_json) -> dict[str, Any]:
    """Ответ ``ubus call uci get`` для Forkop 1.0.5."""

    return read_fixture_json("forkop1-uci.json")


@pytest.fixture(scope="session")
def forkop1_values(forkop1_ubus: dict[str, Any]) -> dict[str, Any]:
    """Только ``values`` — как отдаёт ubus."""

    return forkop1_ubus["values"]


@pytest.fixture
def forkop1_config(forkop1_values: dict[str, Any]) -> UciConfig:
    """Свежая копия UCI-конфига Forkop 1.x."""

    return parse_ubus_uci({"values": forkop1_values}, "forkop")


@pytest.fixture(scope="session")
def podkop_default_text(read_fixture) -> str:
    """UCI-экспорт конфига Podkop по умолчанию."""

    return read_fixture("podkop-default.uci")


@pytest.fixture(scope="session")
def forkop2_default_text(read_fixture) -> str:
    """UCI-экспорт конфига Forkop 2.x по умолчанию."""

    return read_fixture("forkop2-default.uci")


@pytest.fixture
def podkop_config(podkop_default_text: str) -> UciConfig:
    """Разобранный upstream-конфиг Podkop."""

    return parse_uci_export(podkop_default_text, "podkop")


@pytest.fixture
def forkop1_export_config(read_fixture) -> UciConfig:
    """``forkop show_config masked`` как резервный источник конфига."""

    return parse_uci_export(read_fixture("forkop1-showconfig-masked.txt"), "forkop")


# --------------------------------------------------------------------------- профили


@pytest.fixture
def forkop1_profile() -> BackendProfile:
    return make_forkop1_profile()


@pytest.fixture
def forkop2_profile() -> BackendProfile:
    return make_forkop2_profile()


@pytest.fixture
def podkop_profile() -> BackendProfile:
    return make_podkop_profile()


# --------------------------------------------------------------------------- settings


@pytest.fixture
def router_target() -> TargetConfig:
    """Target без реального подключения: localhost и несуществующий ключ."""

    return TargetConfig(
        name="router",
        host="127.0.0.1",
        port=1,
        user="root",
        key_path=str(FIXTURES / "missing-key"),
        host_key_policy="accept-new",
    )


@pytest.fixture
def settings(router_target: TargetConfig) -> Settings:
    """Настройки с одним target и безопасным каталогом бэкапов."""

    return Settings(
        targets={router_target.name: router_target},
        default_target=router_target.name,
        backup_dir="/tmp/podkop-mcp-backups",
        backup_keep=5,
    )


# --------------------------------------------------------------------------- фейки


@pytest.fixture
def fake_session() -> FakeSession:
    """Строгая сессия: незапланированный скрипт — ошибка теста."""

    return FakeSession()


@pytest.fixture
def forkop1_router(forkop1_config: UciConfig, settings: Settings) -> FakeRouter:
    """FakeRouter с профилем Forkop 1.x и конфигом из фикстуры."""

    return FakeRouter(profile=make_forkop1_profile(), config=forkop1_config, settings=settings)
