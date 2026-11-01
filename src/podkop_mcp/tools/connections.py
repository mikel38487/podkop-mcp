"""Инструменты: соединения-конфиг, подписки, прокси Clash API.

Чистые helper-функции (_resolve_subscription, _plan_subscription_add и т.п.)
тестируются без SSH; сами инструменты — тонкие обёртки над роутером.
"""

from __future__ import annotations

import json
import time
from typing import Any

from mcp.server import MCPServer

from ..clash import Clash
from ..errors import BackendError, ConfigError, SafetyError
from ..redaction import mask_value, redact_text
from ..services import Services
from ..uci import UciSection, parse_path
from .common import guard, preview_or_apply, router_for

MAX_URL_LENGTH = 4096
SUBSCRIPTION_POLL_INTERVAL = 2.0
SUBSCRIPTION_CHILD_TYPE = "subscription_url"


def _validate_subscription_url(url: str) -> str:
    """Проверяет URL/путь подписки; возвращает нормализованное значение."""

    value = (url or "").strip()
    if not value:
        raise SafetyError("URL подписки пуст")
    if len(value) > MAX_URL_LENGTH:
        raise SafetyError(f"URL подписки длиннее {MAX_URL_LENGTH} символов")
    lowered = value.lower()
    if lowered.startswith(("http://", "https://")):
        return value
    if value.startswith("/") and ".." not in value:
        # Локальный путь на роутере.
        return value
    raise SafetyError(
        "URL подписки должен начинаться с http(s):// либо быть локальным абсолютным путём (/...)"
    )


def _mask(value: Any, key: str, reveal: bool) -> Any:
    """Маскирует значение опции, если секреты скрыты."""

    if reveal:
        return value
    return mask_value(key, value)


def _summarize_section(config: Any, name: str, section: UciSection, *, reveal: bool) -> dict[str, Any]:
    """Собирает сводку одной секции: подписки, urltest, ссылки, туннели."""

    children = config.children(SUBSCRIPTION_CHILD_TYPE, name)
    subscriptions: list[dict[str, Any]] = []
    for index, child in enumerate(children, start=1):
        subscriptions.append(
            {
                "index": index,
                "name": child.name,
                "section": str(child.value("section") or ""),
                "url": _mask(child.value("url"), "url", reveal),
                "tag": child.value("tag") or "",
                "interval": child.value("subscription_update_interval") or child.value("interval") or "",
            }
        )
    urltest: list[dict[str, Any]] = []
    for child in config.children("urltest", name):
        data = child.to_dict(include_meta=False)
        if not reveal:
            # Опции urltest могут содержать секретные поля (url/token) — маскируем.
            data = {key: mask_value(key, value) for key, value in data.items()}
        urltest.append({"name": child.name, "tag": child.value("tag") or "", "options": data})
    links: list[str] = []
    for child_type in ("connection_link", "link"):
        for child in config.children(child_type, name):
            links.append(child.name)
    tunnels: list[str] = []
    for child in config.children("tunnel", name):
        tunnels.append(child.name)
    extra_children: dict[str, list[dict[str, Any]]] = {}
    for child_type in ("tunnel_peer", "interface", "json", "fallback_group", "section_interface"):
        items: list[dict[str, Any]] = []
        for child in config.children(child_type, name):
            data = child.to_dict(include_meta=False)
            if not reveal:
                data = {key: mask_value(key, value) for key, value in data.items()}
            items.append({"name": child.name, "options": data})
        if items:
            extra_children[child_type] = items
    return {
        "name": name,
        "action": section.value("action") or section.value("connection_type") or "",
        "enabled": str(section.value("enabled", "0")).strip() in {"1", "true", "yes", "on"},
        "subscriptions": subscriptions,
        "urltest": urltest,
        "links": links,
        "tunnels": tunnels,
        "children": extra_children,
    }


