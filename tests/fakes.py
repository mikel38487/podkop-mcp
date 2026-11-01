"""Фейки роутера для тестов: SSH-сессия, Router, Services, профили.

Модуль не открывает сетевых соединений. ``paramiko`` сюда напрямую не
импортируется: ``SSHResult`` — локальный dataclass с теми же полями. При
этом ``podkop_mcp.router`` (нужен для настоящего ``PlanBuilder``) сам
импортирует ``podkop_mcp.ssh``, так что ``paramiko`` в процесс всё равно
попадает — без подключений и работы с ключами.

Важно: :class:`FakeRouter` — это подмена :class:`podkop_mcp.router.Router`
(тот же интерфейс, который ждут MCP-инструменты), а не подмена
транспорта. Планировщик плана — настоящий :class:`PlanBuilder`, поэтому
diff, предпросмотр и генерация UCI-скрипта проверяются на реальном коде.
"""

from __future__ import annotations

import copy
import json
import re
from collections.abc import Callable, Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from typing import Any

from podkop_mcp.backends import BackendProfile
from podkop_mcp.config import Settings, TargetConfig
from podkop_mcp.domains_core import entry_key, merge_entries, parse_entries, render_value
from podkop_mcp.errors import BackendError, ConfigError, RemoteCommandError, SafetyError
from podkop_mcp.redaction import redact_config, redact_text
from podkop_mcp.router import ChangePlan, PlanBuilder, _safe_reason
from podkop_mcp.uci import UciConfig, parse_path, parse_ubus_uci

# Ответ на ``ubus call uci get`` — основной способ чтения конфига.
UBUS_GET_PREFIX = "ubus call uci get"

# Все действия сервиса, которые разрешает настоящий Router.service_action.
SERVICE_ACTIONS = {"start", "stop", "restart", "reload", "enable", "disable"}

# Разрешённые для чтения каталоги (как в Router.read_file).
READABLE_ROOTS = ("/etc/", "/var/", "/tmp/", "/proc/", "/sys/", "/rom/", "/usr/share/")

# Команды, которые считаются «записью» на роутер.
WRITE_COMMAND_MARKERS = (
    "uci set",
    "uci delete",
    "uci add_list",
    "uci del_list",
    "uci commit",
    "/etc/init.d/",
)

BACKUP_ID_RE = re.compile(r"^\d{8}T\d{6}Z-[a-z0-9-]{1,40}\.uci$")


# --------------------------------------------------------------------------- SSH


@dataclass
class FakeSSHResult:
    """Результат команды с теми же полями, что ``podkop_mcp.ssh.SSHResult``.

    Позиционный порядок: ``stdout``, ``stderr``, ``exit_code``.
    """

    stdout: str = ""
    stderr: str = ""
    exit_code: int = 0
    command: str = ""

    @property
    def ok(self) -> bool:
        return self.exit_code == 0

    def tail(self, limit: int = 1200) -> str:
        text = (self.stderr or self.stdout).strip()
        return text[-limit:]


def ok(stdout: str = "", stderr: str = "") -> FakeSSHResult:
    """Фабрика успешного результата."""

    return FakeSSHResult(stdout=stdout, stderr=stderr, exit_code=0)


def fail(stderr: str = "boom", exit_code: int = 1, stdout: str = "") -> FakeSSHResult:
    """Фабрика неуспешного результата."""

    return FakeSSHResult(stdout=stdout, stderr=stderr, exit_code=exit_code)


def json_ok(payload: Any) -> FakeSSHResult:
    """Успешный результат с JSON в stdout."""

    return ok(json.dumps(payload, ensure_ascii=False))


# Алиасы: короткие имена для сценариев.
REPLY_OK = ok
REPLY_FAIL = fail


@dataclass
class FakeCall:
    """Запись одного вызова фейка."""

    kind: str
    text: str
    timeout: float | None = None
    check: bool = False
    args: list[str] = field(default_factory=list)


class ScriptNotFound(Exception):
    """Скрипт не совпал ни с одним правилом фейка.

    Отдельный тип (не ``KeyError``), чтобы в отчёте pytest было сразу видно,
    что тест не договорился с фейком, а не что-то не нашлось в данных.
    """


