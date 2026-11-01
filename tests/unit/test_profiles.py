"""Тесты профилей бэкендов и автоопределения (``podkop_mcp.backends``)."""

from __future__ import annotations

import json

import pytest
from fakes import (
    FakeSession,
    FakeSSHResult,
    fail,
    make_forkop1_profile,
    make_podkop_profile,
)

from podkop_mcp.backends import (
    DESTRUCTIVE_CLI_COMMANDS,
    DETECT_SCRIPT,
    READ_ONLY_CLI_COMMANDS,
    BackendProfile,
    _parse_commands,
    detect_profile,
)
from podkop_mcp.errors import BackendError

# --------------------------------------------------------------------------- образцы help


# Похоже на вывод ``forkop`` без аргументов (Forkop 1.0.5): отступ 4 пробела.
FORKOP1_HELP = """\
Forkop 1.0.5 - sing-box proxy for OpenWrt

Usage: forkop <command> [options]

Commands:
    show_version                 показать версию
    get_status                   статус сервиса
    get_system_info              информация о системе
    check_sing_box               проверка sing-box
    check_nft                    проверка nftables
    check_fakeip                 проверка fakeip
    check_logs                   логи
    show_config                  конфиг (masked|raw)
    clash_api                    Clash API
    list_update                  обновление списков
    subscription_update          обновление подписок
    start                        запуск
    stop                         остановка
    reload                       перезагрузка

Дополнительно:
        forkop help <command>
"""

FORKOP2_HELP = """\
2.0.0
Usage: forkop <action> [args]

Actions:
    get_status
    get_system_info
    report                       сводный отчёт
    clash_summary                сводка Clash
    check_sing_box
    show_config
    list_update
    subscription_update
"""

PODKOP_HELP = """\
Usage: podkop <command>

Commands:
    get_status
    get_system_info
    check_sing_box
    check_fakeip
    show_version
"""


# --------------------------------------------------------------------------- DETECT_SCRIPT


def make_detect_output(
    *,
    forkop: bool = True,
    podkop: bool = False,
    forkop_version: str = "1.0.5",
    config_version: str | None = None,
    forkop_help: str = FORKOP1_HELP,
    podkop_version: str = "0.7.5",
    podkop_help: str = PODKOP_HELP,
    forkop_config: bool | None = None,
    podkop_config: bool | None = None,
) -> str:
    """Собирает stdout, который вернул бы :data:`DETECT_SCRIPT` на роутере."""

    has_forkop_config = forkop if forkop_config is None else forkop_config
    has_podkop_config = podkop if podkop_config is None else podkop_config
    cv = config_version if config_version is not None else (forkop_version if forkop else "")
    lines = [
        "@@BINARIES",
        "forkop=1" if forkop else "",
        "podkop=1" if podkop else "",
        "@@FORKOP_VERSION",
        forkop_version if forkop else "",
        "@@PODKOP_VERSION",
        podkop_version if podkop else "",        "@@FORKOP_CV",
        cv if forkop else "",
        "@@PODKOP_CV",
        podkop_version if podkop else "",
        "@@FORKOP_CONFIG",
        "forkop_config=1" if has_forkop_config else "",
        "@@PODKOP_CONFIG",
        "podkop_config=1" if has_podkop_config else "",
        "@@FORKOP_HELP",
        forkop_help if forkop else "",
        "@@PODKOP_HELP",
        podkop_help if podkop else "",
        "@@END",
    ]
    return "\n".join(lines)


@pytest.fixture
def detect_forkop1_output() -> str:
    """Готовый вывод DETECT_SCRIPT для Forkop 1.0.5 (фикстура-строка)."""

    return make_detect_output(forkop=True, forkop_version="1.0.5")


@pytest.fixture
def detect_forkop2_output() -> str:
    return make_detect_output(forkop=True, forkop_version="2.0.0", forkop_help=FORKOP2_HELP)


@pytest.fixture
def detect_podkop_output() -> str:
    return make_detect_output(forkop=False, podkop=True)


# --------------------------------------------------------------------------- свойства профилей


def test_forkop1_profile_properties(forkop1_profile: BackendProfile) -> None:
    profile = forkop1_profile

    assert profile.kind == "forkop"
    assert profile.display_name == "Forkop"
    assert profile.package == "forkop"
    assert profile.init_script == "/etc/init.d/forkop"
    assert profile.cli == "forkop"
    assert profile.domain_option == "domain"
    assert profile.domain_storage == "option_text"
    assert profile.domain_separator == " "
    assert profile.supports_rule_sets is False
    assert profile.supports_community_lists is True
    assert profile.clash_mode == "actions"
    assert profile.supports("get_status") is True
    assert profile.supports("uninstall") is False
    assert profile.require("get_status") is None
    with pytest.raises(BackendError, match="не поддерживает"):
        profile.require("uninstall")


