"""MCP-инструменты community lists.

Список доступных Rule Set'ов Podkop/Forkop статический: сервер не скачивает
каталог из интернета (на роутере может не быть сети, а набор зависит от
версии бэкенда). Неизвестные имена не блокируются — они могут быть
пользовательскими rule-set'ами, поэтому только помечаются в ``unknown``.
"""

from __future__ import annotations

import re
from typing import Any

from mcp.server import MCPServer

from ..errors import ConfigError
from ..services import Services
from ..uci import UciConfig
from .common import guard, preview_or_apply, router_for

LIST_OPTION = "community_lists"
NAME_RE = re.compile(r"^[a-z0-9_]+$")

# Известные имена community lists (без загрузки из сети). Отсортированы,
# дубликатов нет: набор задан литералом set.
KNOWN_COMMUNITY_LISTS: list[str] = sorted(
    {
        "amazon",
        "anime",
        "anthropic",
        "apple",
        "block",
        "cloudflare",
        "discord",
        "epicgames",
        "facebook",
        "geoblock",
        "github",
        "google",
        "google_ai",
        "hdrezka",
        "instagram",
        "meta",
        "microsoft",
        "news",
        "openai",
        "porn",
        "russia_inside",
        "spotify",
        "steam",
        "supercell",
        "telegram",
        "tiktok",
        "twitch",
        "twitter",
        "whatsapp",
        "youtube",
        "youtube_music",
    }
)


def _validate_names(names: list[str], field: str) -> list[str]:
    """Проверяет имена и убирает повторы, сохраняя порядок."""

    result: list[str] = []
    invalid: list[str] = []
    for raw in names:
        name = str(raw).strip()
        if not name:
            # Пустая строка — тоже нарушение ^[a-z0-9_]+$, иначе пользователь
            # получит непонятное «нужно указать add и/или remove».
            invalid.append("''")
            continue
        if not NAME_RE.fullmatch(name):
            invalid.append(name)
            continue
        if name not in result:
            result.append(name)
    if invalid:
        raise ConfigError(
            f"Недопустимые имена community lists в {field}: {', '.join(invalid)} (ожидается ^[a-z0-9_]+$)"
        )
    return result


def _unknown(selected: list[str]) -> list[str]:
    known = set(KNOWN_COMMUNITY_LISTS)
    return [name for name in selected if name not in known]


def _selected(config: UciConfig, section: str) -> list[str]:
    return config.section(section).list_value(LIST_OPTION)


def _summary(section: str, selected: list[str]) -> dict[str, Any]:
    return {
        "section": section,
        "selected": selected,
        "unknown": _unknown(selected),
        "count": len(selected),
    }


def register(mcp: MCPServer, services: Services) -> None:
    @mcp.tool()
    @guard
    def podkop_community_lists_get(
        section: str | None = None,
        target: str | None = None,
    ) -> dict[str, Any]:
        """Return selected and available community lists of sections.

        With section: the selected lists of that section plus the built-in
        catalogue of known names. Without section: a map for every top-level
        section. Names outside the catalogue are reported in unknown (custom
        rule-sets are allowed and never removed automatically).
        """

        router = router_for(services, target)
        config = router.config()
        if section is not None:
            selected = _selected(config, section)
            return {
                "target": router.name,
                **_summary(section, selected),
                "available": list(KNOWN_COMMUNITY_LISTS),
            }
        sections: dict[str, Any] = {}
        for item in config.top_sections():
            selected = item.list_value(LIST_OPTION)
            sections[item.name] = _summary(item.name, selected)
        return {
            "target": router.name,
            "backend": router.profile.public_dict(),
            "sections": sections,
            "available": list(KNOWN_COMMUNITY_LISTS),
            "count": len(sections),
        }

    @mcp.tool()
    @guard
    def podkop_community_lists_update(
        section: str,
        add: list[str] | None = None,
        remove: list[str] | None = None,
        target: str | None = None,
        apply: bool = False,
    ) -> dict[str, Any]:
        """Add or remove community lists of one section as a UCI list.

        Names must match ^[a-z0-9_]+$; duplicates are dropped and the order of
        existing lists is preserved. The whole list is written at once, so no
        per-item add_list/del_list calls are issued. Names outside the built-in
        catalogue are reported in unknown but accepted. Dry-run by default;
        apply=true writes the config, creates a backup and reloads the service.
        """

        router = router_for(services, target)
        add_names = _validate_names(add or [], "add")
        remove_names = _validate_names(remove or [], "remove")
        if not add_names and not remove_names:
            raise ConfigError("Нужно указать add и/или remove")

        config = router.config()
        before = _selected(config, section)
        before_set = set(before)
        merged = list(before)
        added = []
        for name in add_names:
            if name not in before_set and name not in merged:
                merged.append(name)
                added.append(name)
        remove_set = set(remove_names)
        merged = [name for name in merged if name not in remove_set]
        merged_set = set(merged)
        added = [name for name in added if name in merged_set]
        removed = [name for name in before if name not in merged_set]

        base: dict[str, Any] = {
            "target": router.name,
            "section": section,
            "selected_before": before,
            "selected_after": merged,
            "added": added,
            "removed": removed,
            "unknown": _unknown(merged),
        }
        if not added and not removed:
            return {
                **base,
                "applied": False,
                "dry_run": True,
                "changed": False,
                "hint": "Изменений нет; apply=false не требуется.",
                "message": "Набор community lists не изменился",
            }

        builder = router.builder("community-lists-update")
        builder.set_list(section, LIST_OPTION, merged)
        result = preview_or_apply(router, builder.build(), apply)
        result.update(base)
        return result