class FakeSession:
    """Подмена :class:`podkop_mcp.ssh.SSHSession`.

    Правила проверяются по очереди:

    * ``handlers`` — функции ``handler(text) -> результат | None``;
    * ``responses`` — словарь «подстрока → результат/строка/исключение»;
    * ``queue`` — очередь ответов для сценариев (отдаётся, только если
      последующее правило не сработало);
    * ``default`` — ответ, если ничего не совпало.

    Если ответ не найден, а ``strict=True``, бросается :class:`ScriptNotFound`:
    так тест замечает лишний поход на роутер.
    """

    def __init__(
        self,
        *,
        queue: Iterable[Any] | None = None,
        responses: dict[str, Any] | None = None,
        handlers: Iterable[Callable[[str], Any]] | None = None,
        default: Any = None,
        strict: bool = True,
        connect_error: Exception | None = None,
    ) -> None:
        self.queue: list[Any] = list(queue or [])
        self.responses: dict[str, Any] = dict(responses or {})
        self.handlers: list[Callable[[str], Any]] = list(handlers or [])
        self.default = default
        self.strict = strict
        self.connect_error = connect_error
        self.calls: list[FakeCall] = []

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def coerce(value: Any) -> FakeSSHResult:
        """Приводит значение правила к :class:`FakeSSHResult`."""

        if isinstance(value, FakeSSHResult):
            return value
        if isinstance(value, str):
            return ok(value)
        if isinstance(value, BaseException):
            raise value
        if isinstance(value, dict):
            return json_ok(value)
        raise TypeError(f"Фейк не умеет возвращать объект {type(value).__name__}")

    def _match(self, text: str) -> Any:
        for handler in self.handlers:
            produced = handler(text)
            if produced is not None:
                return produced
        for substring, value in self.responses.items():
            if substring in text:
                return value
        if self.queue:
            return self.queue.pop(0)
        if self.default is not None:
            return self.default
        if self.strict:
            raise ScriptNotFound(text[:200])
        return ok("")

    def _record(self, kind: str, text: str, timeout: float | None, check: bool, args: list[str] | None = None) -> None:
        self.calls.append(FakeCall(kind, text, timeout=timeout, check=check, args=list(args or [])))

    # ------------------------------------------------------------------ API сессии
    def run(self, script: str, *, timeout: float | None = None, check: bool = False) -> FakeSSHResult:
        self._record("run", script, timeout, check)
        if self.connect_error is not None:
            raise self.connect_error
        result = self.coerce(self._match(script))
        if check and not result.ok:
            raise RemoteCommandError(
                f"Команда завершилась с кодом {result.exit_code}: {result.tail()}",
                exit_code=result.exit_code,
                stdout=result.stdout,
                stderr=result.stderr,
            )
        return result

    def run_argv(
        self,
        program: str,
        args: Sequence[str],
        *,
        timeout: float | None = None,
        check: bool = False,
    ) -> FakeSSHResult:
        text = " ".join([program, *(str(arg) for arg in args)])
        self._record("argv", text, timeout, check, [str(arg) for arg in args])
        if self.connect_error is not None:
            raise self.connect_error
        result = self.coerce(self._match(text))
        if check and not result.ok:
            raise RemoteCommandError(
                f"Команда завершилась с кодом {result.exit_code}: {result.tail()}",
                exit_code=result.exit_code,
                stdout=result.stdout,
                stderr=result.stderr,
            )
        return result

    def connect(self) -> None:
        if self.connect_error is not None:
            raise self.connect_error

    def close(self) -> None:
        return None

    # ------------------------------------------------------------------ запросы
    @property
    def scripts(self) -> list[str]:
        """Все переданные скрипты по порядку."""

        return [call.text for call in self.calls]

    @property
    def argv_calls(self) -> list[FakeCall]:
        return [call for call in self.calls if call.kind == "argv"]

    def find(self, *substrings: str) -> list[FakeCall]:
        """Вызовы, в которых встречаются все подстроки."""

        return [call for call in self.calls if all(item in call.text for item in substrings)]

    def find_argv(self, command: str) -> list[FakeCall]:
        """Вызовы CLI-команды (``forkop get_status`` и т.п.)."""

        pattern = re.compile(rf"^{re.escape(command)}(\s|$)")
        return [call for call in self.argv_calls if pattern.search(call.text)]

    def contains(self, *substrings: str) -> bool:
        return bool(self.find(*substrings))

    def write_calls(self) -> list[FakeCall]:
        """Вызовы, которые меняют состояние роутера."""

        return [call for call in self.calls if any(marker in call.text for marker in WRITE_COMMAND_MARKERS)]

    def reset(self) -> None:
        self.calls.clear()


