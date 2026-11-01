"""Общие помощники MCP-инструментов."""

from __future__ import annotations

import functools
import logging
import shlex
from collections.abc import Callable
from typing import Any, TypeVar

from mcp.server.mcpserver.exceptions import ToolError

from ..errors import ConfigError, PodkopError
from ..router import ChangePlan, Router
from ..services import Services

log = logging.getLogger(__name__)
F = TypeVar("F", bound=Callable[..., Any])


def guard(fn: F) -> F:
    """Преобразует доменные ошибки в ``ToolError`` с понятным текстом."""

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return fn(*args, **kwargs)
        except ToolError:
            raise
        except (PodkopError, ConfigError) as exc:
            raise ToolError(str(exc)) from exc
        except ValueError as exc:
            # Пользовательская ошибка валидации, а не сбой сервера.
            raise ToolError(str(exc)) from exc
        except Exception as exc:  # граница MCP-инструмента
            log.exception("Неожиданная ошибка в инструменте %s", fn.__name__)
            raise ToolError(f"Внутренняя ошибка {type(exc).__name__}: {exc}") from exc

    return wrapper  # type: ignore[return-value]


def router_for(services: Services, target: str | None) -> Router:
    services.require_targets()
    return services.router(target)


def preview_or_apply(router: Router, plan: ChangePlan, apply: bool) -> dict[str, Any]:
    """Единый ответ для всех изменяющих инструментов."""

    if not apply:
        return {
            "applied": False,
            "dry_run": True,
            "hint": "Изменения не применены. Повторите вызов с apply=true, чтобы записать конфиг.",
            **plan.to_dict(),
        }
    result = router.apply_plan(plan)
    return {"applied": True, "dry_run": False, **result}


def shquote(value: str) -> str:
    return shlex.quote(value)


def parse_optional_json(value: Any) -> Any:
    return value


def section_enabled(section: dict[str, Any]) -> bool:
    return str(section.get("enabled", "0")).strip() in {"1", "true", "yes", "on"}