def test_forkop2_profile_properties(forkop2_profile: BackendProfile) -> None:
    profile = forkop2_profile

    assert profile.schema == "forkop2"
    assert profile.version == "2.0.0"
    assert profile.domain_option == "destinations"
    assert profile.domain_storage == "option_multiline"
    assert profile.domain_separator == "\n"
    assert profile.supports_rule_sets is True
    assert profile.clash_mode == "http"
    assert profile.supports("report") is True


def test_podkop_profile_properties(podkop_profile: BackendProfile) -> None:
    profile = podkop_profile

    assert profile.kind == "podkop"
    assert profile.display_name == "Podkop"
    assert profile.package == "podkop"
    assert profile.init_script == "/etc/init.d/podkop"
    assert profile.cli == "podkop"
    assert profile.domain_option == "user_domains"
    assert profile.domain_storage == "list"
    assert profile.supports_rule_sets is False
    assert profile.clash_mode == "actions"
    assert profile.supports("report") is False


def test_profiles_public_dict_is_json_safe(forkop1_profile: BackendProfile) -> None:
    public = forkop1_profile.public_dict(include_commands=True)

    assert public["schema"] == "forkop1"
    assert public["config_package"] == "forkop"
    assert public["domain_option"] == "domain"
    assert isinstance(public["commands"], list)
    assert public["commands"] == sorted(public["commands"])
    # публичный словарь сериализуется в JSON без «нестандартных» типов
    assert json.loads(json.dumps(public))["commands"] == public["commands"]

    compact = forkop1_profile.public_dict()
    assert "commands" not in compact
    assert compact["command_count"] == len(forkop1_profile.commands)


def test_allowed_cli_commands_are_read_only(forkop1_profile: BackendProfile) -> None:
    allowed = forkop1_profile.allowed_cli_commands()

    assert allowed <= READ_ONLY_CLI_COMMANDS
    assert "start" not in allowed
    assert "reload" not in allowed
    assert "subscription_update" not in allowed
    assert allowed & DESTRUCTIVE_CLI_COMMANDS == set()

    # без списка команд разрешены только известные read-only
    empty = BackendProfile(
        kind="forkop",
        version="1.0.5",
        config_version="1.0.5",
        schema="forkop1",
        package="forkop",
        init_script="/etc/init.d/forkop",
        cli="forkop",
    )
    assert empty.allowed_cli_commands() == READ_ONLY_CLI_COMMANDS
    empty.require("anything")  # пустой список команд = не проверяем
    assert empty.supports("show_config") is False


# --------------------------------------------------------------------------- парсинг help


def test_parse_commands_on_real_like_help() -> None:
    commands = _parse_commands(FORKOP1_HELP)

    assert "show_version" in commands
    assert "get_status" in commands
    assert "subscription_update" in commands
    # строки с другим отступом и человеческие описания не попадают
    assert "forkop" not in commands
    assert "help" not in commands
    assert "Дополнительно:" not in commands


def test_parse_commands_handles_empty_and_odd_input() -> None:
    assert _parse_commands("") == set()
    assert _parse_commands("Usage: forkop\n    get_status\n") == {"get_status"}
    assert _parse_commands("    get_status extra args") == {"get_status"}
    assert _parse_commands("    UPPER_case") == set()
    assert _parse_commands("   1numeric") == set()


def test_parse_commands_requires_exact_indent() -> None:
    """Известное ограничение: распознаётся только отступ ровно в 4 пробела.

    Если help другой сборки печатает 2 пробела, список команд будет пустым,
    а ``detect_profile`` тогда разрешит все известные команды (см.
    ``test_detect_falls_back_to_failure_output``).
    """

    assert _parse_commands("Commands:\n  get_status\n") == set()
    assert _parse_commands("Commands:\n\tget_status\n") == set()
    # 5 и более пробелов тоже не распознаются
    assert _parse_commands("Commands:\n     get_status\n") == set()


# --------------------------------------------------------------------------- detect