# --------------------------------------------------------------------------- профили


def make_forkop1_profile(**overrides: Any) -> BackendProfile:
    """Forkop 1.0.5: домены — строка ``section.domain`` через пробел."""

    params: dict[str, Any] = {
        "kind": "forkop",
        "version": "1.0.5",
        "config_version": "1.0.5",
        "schema": "forkop1",
        "package": "forkop",
        "init_script": "/etc/init.d/forkop",
        "cli": "forkop",
        "commands": {
            "get_status",
            "get_system_info",
            "check_sing_box",
            "check_nft",
            "check_fakeip",
            "global_check",
            "show_config",
            "check_logs",
            "clash_api",
            "reload",
        },
        "clash_mode": "actions",
    }
    params.update(overrides)
    return BackendProfile(**params)


def make_forkop2_profile(**overrides: Any) -> BackendProfile:
    """Forkop 2.0: домены — ``section.destinations`` построчно, Clash по HTTP."""

    params: dict[str, Any] = {
        "kind": "forkop",
        "version": "2.0.0",
        "config_version": "2.0.0",
        "schema": "forkop2",
        "package": "forkop",
        "init_script": "/etc/init.d/forkop",
        "cli": "forkop",
        "commands": {
            "get_status",
            "get_system_info",
            "clash_summary",
            "report",
            "check_sing_box",
            "show_config",
            "list_update",
            "subscription_update",
        },
        "clash_mode": "http",
    }
    params.update(overrides)
    return BackendProfile(**params)


def make_podkop_profile(**overrides: Any) -> BackendProfile:
    """Podkop: домены — UCI-список ``section.user_domains``."""

    params: dict[str, Any] = {
        "kind": "podkop",
        "version": "0.7.5",
        "config_version": "0.7.5",
        "schema": "podkop",
        "package": "podkop",
        "init_script": "/etc/init.d/podkop",
        "cli": "podkop",
        "commands": {"get_status", "get_system_info", "check_sing_box", "show_config"},
        "clash_mode": "actions",
    }
    params.update(overrides)
    return BackendProfile(**params)


# --------------------------------------------------------------------------- Router


