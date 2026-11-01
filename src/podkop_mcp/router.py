"""Высокоуровневый доступ к роутеру: UCI, CLI, бэкапы, применение изменений."""

from __future__ import annotations

import copy
import json
import logging
import re
import shlex
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal

from .backends import BackendProfile, detect_profile
from .config import Settings, TargetConfig
from .domains_core import entry_key, parse_entries
from .errors import BackendError, ConfigError, RemoteCommandError, SafetyError
from .redaction import mask_value, redact_config, redact_text
from .ssh import SSHSession
from .uci import UciChange, UciConfig, diff_configs, parse_ubus_uci, parse_uci_export, validate_package

log = logging.getLogger(__name__)

BACKUP_ID_RE = re.compile(r"^\d{8}T\d{6}Z-[a-z0-9-]{1,40}\.uci$")
ALLOWED_BACKUP_ROOT = re.compile(r"^/[A-Za-z0-9_./-]+$")
SAFE_REASON_RE = re.compile(r"[^a-z0-9-]+")


def _value_expr(value: str) -> str:
    """Безопасно экранирует значение для POSIX shell (без base64 на роутере)."""

    return shlex.quote(str(value))


@dataclass
class UciOp:
    op: Literal["set", "delete", "add_list", "del_list", "set_list", "delete_section", "create_section"]
    path: str
    value: Any = None
    section_type: str | None = None


@dataclass
class ChangePlan:
    target: str
    package: str
    reason: str
    changes: list[UciChange]
    preview: list[str]
    warnings: list[str] = field(default_factory=list)
    ops: list[UciOp] = field(default_factory=list, repr=False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "target": self.target,
            "package": self.package,
            "reason": self.reason,
            "changes": [_safe_change(change) for change in self.changes],
            "preview": self.preview,
            "warnings": self.warnings,
            "change_count": len(self.changes),
        }