def test_detect_forkop1(detect_forkop1_output: str) -> None:
    session = FakeSession(responses={DETECT_SCRIPT: FakeSSHResult(stdout=detect_forkop1_output)})

    profile = detect_profile(session)

    assert profile.kind == "forkop"
    assert profile.schema == "forkop1"
    assert profile.version == "1.0.5"
    assert profile.config_version == "1.0.5"
    assert profile.clash_mode == "actions"
    assert profile.domain_option == "domain"
    assert profile.supports("get_status")
    assert session.contains(DETECT_SCRIPT)


def test_detect_forkop2_uses_config_version(detect_forkop2_output: str) -> None:
    session = FakeSession(queue=[FakeSSHResult(stdout=detect_forkop2_output)])

    profile = detect_profile(session)

    assert profile.schema == "forkop2"
    assert profile.config_version == "2.0.0"
    assert profile.domain_option == "destinations"
    assert profile.clash_mode == "http"


def test_detect_forkop2_by_config_version_only(detect_forkop1_output: str) -> None:
    """Версия конфига 2.x перевешивает старую строку версии CLI."""

    output = detect_forkop1_output.replace(
        "@@FORKOP_CV\n1.0.5", "@@FORKOP_CV\n2.0.0"
    )
    session = FakeSession(default=FakeSSHResult(stdout=output))

    assert detect_profile(session).schema == "forkop2"


def test_detect_podkop(detect_podkop_output: str) -> None:
    session = FakeSession(default=FakeSSHResult(stdout=detect_podkop_output))

    profile = detect_profile(session)

    assert profile.kind == "podkop"
    assert profile.schema == "podkop"
    assert profile.package == "podkop"
    assert profile.init_script == "/etc/init.d/podkop"
    assert profile.domain_option == "user_domains"
    assert profile.supports("check_sing_box")


def test_detect_prefer_backend_errors() -> None:
    podkop_only = make_detect_output(forkop=False, podkop=True)
    session = FakeSession(default=FakeSSHResult(stdout=podkop_only))
    with pytest.raises(BackendError, match="forkop не найдена"):
        detect_profile(session, prefer="forkop")

    forkop_only = make_detect_output(forkop=True, podkop=False)
    session = FakeSession(default=FakeSSHResult(stdout=forkop_only))
    with pytest.raises(BackendError, match="podkop не найдена"):
        detect_profile(session, prefer="podkop")

    assert detect_profile(FakeSession(default=FakeSSHResult(stdout=forkop_only)), prefer="forkop").kind == "forkop"


def test_detect_nothing_installed() -> None:
    output = make_detect_output(forkop=False, podkop=False, forkop_config=False, podkop_config=False)
    session = FakeSession(default=FakeSSHResult(stdout=output))

    with pytest.raises(BackendError, match="ни Forkop, ни Podkop"):
        detect_profile(session)


def test_detect_expired_session_propagates_error() -> None:
    from podkop_mcp.errors import SSHError

    session = FakeSession(connect_error=SSHError("нет связи с роутером"))
    with pytest.raises(SSHError, match="нет связи"):
        detect_profile(session)


def test_detect_falls_back_to_failure_output() -> None:
    """Ненулевой код DETECT_SCRIPT не мешает: разбираем stdout как есть."""

    output = make_detect_output(forkop=True, forkop_help="")
    session = FakeSession(queue=[fail(stdout=output, stderr="sed: busy")])

    profile = detect_profile(session)

    # без help команды берутся из известного списка, чтобы не блокировать работу
    assert profile.schema == "forkop1"
    assert "get_status" in profile.commands
    assert profile.commands >= READ_ONLY_CLI_COMMANDS


def test_detect_unknown_version_means_forkop1() -> None:
    output = make_detect_output(forkop=True, forkop_help=FORKOP1_HELP).replace(
        "@@FORKOP_VERSION\n1.0.5", "@@FORKOP_VERSION\n"
    )
    # config_version возьмётся из версии (пусто) — тогда unknown → major 0 → forkop1
    output = output.replace("@@FORKOP_CV\n1.0.5", "@@FORKOP_CV\n")
    profile = detect_profile(FakeSession(default=FakeSSHResult(stdout=output)))

    assert profile.schema == "forkop1"
    assert profile.version == "unknown"


# --------------------------------------------------------------------------- фабрики


def test_profile_factories_are_independent(forkop1_profile: BackendProfile) -> None:
    """Фабрики возвращают новые объекты: правка commands не задевает других."""

    another = make_forkop1_profile()
    another.commands.add("uninstall")
    assert "uninstall" not in forkop1_profile.commands

    podkop = make_podkop_profile()
    podkop.commands.add("report")
    assert "report" not in make_podkop_profile().commands