class FakeRouter:
    """Подмена :class:`podkop_mcp.router.Router` без SSH.

    ``builder()`` возвращает настоящий :class:`PlanBuilder`, ``apply_plan()``
    запоминает план и возвращает «успешный» ответ, ``run()``/``cli()`` идут
    в :class:`FakeSession`. Запреты безопасности повторяют реальный Router:
    read-only блокирует запись, бэкап и восстановление; ``service_action``
    и ``read_file`` валидируют свои аргументы.
    """

    def __init__(
        self,
        *,
        profile: BackendProfile | None = None,
        config: UciConfig | dict[str, Any] | Callable[[], UciConfig] | None = None,
        settings: Settings | None = None,
        session: FakeSession | None = None,
        name: str = "fake-router",
        backups: list[dict[str, Any]] | None = None,
    ) -> None:
        self._profile = profile or make_forkop1_profile()
        self.settings = settings or Settings()
        self.session = session if session is not None else FakeSession(strict=False)
        self.name = name
        self._config_source = config
        self._backups: list[dict[str, Any]] = list(backups or [])
        self._config_cache: dict[str, UciConfig] = {}
        self.applied: list[ChangePlan] = []
        self.plans: list[ChangePlan] = []
        self.run_calls: list[FakeCall] = []
        self.invalidated = 0
        self.health: dict[str, Any] = {"sing_box": {"ok": True}, "status": {"running": 1}}
        self.apply_result: dict[str, Any] | None = None
        self.target = TargetConfig(name=name, host="127.0.0.1", port=1)

    # ------------------------------------------------------------------ свойства
    @property
    def profile(self) -> BackendProfile:
        return self._profile

    @profile.setter
    def profile(self, value: BackendProfile) -> None:
        self._profile = value

    # ------------------------------------------------------------------ конфиг
    def config(self, package: str | None = None, *, refresh: bool = False) -> UciConfig:
        """Отдаёт копию конфига: правки теста не должны протекать в кэш."""

        _ = package or self.profile.package
        cached = self._config_cache.get("current")
        if cached is None or refresh:
            cached = self._build_config()
            self._config_cache["current"] = cached
        return copy.deepcopy(cached)

    def _build_config(self) -> UciConfig:
        source = self._config_source
        if source is None:
            return UciConfig(package=self.profile.package)
        if callable(source):
            return source()
        if isinstance(source, UciConfig):
            return copy.deepcopy(source)
        payload = copy.deepcopy(source)
        values = payload.get("values", payload)
        return parse_ubus_uci({"values": values}, self.profile.package)

    def set_config(self, config: UciConfig | dict[str, Any]) -> None:
        """Подменяет конфиг (после «записи» на роутер)."""

        self._config_source = config
        self._config_cache.clear()

    def config_public(self, *, reveal_secrets: bool = False, package: str | None = None) -> dict[str, Any]:
        data = self.config(package).to_public_dict()
        if not reveal_secrets:
            data["sections"] = redact_config(data["sections"])
        return data

    def uci_export(self, *, mode: str = "masked") -> str:
        lines: list[str] = []
        for section in self.config().sections.values():
            lines.append(f"config {section.type} '{section.name}'")
            for key, value in section.options.items():
                if isinstance(value, list):
                    lines.extend(f"\tlist {key} '{item}'" for item in value)
                else:
                    lines.append(f"\toption {key} '{value}'")
        text = "\n".join(lines)
        return text if mode == "raw" else redact_text(text)

    def read_file(self, path: str, *, max_bytes: int | None = None) -> str:
        """Повторяет проверки Router.read_file: абсолютный путь из белого списка."""

        _ = max_bytes
        if not path.startswith("/") or ".." in path:
            raise SafetyError(f"Чтение разрешено только по абсолютному пути без '..': {path!r}")
        if not path.startswith(READABLE_ROOTS):
            raise SafetyError(f"Путь {path!r} вне разрешённых каталогов {READABLE_ROOTS}")
        result = self.run(f"if [ -f '{path}' ]; then head -c 1000 '{path}'; else echo '__NO_FILE__'; fi")
        if result.stdout.strip() == "__NO_FILE__":
            raise ConfigError(f"Файл не найден: {path}")
        return result.stdout

    # ------------------------------------------------------------------ план и запись
    def builder(self, reason: str, package: str | None = None) -> PlanBuilder:
        # Как и Router.builder — reason санитизируется для имени бэкапа.
        return PlanBuilder(self, package or self.profile.package, _safe_reason(reason))

    def apply_plan(self, plan: ChangePlan, *, wait: bool = True, timeout: float = 35.0) -> dict[str, Any]:
        _ = wait, timeout
        if self.settings.read_only:
            raise SafetyError("PODKOP_MCP_READ_ONLY=1: запись на роутер запрещена")
        if not plan.ops:
            raise ConfigError("Нечего применять: план пуст")
        self.plans.append(plan)
        self.applied.append(plan)
        self.invalidated += 1
        if self.apply_result is not None:
            return dict(self.apply_result)
        return {
            "ok": True,
            "target": self.name,
            "package": plan.package,
            "reason": plan.reason,
            "backup_id": "20260101T000000Z-fake.uci",
            "changes": [change.to_dict() for change in plan.changes],
            "service": {"action": "reload", "running": 1},
            "health": dict(self.health),
            "warnings": list(plan.warnings),
        }

    def backup(self, reason: str = "manual") -> dict[str, Any]:
        if self.settings.read_only:
            raise SafetyError("PODKOP_MCP_READ_ONLY=1: создание бэкапа на роутере запрещено")
        if not self.settings.backup_dir.startswith("/") or ".." in self.settings.backup_dir:
            raise SafetyError(f"Недопустимый каталог бэкапов: {self.settings.backup_dir!r}")
        safe_reason = _safe_reason(reason)
        info = {
            "id": f"20260101T000000Z-{safe_reason}.uci",
            "path": f"{self.settings.backup_dir}/20260101T000000Z-{safe_reason}.uci",
            "dir": self.settings.backup_dir,
            "package": self.profile.package,
            "reason": safe_reason,
        }
        self._backups.insert(0, info)
        return info

    def list_backups(self, limit: int = 50) -> list[dict[str, Any]]:
        return self._backups[: max(1, min(limit, 200))]

    def restore_backup(self, backup_id: str) -> dict[str, Any]:
        if self.settings.read_only:
            raise SafetyError("PODKOP_MCP_READ_ONLY=1: восстановление бэкапа запрещено")
        if not BACKUP_ID_RE.fullmatch(backup_id):
            raise SafetyError(f"Недопустимый id бэкапа: {backup_id!r}")
        return {"ok": True, "backup_id": backup_id, "package": self.profile.package}

    def service_action(self, action: str, *, timeout: float = 45.0) -> dict[str, Any]:
        _ = timeout
        if action not in SERVICE_ACTIONS:
            raise SafetyError(f"Недопустимое действие сервиса: {action!r}")
        if self.settings.read_only:
            raise SafetyError("PODKOP_MCP_READ_ONLY=1: управление сервисом запрещено")
        return {"action": action, "exit_code": 0, "output": "", "status": {"running": 1}}

    def quick_health(self) -> dict[str, Any]:
        return dict(self.health)

    def invalidate(self) -> None:
        self._config_cache.clear()
        self.invalidated += 1

    # ------------------------------------------------------------------ домены
    def read_domains(self, section_name: str) -> dict[str, Any]:
        section = self.config().section(section_name)
        if self.profile.schema == "podkop":
            list_type = str(section.value("user_domain_list_type") or "").strip()
            if list_type == "text" or (
                section.value("user_domains") is None and section.value("user_domains_text") is not None
            ):
                parsed = parse_entries(section.value("user_domains_text"), "option_multiline")
                return {
                    "section": section_name,
                    "option": "user_domains",
                    "storage": "list",
                    "entries": parsed.entries,
                    "invalid": parsed.invalid,
                    "raw": copy.deepcopy(section.value("user_domains_text")),
                }
            option = "domain_list" if section.value("user_domains") is None and section.value("domain_list") is not None else "user_domains"
            storage = "list"
        elif section.value("destinations") is not None:
            option, storage = "destinations", "option_multiline"
        elif section.value("domain") is not None:
            if self.profile.schema == "forkop2":
                raise ConfigError("legacy domain для forkop2")
            option, storage = "domain", "option_text"
        elif self.profile.schema == "forkop2":
            option, storage = "destinations", "option_multiline"
        else:
            option, storage = "domain", "option_text"
        raw = section.value(option)
        parsed = parse_entries(raw, storage)
        return {
            "section": section_name,
            "option": option,
            "storage": storage,
            "entries": parsed.entries,
            "invalid": parsed.invalid,
            "raw": copy.deepcopy(raw),
        }

    def merge_domains(self, section_name: str, additions: list[str]) -> dict[str, Any]:
        """Применяет добавление доменов через планировщик (для тестов домены)."""

        section = self.config().section(section_name)
        current = self.read_domains(section_name)
        merged, added, duplicates, invalid = merge_entries(current["entries"], additions)
        builder = self.builder("domains-add")
        option = current["option"]
        if current["storage"] == "list":
            builder.set_list(section_name, option, merged)
        else:
            rendered = render_value(merged, current["storage"], original=section.value(option))
            builder.set_option(section_name, option, rendered)
        return {"plan": builder.build(), "added": added, "duplicates": duplicates, "invalid": invalid}

    # ------------------------------------------------------------------ прочее
    def run(self, script: str, *, timeout: float | None = None, check: bool = False) -> FakeSSHResult:
        self.run_calls.append(FakeCall("run", script, timeout=timeout, check=check))
        return self.session.run(script, timeout=timeout, check=check)

    def cli(self, *args: str, timeout: float | None = None, check: bool = False) -> FakeSSHResult:
        return self.session.run_argv(self.profile.cli, list(args), timeout=timeout, check=check)

    def cli_json(self, *args: str, timeout: float | None = None) -> dict[str, Any]:
        result = self.cli(*args, timeout=timeout)
        source = f"{self.profile.cli} {' '.join(args)}"
        if not result.ok:
            raise RemoteCommandError(
                f"{source} завершился с кодом {result.exit_code}: {result.tail()}",
                exit_code=result.exit_code,
                stdout=result.stdout,
                stderr=result.stderr,
            )
        stripped = result.stdout.strip()
        if not stripped:
            raise BackendError(f"{source}: пустой вывод вместо JSON")
        try:
            payload = json.loads(stripped)
        except json.JSONDecodeError as exc:
            raise BackendError(f"{source}: некорректный JSON ({exc}): {stripped[:300]!r}") from exc
        if not isinstance(payload, dict):
            raise BackendError(f"{source}: ожидался JSON-объект")
        return payload

    def service_cli(self, *args: str, timeout: float | None = None) -> FakeSSHResult:
        return self.session.run_argv(self.profile.cli, list(args), timeout=timeout)

    def __repr__(self) -> str:  # pragma: no cover - отладка
        return f"FakeRouter(name={self.name!r}, schema={self.profile.schema!r})"