class PlanBuilder:
    """Строит план изменений и человекочитаемый diff."""

    def __init__(self, router: Router, package: str, reason: str) -> None:
        self.router = router
        self.package = package
        self.reason = reason
        self.ops: list[UciOp] = []
        self.warnings: list[str] = []
        self.before = router.config(package)
        self.after: dict[str, dict[str, Any]] = {
            name: copy.deepcopy(section.options) for name, section in self.before.sections.items()
        }
        self.types: dict[str, str] = {name: section.type for name, section in self.before.sections.items()}

    # ------------------------------------------------------------------ helpers
    def _resolve(self, section: str) -> str:
        """Приводит ``cfg...``/``@type[0]`` к каноническому имени секции."""

        canonical = self.before.resolve_section_name(section)
        if canonical not in self.after:
            known = ", ".join(sorted(self.after)[:20]) or "нет"
            raise ConfigError(f"Секция {section!r} не найдена в {self.package}. Есть: {known}")
        return canonical

    def _option_path(self, section: str, option: str) -> tuple[str, str]:
        canonical = self._resolve(section)
        if not re.fullmatch(r"[A-Za-z0-9_]+", option):
            raise ConfigError(f"Недопустимое имя UCI-опции: {option!r}")
        return canonical, f"{self.package}.{canonical}.{option}"

    def _prepare_option(self, section: str, option: str) -> None:
        self.after.setdefault(section, {})
        self.after[section].pop(option, None)

    # ------------------------------------------------------------------ ops
    def create_section(self, name: str, section_type: str) -> None:
        name = self.before.resolve_section_name(name)
        if name in self.after:
            raise ConfigError(f"Секция {name!r} уже существует")
        if not re.fullmatch(r"[A-Za-z0-9_@\[\]-]+", name):
            raise ConfigError(f"Недопустимое имя секции: {name!r}")
        if not re.fullmatch(r"[A-Za-z0-9_-]+", section_type):
            raise ConfigError(f"Недопустимый тип секции: {section_type!r}")
        self.ops.append(UciOp("create_section", f"{self.package}.{name}", section_type=section_type))
        self.after[name] = {}
        self.types[name] = section_type

    def delete_section(self, name: str) -> None:
        name = self._resolve(name)
        self.ops.append(UciOp("delete_section", f"{self.package}.{name}"))
        self.after.pop(name, None)
        self.types.pop(name, None)

    def set_option(self, section: str, option: str, value: Any) -> None:
        section, path = self._option_path(section, option)
        self._prepare_option(section, option)
        self.ops.append(UciOp("set", path, value))
        self.after[section][option] = value

    def delete_option(self, section: str, option: str) -> None:
        section, path = self._option_path(section, option)
        if option not in self.after.get(section, {}):
            self.warnings.append(f"Опция {path} отсутствует — удаление пропущено")
            return
        self._prepare_option(section, option)
        self.ops.append(UciOp("delete", path))

    def set_list(self, section: str, option: str, values: list[str]) -> None:
        section, path = self._option_path(section, option)
        self._prepare_option(section, option)
        self.ops.append(UciOp("set_list", path, list(values)))
        self.after[section][option] = list(values)

    def add_list(self, section: str, option: str, values: list[str]) -> None:
        section, path = self._option_path(section, option)
        existing = self.after.setdefault(section, {}).get(option, [])
        if not isinstance(existing, list):
            existing = [existing] if existing not in (None, "") else []
        merged = list(existing)
        keys = {entry_key(str(item)) for item in merged}
        new_values: list[str] = []
        for value in values:
            value = str(value)
            if not value.strip() or entry_key(value) in keys:
                continue
            new_values.append(value)
            keys.add(entry_key(value))
        if not new_values:
            self.warnings.append(f"{path}: добавлять нечего — все значения уже есть или пусты")
            return
        if isinstance(self.after.get(section, {}).get(option), list):
            self.ops.append(UciOp("add_list", path, new_values))
        else:
            # Строковую опцию нельзя наращивать через add_list: сначала удаляем
            # старое значение, затем пишем полноценный список.
            self.ops.append(UciOp("set_list", path, [*merged, *new_values]))
        self.after[section][option] = [*merged, *new_values]

    def del_list(self, section: str, option: str, values: list[str]) -> None:
        section, path = self._option_path(section, option)
        existing = self.after.setdefault(section, {}).get(option, [])
        if not isinstance(existing, list):
            raise ConfigError(f"{path}: опция не является списком, удаление через del_list невозможно")
        remove = {entry_key(str(value)) for value in values}
        remaining = [item for item in existing if entry_key(str(item)) not in remove]
        removed = [str(item) for item in existing if entry_key(str(item)) in remove]
        if not removed:
            self.warnings.append(f"{path}: удалять нечего — значений нет в списке")
            return
        self.ops.append(UciOp("del_list", path, removed))
        self.after[section][option] = remaining

    # ------------------------------------------------------------------ build
    def build(self) -> ChangePlan:
        if not self.ops:
            raise ConfigError("План пуст: нет ни одного изменения")
        changes = diff_configs(self.before, self.after)
        preview: list[str] = []
        for op in self.ops:
            if op.op == "create_section":
                preview.append(f"uci set '{op.path}={op.section_type}'")
            elif op.op == "delete_section":
                preview.append(f"uci delete '{op.path}'")
            elif op.op == "delete":
                preview.append(f"uci delete '{op.path}'")
            elif op.op == "set":
                preview.append(f"uci set '{op.path}={_preview_value(op.path, op.value)}'")
            elif op.op == "set_list":
                preview.append(f"uci delete '{op.path}'")
                for item in op.value or []:
                    preview.append(f"uci add_list '{op.path}={_preview_value(op.path, item)}'")
            elif op.op == "add_list":
                for item in op.value or []:
                    preview.append(f"uci add_list '{op.path}={_preview_value(op.path, item)}'")
            elif op.op == "del_list":
                for item in op.value or []:
                    preview.append(f"uci del_list '{op.path}={_preview_value(op.path, item)}'")
        preview.append(f"uci commit {self.package}")
        preview.append(f"{self.router.profile.init_script} reload")
        return ChangePlan(
            target=self.router.name,
            package=self.package,
            reason=self.reason,
            changes=changes,
            preview=preview,
            warnings=self.warnings,
            ops=self.ops,
        )