def _collect_sections(router: Any, *, reveal: bool) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Все топ-уровневые секции с их подключениями."""

    config = router.config()
    profile = router.profile
    sections: list[dict[str, Any]] = []
    subscription_count = 0
    urltest_count = 0
    for name, section in config.sections.items():
        if section.type != "section":
            continue
        summary = _summarize_section(config, name, section, reveal=reveal)
        if profile.schema == "podkop":
            # Podkop: подписка хранится прямо в секции (inline-опции).
            sub_url = section.value("subscription_url")
            if sub_url:
                summary["subscriptions"].append(
                    {
                        "index": len(summary["subscriptions"]) + 1,
                        "inline": True,
                        "name": name,
                        "section": name,
                        "url": _mask(sub_url, "subscription_url", reveal),
                        "tag": "",
                        "interval": section.value("subscription_update_interval") or "",
                    }
                )
            summary["proxy_config_type"] = section.value("proxy_config_type") or ""
            summary["proxy_string"] = (
                _mask(section.value("proxy_string"), "proxy_string", reveal) if section.value("proxy_string") else ""
            )
        sections.append(summary)
        subscription_count += len(summary["subscriptions"])
        urltest_count += len(summary["urltest"])
    counts = {
        "sections": len(sections),
        "subscriptions": subscription_count,
        "urltest": urltest_count,
    }
    return sections, counts


def _find_subscription_children(
    config: Any,
    identifier: str,
    section: str | None,
) -> list[UciSection]:
    """Ищет child subscription_url по имени, индексу, tag или url.

    section ограничивает поиск детьми конкретной секции. Индекс — 1-based
    в пределах выбранной области поиска.
    """

    children = config.children(SUBSCRIPTION_CHILD_TYPE, section) if section else config.sections_of_type(SUBSCRIPTION_CHILD_TYPE)
    if not children:
        return []
    # Точное имя
    by_name = [child for child in children if child.name == identifier]
    if by_name:
        return by_name
    # Индекс (1-based)
    if identifier.isdigit():
        index = int(identifier)
        if 1 <= index <= len(children):
            return [children[index - 1]]
    needle = identifier.casefold()
    by_tag = [child for child in children if str(child.value("tag") or "").casefold() == needle]
    if by_tag:
        return by_tag
    by_url = [child for child in children if str(child.value("url") or "") == identifier]
    if by_url:
        return by_url
    return []


def _next_free_child_name(config: Any, section: str) -> str:
    """Первое свободное имя вида {section}_sub_{n}, начиная с 1."""

    n = 1
    existing = {child.name for child in config.sections_of_type(SUBSCRIPTION_CHILD_TYPE)}
    while f"{section}_sub_{n}" in existing:
        n += 1
    return f"{section}_sub_{n}"


def _plan_subscription_add(
    router: Any,
    section: str,
    url: str,
    options: dict[str, Any] | None,
) -> Any:
    """Строит план добавления подписки с учётом схемы бэкенда."""

    config = router.config()
    profile = router.profile
    config.section(section)  # проверка существования секции
    builder = router.builder("subscription-add")
    if profile.schema == "podkop":
        # Podkop: подписка — inline-опции самой секции.
        existing_url = config.section(section).value("subscription_url")
        if existing_url and existing_url != url:
            # Set-семантика: существующий URL будет перезаписан — предупреждаем,
            # чтобы apply=true не привёл к молчаливой потере подписки.
            builder.warnings.append(
                f"У секции {section!r} уже есть subscription_url — он будет ПЕРЕЗАПИСАН "
                f"(старое значение: {mask_value('subscription_url', existing_url)})."
            )
        builder.set_option(section, "subscription_url", url)
        builder.set_option(section, "subscription_update_enabled", "1")
        for key, value in (options or {}).items():
            _apply_option(builder, section, key, value)
        return builder
    # Forkop 1.x/2.x: отдельная child-секция subscription_url.
    parse_path(f"{profile.package}.{section}.enabled")  # валидация имени секции
    child_name = _next_free_child_name(config, section)
    builder.create_section(child_name, SUBSCRIPTION_CHILD_TYPE)
    builder.set_option(child_name, "section", section)
    builder.set_option(child_name, "tag", child_name)
    builder.set_option(child_name, "url", url)
    builder.set_option(child_name, "subscription_update_enabled", "1")
    builder.set_option(child_name, "subscription_update_interval", "1h")
    builder.set_option(child_name, "auto_user_agent", "1")
    builder.set_option(child_name, "auto_hwid", "1")
    builder.set_option(child_name, "include_urltest_groups", "1")
    for key, value in (options or {}).items():
        _apply_option(builder, child_name, key, value)
    return builder


def _apply_option(builder: Any, section: str, key: str, value: Any) -> None:
    """Записывает одну опцию с учётом типа значения."""

    if isinstance(value, bool):
        builder.set_option(section, key, "1" if value else "0")
    elif isinstance(value, list):
        builder.set_list(section, key, [str(item) for item in value])
    elif value is None:
        builder.delete_option(section, key)
    else:
        builder.set_option(section, key, str(value))


def _plan_subscription_delete(router: Any, identifier: str, section: str | None) -> tuple[Any, list[str]]:
    """Строит план удаления подписки; возвращает builder и найденные имена."""

    config = router.config()
    profile = router.profile
    matches = _find_subscription_children(config, identifier, section)
    if section:
        config.section(section)  # проверка существования секции
    builder = router.builder("subscription-delete")
    removed: list[str] = []
    if profile.schema == "podkop":
        # Podkop: подписка — опция секции; identifier трактуем как имя секции
        # (или section, если он явно задан параметром).
        target_section = section or identifier
        section_obj = config.section(target_section)
        if not section_obj.value("subscription_url"):
            raise BackendError(f"У секции {target_section!r} нет subscription_url")
        builder.delete_option(target_section, "subscription_url")
        builder.delete_option(target_section, "subscription_update_enabled")
        removed.append(f"{target_section}.subscription_url")
        return builder, removed
    if not matches:
        raise BackendError(
            f"Подписка {identifier!r} не найдена (section={section or 'любая'}). "
            "Искать можно по имени, индексу, tag или url."
        )
    for child in matches:
        builder.delete_section(child.name)
        removed.append(child.name)
    return builder, removed


def register(mcp: MCPServer, services: Services) -> None:
    @mcp.tool()
    @guard
    def podkop_connections_config(
        target: str | None = None,
        section: str | None = None,
        reveal_secrets: bool = False,
    ) -> dict[str, Any]:
        """List routing sections with subscriptions, urltest groups, links and tunnels.

        Subscription URLs and proxy strings are masked unless
        reveal_secrets=true. This is a read-only map of how connections are
        configured; use podkop_subscription_* to modify them.
        """

        router = router_for(services, target)
        sections, counts = _collect_sections(router, reveal=reveal_secrets)
        if section:
            sections = [item for item in sections if item["name"] == section]
            if not sections:
                raise ConfigError(f"Секция {section!r} не найдена")
        return {"target": router.name, "sections": sections, "counts": counts}

    @mcp.tool()
    @guard
    def podkop_subscription_add(
        section: str,
        url: str,
        options: dict[str, Any] | None = None,
        target: str | None = None,
        apply: bool = False,
    ) -> dict[str, Any]:
        """Add a subscription to a section. Dry-run by default; apply=true writes.

        Accepts http(s):// URLs or local absolute paths (max 4096 chars).
        Forkop creates a child section {section}_sub_N with sensible defaults;
        Podkop writes subscription_url inline into the section.
        """

        router = router_for(services, target)
        normalized = _validate_subscription_url(url)
        builder = _plan_subscription_add(router, section, normalized, options)
        return preview_or_apply(router, builder.build(), apply)

    @mcp.tool()
    @guard
    def podkop_subscription_delete(
        identifier: str,
        section: str | None = None,
        target: str | None = None,
        apply: bool = False,
    ) -> dict[str, Any]:
        """Delete a subscription by name, 1-based index, tag or raw URL.

        When ``section`` is given, only its children are considered. Podkop
        removes the subscription_url option instead. Dry-run by default.
        """

        router = router_for(services, target)
        builder, removed = _plan_subscription_delete(router, identifier, section)
        result = preview_or_apply(router, builder.build(), apply)
        result["removed"] = removed
        return result

    @mcp.tool()
    @guard
    def podkop_subscription_update(
        section: str | None = None,
        target: str | None = None,
        apply: bool = False,
        wait: bool = True,
        timeout: float = 180.0,
    ) -> dict[str, Any]:
        """Refresh subscription cache on the router (apply must be true).

        Uses async update with status polling when the backend supports it,
        otherwise runs the blocking subscription_update command. Bounded by
        ``timeout`` seconds.
        """

        router = router_for(services, target)
        profile = router.profile
        has_async = profile.supports("subscription_update_async") and profile.supports("subscription_update_status")
        has_sync = profile.supports("subscription_update")
        if has_async:
            mode = "async"
        elif has_sync:
            mode = "sync"
        elif profile.clash_mode == "http":
            mode = "providers"
        else:
            raise BackendError(
                f"{profile.display_name} не поддерживает subscription_update "
                "(нет ни sync-команды, ни пары subscription_update_async/status)"
            )
        if not apply:
            return {
                "target": router.name,
                "section": section,
                "applied": False,
                "dry_run": True,
                "mode": mode,
                "would_update": ["subscription cache"],
                "hint": "Повторите с apply=true, чтобы обновить кеш подписки на роутере.",
            }
        if router.settings.read_only:
            raise SafetyError("PODKOP_MCP_READ_ONLY=1: обновление подписок запрещено")
        if mode == "providers":
            # Forkop 2.x обновляет подписки как sing-box proxy-providers.
            clash = Clash(router)
            result = clash.refresh_providers()
            return {
                "target": router.name,
                "mode": "providers",
                "section": section,
                "applied": True,
                "ok": True,
                **result,
            }
        if wait and has_async:
            return _run_async_subscription_update(router, section, timeout)
        result = router.cli(
            "subscription_update",
            *( [section] if section else [] ),
            timeout=max(30.0, min(timeout, 600.0)),
        )
        if getattr(result, "ok", result.exit_code == 0) is False:
            # Упавшее обновление не маскируем под успех.
            raise BackendError(
                f"subscription_update завершился с кодом {result.exit_code}: {(result.stderr or result.stdout or '')[-4000:]}"
            )
        return {
            "target": router.name,
            "mode": "sync",
            "section": section,
            "ok": True,
            "exit_code": result.exit_code,
            "stdout": redact_text((result.stdout or "")[-12000:]),
            "stderr": redact_text((result.stderr or "")[-4000:]),
        }

    @mcp.tool()
    @guard
    def podkop_proxies_list(target: str | None = None, group: str | None = None) -> dict[str, Any]:
        """List Clash proxy groups and nodes with delays.

        With ``group``, returns that group's members (all) and its current
        selection (now). Read-only.
        """

        router = router_for(services, target)
        clash = Clash(router)
        summary = clash.proxies_summary()
        if group:
            for item in summary.get("groups", []):
                if item.get("name") == group:
                    return {
                        "target": router.name,
                        "group": item,
                        "all": item.get("all", []),
                        "now": item.get("now"),
                    }
            raise ConfigError(f"Группа {group!r} не найдена")
        return {
            "target": router.name,
            "groups": summary.get("groups", []),
            "nodes": summary.get("nodes", []),
            "group_count": summary.get("group_count", 0),
            "node_count": summary.get("node_count", 0),
        }

    @mcp.tool()
    @guard
    def podkop_proxy_select(
        group: str,
        proxy: str,
        target: str | None = None,
        apply: bool = False,
    ) -> dict[str, Any]:
        """Select a proxy inside a Clash group (runtime change, not config).

        Dry-run shows what would be selected. apply=true performs the switch;
        on Forkop 1.x this raises BackendError because runtime selection is
        not supported there — that is expected behaviour.
        """

        router = router_for(services, target)
        clash = Clash(router)
        if not apply:
            summary = clash.proxies_summary()
            found = next((item for item in summary.get("groups", []) if item.get("name") == group), None)
            if found is None:
                raise ConfigError(f"Группа {group!r} не найдена")
            return {
                "applied": False,
                "dry_run": True,
                "hint": "Повторите с apply=true, чтобы переключить прокси.",
                "group": group,
                "proxy": proxy,
                "current": found.get("now"),
                "members": found.get("all", []),
            }
        return {"applied": True, "target": router.name, **clash.select_proxy(group, proxy)}

    @mcp.tool()
    @guard
    def podkop_proxy_test(target: str | None = None) -> dict[str, Any]:
        """Run read-only proxy check and report selected-group delays.

        Calls check_proxy via CLI when available; falls back to reporting
        delays of currently selected proxies from Clash API history.
        """

        router = router_for(services, target)
        profile = router.profile
        check: dict[str, Any] = {}
        if profile.supports("check_proxy"):
            result = router.cli("check_proxy", timeout=60)
            check = {
                "exit_code": result.exit_code,
                # Podkop 0.7.x может вывести в stdout весь sing-box config;
                # для ответа достаточно хвоста, полный вывод — через podkop_logs.
                "stdout": redact_text((result.stdout or "")[-2000:]),
                "stderr": redact_text((result.stderr or "")[-2000:]),
            }
        else:
            check = {"skipped": True, "reason": f"{profile.display_name} не поддерживает check_proxy"}
        summary = Clash(router).proxies_summary()
        selected = [
            {"group": item.get("name"), "now": item.get("now"), "delay": item.get("delay")}
            for item in summary.get("selected", [])
        ]
        return {
            "target": router.name,
            "check_proxy": check,
            "selected": selected,
        }


# Состояния async-обновления, означающие завершение (успех или ошибку).
SUBSCRIPTION_TERMINAL_STATES = {"done", "finished", "ok", "success", "complete", "completed", "error", "failed"}


def _parse_status_output(text: str) -> dict[str, Any]:
    """Разбирает stdout subscription_update_status: JSON (возможно с мусором
    до него) или произвольный текст."""

    stripped = (text or "").strip()
    if not stripped:
        return {}
    start = stripped.find("{")
    if start >= 0:
        try:
            payload = json.loads(stripped[start:])
            if isinstance(payload, dict):
                return payload
        except json.JSONDecodeError:
            pass
    return {"text": stripped[-2000:]}


def _run_async_subscription_update(router: Any, section: str | None, timeout: float) -> dict[str, Any]:
    """Запускает async-обновление подписки и опрашивает статус."""

    start_result = router.cli(
        "subscription_update_async",
        *( [section] if section else [] ),
        timeout=30,
    )
    if getattr(start_result, "ok", start_result.exit_code == 0) is False:
        raise BackendError(
            f"Не удалось запустить subscription_update_async (код {start_result.exit_code}): "
            f"{redact_text((start_result.stderr or start_result.stdout or '')[-2000:])}"
        )
    deadline = time.monotonic() + max(10.0, min(timeout, 600.0))
    last_status: dict[str, Any] = {}
    completed = False
    while time.monotonic() < deadline:
        time.sleep(SUBSCRIPTION_POLL_INTERVAL)
        result = router.cli("subscription_update_status", timeout=15)
        last_status = _parse_status_output(result.stdout or "")
        state = str(last_status.get("state") or last_status.get("status") or "").lower()
        if state in SUBSCRIPTION_TERMINAL_STATES:
            completed = True
            break
    from ..redaction import redact_config

    return {
        "target": router.name,
        "mode": "async",
        "section": section,
        "ok": completed and str(last_status.get("state") or last_status.get("status") or "").lower()
        not in {"error", "failed"},
        "status": redact_config({"status": last_status})["status"],
        "timed_out": not completed,
    }