# --------------------------------------------------------------------------- Services


class FakeServices:
    """Подмена :class:`podkop_mcp.services.Services` без SSHManager."""

    def __init__(
        self,
        routers: dict[str, FakeRouter] | None = None,
        *,
        settings: Settings | None = None,
        doctor: Callable[[str | None], dict[str, Any]] | None = None,
    ) -> None:
        if routers is None:
            routers = {"router": FakeRouter()}
        self._routers: dict[str, FakeRouter] = dict(routers)
        if settings is not None:
            self.settings = settings
        elif self._routers:
            self.settings = next(iter(self._routers.values())).settings
        else:
            self.settings = Settings()
        self._doctor = doctor
        self.closed = False

    def target(self, name: str | None = None) -> TargetConfig:
        return self._resolve(name).target

    def router(self, name: str | None = None) -> FakeRouter:
        return self._resolve(name)

    def _resolve(self, name: str | None) -> FakeRouter:
        if not self._routers:
            raise ConfigError("Роутер не настроен.")
        key = name or self.settings.default_target or next(iter(self._routers))
        if key not in self._routers:
            known = ", ".join(sorted(self._routers))
            raise ConfigError(f"Неизвестный target {key!r}. Доступные: {known}")
        return self._routers[key]

    def require_targets(self) -> None:
        if not self._routers:
            raise ConfigError("Роутер не настроен. Задайте PODKOP_MCP_SSH_HOST.")

    def doctor(self, name: str | None = None) -> dict[str, Any]:
        if self._doctor is not None:
            return self._doctor(name)
        router = self._resolve(name)
        return {
            "ok": True,
            "target": router.name,
            "backend": router.profile.public_dict(),
            "read_only": self.settings.read_only,
        }

    def close(self) -> None:
        self.closed = True