def _preview_value(path: str, value: Any) -> str:
    option = path.rsplit(".", 1)[-1]
    if isinstance(value, list):
        return " ".join(str(item) for item in value)
    masked = mask_value(option, value)
    text = str(masked)
    if len(text) > 120:
        text = text[:117] + "..."
    return text


def _safe_change(change: UciChange) -> dict[str, Any]:
    """Сериализует change, маскируя секретные значения опции."""

    option = change.path.rsplit(".", 1)[-1]
    data = change.to_dict()
    data["before"] = mask_value(option, data["before"])
    data["after"] = mask_value(option, data["after"])
    return data


class Router:
    """Один роутер: SSH-сессия, профиль бэкенда, UCI и сервисные операции."""

    def __init__(self, session: SSHSession, target: TargetConfig, settings: Settings, *, name: str | None = None) -> None:
        self.session = session
        self.target = target
        self.settings = settings
        self.name = name or target.name
        self._profile: BackendProfile | None = None
        self._profile_checked_at = 0.0
        self._config_cache: dict[str, UciConfig] = {}
        self._last_backup: dict[str, Any] | None = None

    # ------------------------------------------------------------------ profile
    @property
    def profile(self) -> BackendProfile:
        if self._profile is None:
            self._load_profile()
        elif self.settings.profile_ttl > 0 and (
            time.monotonic() - self._profile_checked_at >= self.settings.profile_ttl
        ):
            self._revalidate_profile()
        assert self._profile is not None
        return self._profile

    def _load_profile(self) -> None:
        self._profile = detect_profile(self.session, prefer=self.target.backend)
        self._profile_checked_at = time.monotonic()

    def _profile_version(self) -> str | None:
        """Дешёвая проверка версии конфига без полного detect-скрипта."""

        assert self._profile is not None
        pkg = self._profile.package
        result = self.run(f"uci -q get {_sh(pkg)}.settings.config_version", timeout=10)
        if result.ok and result.stdout.strip():
            return result.stdout.strip()
        try:
            result = self.session.run_argv(self._profile.cli, ["show_version"], timeout=10)
        except Exception:
            return None
        if result.ok and result.stdout.strip():
            return result.stdout.strip().splitlines()[-1].strip()
        return None

    def _revalidate_profile(self) -> None:
        try:
            current = self._profile_version()
        except Exception as exc:  # временный сбой SSH не должен ломать кеш
            log.debug("Не удалось проверить версию backend: %s", exc)
            return
        assert self._profile is not None
        if current is not None and current in {
            self._profile.config_version,
            self._profile.version,
        }:
            self._profile_checked_at = time.monotonic()
            return
        log.info("Версия backend изменилась (%s -> %s), пересобираю профиль", self._profile.version, current)
        self._load_profile()
        self._config_cache.clear()

    def refresh_profile(self) -> BackendProfile:
        """Принудительно перечитать профиль (например, в doctor)."""

        self._load_profile()
        self._config_cache.clear()
        assert self._profile is not None
        return self._profile

    def invalidate(self) -> None:
        self._config_cache.clear()
        self._profile_checked_at = 0.0

    # ------------------------------------------------------------------ raw exec
    def run(self, script: str, *, timeout: float | None = None, check: bool = False) -> Any:
        return self.session.run(script, timeout=timeout, check=check)

    def cli(self, *args: str, timeout: float | None = None, check: bool = False) -> Any:
        result = self.session.run_argv(self.profile.cli, list(args), timeout=timeout, check=check)
        return result

    def cli_json(self, *args: str, timeout: float | None = None) -> dict[str, Any]:
        result = self.cli(*args, timeout=timeout)
        if not result.ok:
            raise RemoteCommandError(
                f"{self.profile.cli} {' '.join(args)} завершился с кодом {result.exit_code}: {redact_text(result.tail())}",
                exit_code=result.exit_code,
                stdout=result.stdout,
                stderr=result.stderr,
            )
        return parse_json_output(result.stdout, source=f"{self.profile.cli} {' '.join(args)}")

    def service_cli(self, *args: str, timeout: float | None = None) -> Any:
        """Команда управления сервисом (start/stop/reload/...)."""

        return self.session.run_argv(self.profile.cli, list(args), timeout=timeout)

    # ------------------------------------------------------------------ UCI read
    def config(self, package: str | None = None, *, refresh: bool = False) -> UciConfig:
        pkg = validate_package(package or self.profile.package)
        if pkg != self.profile.package:
            raise ConfigError(
                f"Чтение разрешено только для пакета {self.profile.package!r}, запрошен {pkg!r}"
            )
        if pkg in self._config_cache and not refresh:
            return self._config_cache[pkg]
        result = self.run(f"ubus call uci get '{{\"config\":\"{pkg}\"}}'", timeout=20)
        if result.ok and result.stdout.strip().startswith("{"):
            try:
                config = parse_ubus_uci(json.loads(result.stdout), pkg)
                self._config_cache[pkg] = config
                return config
            except (json.JSONDecodeError, ConfigError):
                log.debug("ubus uci get не разобрался, пробую uci export")
        export = self.run(f"uci export {_sh(pkg)}", timeout=20, check=True)
        config = parse_uci_export(export.stdout, pkg)
        if not config.sections:
            raise BackendError(
                f"UCI-пакет {pkg!r} пуст или не найден. Проверьте, установлен ли {self.profile.display_name}."
            )
        self._config_cache[pkg] = config
        return config

    def config_public(self, *, reveal_secrets: bool = False, package: str | None = None) -> dict[str, Any]:
        config = self.config(package)
        data = config.to_public_dict()
        if not reveal_secrets:
            data["sections"] = redact_config(data["sections"])
        return data

    def uci_export(self, *, mode: Literal["masked", "raw"] = "masked") -> str:
        pkg = self.profile.package
        if mode == "masked" and self.profile.supports("show_config"):
            result = self.cli("show_config", "masked", timeout=30)
            if result.ok and result.stdout.strip():
                return redact_text(result.stdout)
        result = self.run(f"uci export {pkg}", timeout=20, check=True)
        text = result.stdout
        return text if mode == "raw" else redact_text(text)

    # ------------------------------------------------------------------ UCI write
    def builder(self, reason: str, package: str | None = None) -> PlanBuilder:
        return PlanBuilder(self, package or self.profile.package, _safe_reason(reason))

    def apply_plan(self, plan: ChangePlan, *, wait: bool = True, timeout: float = 35.0) -> dict[str, Any]:
        if self.settings.read_only:
            raise SafetyError("PODKOP_MCP_READ_ONLY=1: запись на роутер запрещена")
        if not plan.ops:
            raise ConfigError("Нечего применять: план пуст")
        if self._profile is None:
            _ = self.profile

        was_running = self._service_running()
        backup = self.backup(plan.reason)
        script = self._render_ops(plan)
        try:
            self.run(script, timeout=90, check=True)
        except RemoteCommandError as exc:
            # UCI-изменения могли остаться незакоммиченными — откатываем сессию UCI.
            self.run(f"uci revert {plan.package} 2>/dev/null || true", timeout=15)
            raise RemoteCommandError(
                f"Не удалось применить изменения: {redact_text(exc.tail())}. Изменения не закоммичены.",
                exit_code=exc.exit_code,
                stdout=exc.stdout,
                stderr=exc.stderr,
            ) from exc

        self.invalidate()
        try:
            if wait and was_running is False and self.profile.kind == "podkop":
                # Podkop не запускается из reload при остановленном сервисе и
                # без настроенного outbound возвращает код 1. Коммитим конфиг,
                # запуск — отдельным podkop_service_action.
                service = {
                    "action": "reload",
                    "exit_code": 0,
                    "running": 0,
                    "status": "stopped",
                    "warning": "Podkop был остановлен до apply; конфиг записан, но сервис не запускался.",
                }
            else:
                service = (
                    self.reload_and_wait(timeout=timeout, require_running=bool(was_running))
                    if wait
                    else self.reload_service()
                )
        except Exception as exc:
            restored = self._restore_backup(backup["id"], reload=True, require_running=bool(was_running))
            raise BackendError(
                f"Изменения применены, но сервис не поднялся: {exc}. "
                f"Выполнен автоматический откат к бэкапу {backup['id']} (restore={restored})."
            ) from exc

        self.invalidate()
        health = self.quick_health()
        return {
            "ok": True,
            "target": self.name,
            "package": plan.package,
            "reason": plan.reason,
            "backup_id": backup["id"],
            "changes": [_safe_change(change) for change in plan.changes],
            "service": service,
            "health": health,
            "warnings": plan.warnings,
        }

    def _render_ops(self, plan: ChangePlan) -> str:
        pkg = validate_package(plan.package)
        lines = ["set -e"]
        for op in plan.ops:
            if op.op == "create_section":
                lines.append(f"uci set '{op.path}={op.section_type}'")
            elif op.op == "delete_section":
                lines.append(f"uci delete '{op.path}' 2>/dev/null || true")
            elif op.op == "delete":
                lines.append(f"uci delete '{op.path}' 2>/dev/null || true")
            elif op.op == "set":
                lines.append(f"uci delete '{op.path}' 2>/dev/null || true")
                lines.append(f"uci set '{op.path}'={_value_expr(str(op.value))}")
            elif op.op == "set_list":
                lines.append(f"uci delete '{op.path}' 2>/dev/null || true")
                for item in op.value or []:
                    lines.append(f"uci add_list '{op.path}'={_value_expr(str(item))}")
            elif op.op == "add_list":
                for item in op.value or []:
                    lines.append(f"uci add_list '{op.path}'={_value_expr(str(item))}")
            elif op.op == "del_list":
                for item in op.value or []:
                    lines.append(f"uci del_list '{op.path}'={_value_expr(str(item))} 2>/dev/null || true")
        lines.append(f"uci commit {_sh(pkg)}")
        return "\n".join(lines)

    # ------------------------------------------------------------------ service
    def reload_service(self, timeout: float | None = None) -> dict[str, Any]:
        init = self.profile.init_script
        result = self.run(f"{init} reload", timeout=timeout or 60)
        return {"action": "reload", "exit_code": result.exit_code, "output": redact_text(result.tail())}

    def is_running(self) -> bool | None:
        """Запущен ли сервис.

        Forkop отдаёт ``running`` в get_status; Podkop — нет, поэтому для него
        дополнительно проверяется процесс sing-box через ``pidof``.
        """

        try:
            status = self.cli_json("get_status", timeout=10)
            if "running" in status:
                return int(status.get("running", 0) or 0) == 1
        except Exception:
            pass
        if self.profile.kind == "podkop":
            try:
                result = self.run("pidof sing-box >/dev/null 2>&1 && echo 1 || echo 0", timeout=10)
                if result.ok and result.stdout.strip() in {"0", "1"}:
                    return result.stdout.strip() == "1"
            except Exception:
                return None
        return None

    def _service_running(self) -> bool | None:
        return self.is_running()

    def reload_and_wait(self, *, timeout: float = 35.0, require_running: bool = True) -> dict[str, Any]:
        result = self.run(f"{self.profile.init_script} reload", timeout=60)
        if result.exit_code != 0:
            raise BackendError(
                f"{self.profile.display_name}: reload завершился с кодом {result.exit_code}: {redact_text(result.tail())}"
            )
        deadline = time.monotonic() + timeout
        last: dict[str, Any] = {}
        while time.monotonic() < deadline:
            try:
                last = self.cli_json("get_status", timeout=10)
            except Exception as exc:  # сервис ещё перезапускается
                log.debug("get_status пока недоступен: %s", exc)
            if self.is_running() is True:
                return {
                    "action": "reload",
                    "exit_code": result.exit_code,
                    "running": 1,
                    "status": last.get("status"),
                    "dns_configured": last.get("dns_configured"),
                }
            if not require_running:
                # Сервис был остановлен до apply: reload применил конфиг, но
                # запускать его — отдельное решение пользователя.
                time.sleep(1.0)
                break
            time.sleep(1.5)
        if not require_running:
            return {
                "action": "reload",
                "exit_code": result.exit_code,
                "running": 0,
                "status": (last or {}).get("status"),
                "warning": "Сервис был остановлен до apply; конфиг записан, но не запущен.",
            }
        raise BackendError(
            f"{self.profile.display_name} не поднялся за {timeout:.0f} с "
            f"(reload exit={result.exit_code}, status={last})"
        )

    def service_action(self, action: str, *, timeout: float = 45.0) -> dict[str, Any]:
        if action not in {"start", "stop", "restart", "reload", "enable", "disable"}:
            raise SafetyError(f"Недопустимое действие сервиса: {action!r}")
        if self.settings.read_only:
            raise SafetyError("PODKOP_MCP_READ_ONLY=1: управление сервисом запрещено")
        result = self.run(f"{self.profile.init_script} {action}", timeout=timeout)
        status: dict[str, Any] = {}
        try:
            status = self.cli_json("get_status", timeout=10)
        except Exception:
            pass
        return {"action": action, "exit_code": result.exit_code, "output": redact_text(result.tail()), "status": status}

    def quick_health(self) -> dict[str, Any]:
        """Быстрая проверка после записи — без тяжёлых сетевых тестов."""

        health: dict[str, Any] = {}
        for command, key in (("check_sing_box", "sing_box"), ("get_status", "status")):
            if self.profile.commands and command not in self.profile.commands:
                continue
            try:
                health[key] = self.cli_json(command, timeout=25)
            except Exception as exc:
                health[key] = {"error": str(exc)}
        return health

    # ------------------------------------------------------------------ backups
    def backup(self, reason: str = "manual") -> dict[str, Any]:
        if self.settings.read_only:
            raise SafetyError("PODKOP_MCP_READ_ONLY=1: создание бэкапа на роутере запрещено")
        backup_dir = self.settings.backup_dir
        if not ALLOWED_BACKUP_ROOT.fullmatch(backup_dir) or ".." in backup_dir:
            raise SafetyError(f"Недопустимый каталог бэкапов: {backup_dir!r}")
        safe_reason = _safe_reason(reason)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        filename = f"{timestamp}-{safe_reason}.uci"
        path = f"{backup_dir}/{filename}"
        keep = max(1, int(self.settings.backup_keep))
        script = "\n".join(
            [
                "set -e",
                f"mkdir -p {_sh(backup_dir)}",
                f"cp -f {_sh('/etc/config/' + self.profile.package)} {_sh(path)}",
                f"sha256sum {_sh(path)} | awk '{{print $1}}' > {_sh(path + '.sha256')}",
                f"ls -1t {_sh(backup_dir)}/*.uci 2>/dev/null | tail -n +{keep + 1} | while read -r f; do rm -f \"$f\" \"$f.sha256\"; done || true",
                f"echo {_sh(filename)}",
            ]
        )
        result = self.run(script, timeout=30, check=True)
        backup_id = result.stdout.strip().splitlines()[-1] if result.stdout.strip() else filename
        info = {
            "id": backup_id,
            "path": path,
            "dir": backup_dir,
            "package": self.profile.package,
            "reason": safe_reason,
            "created_at": timestamp,
        }
        self._last_backup = info
        return info

    def list_backups(self, limit: int = 50) -> list[dict[str, Any]]:
        backup_dir = self.settings.backup_dir
        script = "\n".join(
            [
                f"ls -1t {_sh(backup_dir)}/*.uci 2>/dev/null | head -n {max(1, min(limit, 200))}",
            ]
        )
        result = self.run(script, timeout=20)
        items: list[dict[str, Any]] = []
        for line in result.stdout.splitlines():
            name = line.strip().rsplit("/", 1)[-1]
            if not name.endswith(".uci"):
                continue
            items.append({"id": name, "path": f"{backup_dir}/{name}"})
        return items

    def _restore_backup(self, backup_id: str, *, reload: bool, require_running: bool = True) -> bool:
        if not BACKUP_ID_RE.fullmatch(backup_id):
            raise SafetyError(f"Недопустимый id бэкапа: {backup_id!r}")
        path = f"{self.settings.backup_dir}/{backup_id}"
        pkg = self.profile.package
        script = "\n".join(
            [
                "set -e",
                f"test -f {_sh(path)}",
                f"cp -f {_sh(path)} {_sh('/etc/config/' + pkg)}",
                f"uci commit {pkg}",
            ]
        )
        self.run(script, timeout=30, check=True)
        self.invalidate()
        if reload:
            try:
                self.reload_and_wait(timeout=35, require_running=require_running)
            except Exception as exc:
                log.error("После восстановления бэкапа сервис не поднялся: %s", exc)
                return False
        return True

    def restore_backup(self, backup_id: str) -> dict[str, Any]:
        if self.settings.read_only:
            raise SafetyError("PODKOP_MCP_READ_ONLY=1: восстановление бэкапа запрещено")
        ok = self._restore_backup(backup_id, reload=True)
        return {"ok": ok, "backup_id": backup_id, "package": self.profile.package}

    # ------------------------------------------------------------------ domain helpers
    def domain_field(self, section: Any) -> tuple[str, str]:
        """Определяет реальное поле доменов в секции, не доверяя кешу схемы."""

        if self.profile.schema == "podkop":
            list_type = str(section.value("user_domain_list_type") or "").strip()
            if list_type == "text" or (
                section.value("user_domains") is None and section.value("user_domains_text") is not None
            ):
                return "user_domains", "list"
            if section.value("user_domains") is None and section.value("domain_list") is not None:
                return "domain_list", "list"
            return "user_domains", "list"

        has_destinations = section.value("destinations") is not None
        has_domain = section.value("domain") is not None
        if has_destinations:
            return "destinations", "option_multiline"
        if has_domain:
            if self.profile.schema == "forkop2":
                raise ConfigError(
                    f"Секция {section.name!r}: найден legacy-option domain, но backend Forkop 2.0 "
                    "ожидает destinations; запись в domain была бы проигнорирована"
                )
            return "domain", "option_text"
        if self.profile.schema == "forkop2":
            return "destinations", "option_multiline"
        return "domain", "option_text"

    def read_domains(self, section_name: str) -> dict[str, Any]:
        config = self.config()
        section = config.section(section_name)
        option, storage = self.domain_field(section)
        if option == "user_domains" and storage == "list":
            list_type = str(section.value("user_domain_list_type") or "").strip()
            has_list = section.value("user_domains") is not None
            has_text = section.value("user_domains_text") is not None
            if list_type == "text" or (not has_list and has_text):
                parsed = parse_entries(section.value("user_domains_text"), "option_multiline")
                return {
                    "section": section_name,
                    "option": "user_domains",
                    "storage": "list",
                    "entries": parsed.entries,
                    "invalid": parsed.invalid,
                    "raw": _raw_public(section.value("user_domains_text")),
                }
        raw = section.value(option)
        parsed = parse_entries(raw, storage)
        return {
            "section": section_name,
            "option": option,
            "storage": storage,
            "entries": parsed.entries,
            "invalid": parsed.invalid,
            "raw": _raw_public(raw),
        }


def _raw_public(value: Any) -> Any:
    if isinstance(value, list):
        return list(value)
    if value is None:
        return None
    return str(value)


def _safe_reason(reason: str) -> str:
    normalized = SAFE_REASON_RE.sub("-", reason.strip().lower()).strip("-")
    return (normalized or "manual")[:40]


def _sh(value: str) -> str:
    return "'" + value.replace("'", "'\\''") + "'"


def parse_json_output(text: str, *, source: str = "") -> dict[str, Any]:
    """Парсит JSON, допуская предупреждения перед ним."""

    stripped = text.strip()
    if not stripped:
        raise BackendError(f"{source}: пустой вывод вместо JSON")
    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError:
        start = min(
            (index for index in (stripped.find("{"), stripped.find("[")) if index >= 0),
            default=-1,
        )
        if start < 0:
            raise BackendError(f"{source}: в выводе нет JSON: {stripped[:300]!r}") from None
        try:
            payload = json.loads(stripped[start:])
        except json.JSONDecodeError as exc:
            raise BackendError(f"{source}: некорректный JSON ({exc}): {stripped[:300]!r}") from exc
    if not isinstance(payload, dict):
        raise BackendError(f"{source}: ожидался JSON-объект, получено {type(payload).__name__}")
    return payload
