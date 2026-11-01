"""Профили бэкендов Podkop/Forkop.

Одна и та же операция «добавить домен» выглядит по-разному:

* Forkop 1.x — ``section.domain``: одна строка, значения через пробел;
* Forkop 2.x — ``section.destinations``: текст, по одному значению в строке;
* Podkop      — ``section.user_domains``: UCI-список.

Профиль инкапсулирует эти различия, а инструменты работают с абстракцией
«секция + список доменов».
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from .errors import BackendError
from .ssh import SSHResult

ClashMode = Literal["actions", "http"]
DomainStorage = Literal["option_text", "option_multiline", "list"]

# Команды, которые ничего не меняют на роутере. Всё остальное через
# podkop_cli запрещено — для записи есть отдельные инструменты.
READ_ONLY_CLI_COMMANDS = {
    "check_proxy",
    "check_nft",
    "check_nft_rules",
    "check_sing_box",
    "check_inbounds_config",
    "check_inbounds",
    "check_logs",
    "check_sing_box_logs",
    "check_fakeip",
    "check_zapret_runtime",
    "check_zapret2_runtime",
    "check_byedpi_runtime",
    "check_dns_available",
    "check_service",
    "check_dns",
    "check_traffic",
    "check_servers",
    "global_check",
    "show_config",
    "show_version",
    "show_sing_box_config",
    "show_sing_box_version",
    "show_system_info",
    "get_status",
    "get_outbound_metadata",
    "get_subscription_metadata",
    "get_sing_box_status",
    "get_zapret_status",
    "get_zapret2_status",
    "get_byedpi_status",
    "get_system_info",
    "get_server_capabilities",
    "get_ui_capabilities",
    "get_ui_state",
    "get_core_features",
    "get_subscription_default_headers",
    "get_tls_certificate_sha256",
    "service_action_status",
    "latency_test_status",
    "component_update_check_cache",
    "clash_summary",
    "report",
    "validate_nfqws_strategy_json",
    "validate_nfqws2_strategy_json",
    "validate_byedpi_strategy_json",
    "initd_status",
    "initd_triggers",
    "generate_reality_keypair",
}

DESTRUCTIVE_CLI_COMMANDS = {
    "start",
    "stop",
    "reload",
    "restart",
    "enable",
    "disable",
    "uninstall",
    "main",
    "list_update",
    "list_update_if_due",
    "subscription_update",
    "subscription_update_async",
    "subscription_update_if_due",
    "service_action_async",
    "ui_action_ack",
    "component_action",
    "component_action_async",
    "component_updates_if_due",
    "installer_start",
    "installer_run",
    "import_config",
    "generate_warp_profile",
    "initd_start",
    "initd_stop",
    "initd_restart",
    "initd_reload",
    "initd_wan_up",
    "initd_retry_start",
    "sing_box_crash_cleanup",
    "sing_box_crash_pause",
    "sing_box_crash_resume",
    "sync_rule_sets",
    "luci_postinst",
    "luci_prerm",
    "package_postinst",
    "package_prerm",
}


class Runner(Protocol):
    def run(self, script: str, *, timeout: float | None = None, check: bool = False) -> SSHResult: ...

    def run_argv(
        self, program: str, args: list[str] | tuple[str, ...], *, timeout: float | None = None, check: bool = False
    ) -> SSHResult: ...


@dataclass
class BackendProfile:
    kind: Literal["forkop", "podkop"]
    version: str
    config_version: str
    schema: Literal["forkop1", "forkop2", "podkop"]
    package: str
    init_script: str
    cli: str
    commands: set[str] = field(default_factory=set)
    clash_mode: ClashMode = "actions"

    @property
    def display_name(self) -> str:
        return "Forkop" if self.kind == "forkop" else "Podkop"

    @property
    def domain_option(self) -> str:
        if self.schema == "forkop2":
            return "destinations"
        if self.schema == "podkop":
            return "user_domains"
        return "domain"

    @property
    def domain_storage(self) -> DomainStorage:
        if self.schema == "podkop":
            return "list"
        if self.schema == "forkop2":
            return "option_multiline"
        return "option_text"

    @property
    def domain_separator(self) -> str:
        return "\n" if self.schema == "forkop2" else " "

    @property
    def supports_rule_sets(self) -> bool:
        return self.schema == "forkop2"

    @property
    def supports_community_lists(self) -> bool:
        return True

    def supports(self, command: str) -> bool:
        return command in self.commands

    def require(self, command: str) -> None:
        if self.commands and command not in self.commands:
            raise BackendError(
                f"{self.display_name} {self.version} не поддерживает команду {command!r}"
            )

    def public_dict(self, *, include_commands: bool = False) -> dict[str, Any]:
        result: dict[str, Any] = {
            "kind": self.kind,
            "name": self.display_name,
            "version": self.version,
            "config_version": self.config_version,
            "schema": self.schema,
            "config_package": self.package,
            "init_script": self.init_script,
            "cli": self.cli,
            "clash_mode": self.clash_mode,
            "domain_option": self.domain_option,
            "domain_storage": self.domain_storage,
            "supports_rule_sets": self.supports_rule_sets,
            "command_count": len(self.commands),
        }
        if include_commands:
            result["commands"] = sorted(self.commands)
        return result

    def allowed_cli_commands(self) -> set[str]:
        if self.commands:
            return self.commands & READ_ONLY_CLI_COMMANDS
        return set(READ_ONLY_CLI_COMMANDS)


def _parse_commands(help_text: str) -> set[str]:
    commands: set[str] = set()
    for line in help_text.splitlines():
        match = re.match(r"^\s{4}([a-z][a-z0-9_]*)\b", line)
        if match:
            commands.add(match.group(1))
    return commands


def _parse_version(text: str) -> str:
    for line in text.splitlines():
        line = line.strip()
        if re.match(r"^\d+\.\d+", line):
            return line.split()[0]
    return text.strip().splitlines()[0].strip() if text.strip() else ""


DETECT_SCRIPT = r"""
echo '@@BINARIES'
if command -v forkop >/dev/null 2>&1; then echo 'forkop=1'; fi
if command -v podkop >/dev/null 2>&1; then echo 'podkop=1'; fi
echo '@@FORKOP_VERSION'
forkop show_version 2>/dev/null || true
echo '@@PODKOP_VERSION'
podkop show_version 2>/dev/null || true
echo '@@FORKOP_CV'
uci -q get forkop.settings.config_version 2>/dev/null || true
echo '@@PODKOP_CV'
uci -q get podkop.settings.config_version 2>/dev/null || true
echo '@@FORKOP_CONFIG'
uci -q show forkop >/dev/null 2>&1 && echo 'forkop_config=1' || true
echo '@@PODKOP_CONFIG'
uci -q show podkop >/dev/null 2>&1 && echo 'podkop_config=1' || true
echo '@@FORKOP_HELP'
forkop 2>&1 | sed -n '1,160p' || true
echo '@@PODKOP_HELP'
podkop 2>&1 | sed -n '1,160p' || true
echo '@@END'
"""


def _section(text: str, name: str) -> str:
    marker = f"@@{name}"
    start = text.find(marker)
    if start < 0:
        return ""
    start += len(marker)
    end = text.find("@@", start)
    return text[start:end] if end >= 0 else text[start:]


def detect_profile(runner: Runner, *, prefer: str = "auto") -> BackendProfile:
    result = runner.run(DETECT_SCRIPT, timeout=45)
    text = result.stdout
    binaries = _section(text, "BINARIES")
    has_forkop = "forkop=1" in binaries
    has_podkop = "podkop=1" in binaries
    forkop_cfg = "forkop_config=1" in _section(text, "FORKOP_CONFIG")
    podkop_cfg = "podkop_config=1" in _section(text, "PODKOP_CONFIG")

    if prefer == "forkop" and not has_forkop:
        raise BackendError("Запрошен Forkop, но команда forkop не найдена")
    if prefer == "podkop" and not has_podkop:
        raise BackendError("Запрошен Podkop, но команда podkop не найдена")

    if has_forkop:
        kind: Literal["forkop", "podkop"] = "forkop"
    elif has_podkop:
        kind = "podkop"
    elif forkop_cfg:
        kind = "forkop"
    elif podkop_cfg:
        kind = "podkop"
    else:
        raise BackendError(
            "На роутере не найден ни Forkop, ни Podkop. "
            "Ожидались команды forkop/podkop и UCI-пакет forkop/podkop."
        )

    if kind == "forkop":
        version = _parse_version(_section(text, "FORKOP_VERSION")) or "unknown"
        config_version = _section(text, "FORKOP_CV").strip() or version
        help_text = _section(text, "FORKOP_HELP")
        package = "forkop"
        init_script = "/etc/init.d/forkop"
        cli = "forkop"
        major = 0
        match = re.match(r"^(\d+)", config_version) or re.match(r"^(\d+)", version)
        if match:
            major = int(match.group(1))
        schema: Literal["forkop1", "forkop2"] = "forkop2" if major >= 2 else "forkop1"
        clash_mode: ClashMode = "http" if schema == "forkop2" else "actions"
    else:
        version = _parse_version(_section(text, "PODKOP_VERSION")) or "unknown"
        config_version = _section(text, "PODKOP_CV").strip() or version
        help_text = _section(text, "PODKOP_HELP")
        package = "podkop"
        init_script = "/etc/init.d/podkop"
        cli = "podkop"
        schema = "podkop"
        clash_mode = "actions"

    commands = _parse_commands(help_text)
    if not commands and kind == "forkop":
        # Старые сборки могут печатать help в stderr или иначе — не блокируемся.
        commands = set(READ_ONLY_CLI_COMMANDS) | DESTRUCTIVE_CLI_COMMANDS

    return BackendProfile(
        kind=kind,
        version=version,
        config_version=config_version,
        schema=schema,
        package=package,
        init_script=init_script,
        cli=cli,
        commands=commands,
        clash_mode=clash_mode,
    )