# --------------------------------------------------------------------------- данные


def config_from_fixture(payload: dict[str, Any], package: str = "forkop") -> UciConfig:
    """Собирает :class:`UciConfig` из санитизированного ответа ubus."""

    values = payload.get("values", payload)
    return parse_ubus_uci({"values": values}, package)


def entries_of(value: Any, storage: str = "option_text") -> list[str]:
    """Разбирает домены из значения (для проверок)."""

    return parse_entries(value, storage).entries  # type: ignore[arg-type]


def keys(values: Iterable[str]) -> list[str]:
    """Ключи сравнения доменов — удобно для assert-ов."""

    return [entry_key(item) for item in values]


def iter_dict_paths(data: Any, prefix: str = "") -> Iterator[tuple[str, Any]]:
    """Обходит вложенные dict/list, отдавая пары (путь, лист)."""

    if isinstance(data, dict):
        for key, value in data.items():
            yield from iter_dict_paths(value, f"{prefix}.{key}" if prefix else str(key))
    elif isinstance(data, list):
        for index, value in enumerate(data):
            yield from iter_dict_paths(value, f"{prefix}[{index}]")
    else:
        yield prefix, data


def plan_paths(plan: ChangePlan) -> list[str]:
    """Пути изменений плана — удобно для проверок diff."""

    return [change.path for change in plan.changes]


def plan_ops(plan: ChangePlan) -> list[tuple[str, str, Any]]:
    """``(op, path, value)`` каждого UCI-действия плана."""

    return [(op.op, op.path, op.value) for op in plan.ops]


def assert_uci_path(value: str) -> tuple[str, str, str | None]:
    """Проверяет, что строка является валидным UCI-путём, и разбирает её."""

    return parse_path(value)
