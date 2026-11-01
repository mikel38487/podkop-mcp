"""Универсальные инструменты конфигурации и бэкапов."""

from __future__ import annotations

from typing import Any, Literal

from mcp.server import MCPServer

from ..errors import SafetyError
from ..redaction import mask_value
from ..services import Services
from ..uci import parse_path
from .common import guard, preview_or_apply, router_for


def register(mcp: MCPServer, services: Services) -> None:
    @mcp.tool()
    @guard
    def podkop_config_get(
        target: str | None = None,
        package: str | None = None,
        reveal_secrets: bool = False,
        include_raw: bool = False,
    ) -> dict[str, Any]:
        """Return the full UCI configuration as structured JSON.

        Secrets (subscription URLs, proxy strings, keys) are masked unless
        reveal_secrets=true. The structure is identical for Forkop 1.x/2.x and
        Podkop; use podkop_sections_list for a compact view.
        """

        router = router_for(services, target)
        data = router.config_public(reveal_secrets=reveal_secrets, package=package)
        if include_raw:
            data["raw"] = router.uci_export(mode="raw" if reveal_secrets else "masked")
        return {"target": router.name, "backend": router.profile.public_dict(), **data}

    @mcp.tool()
    @guard
    def podkop_config_export(
        target: str | None = None,
        mode: Literal["masked", "raw"] = "masked",
        reveal_secrets: bool = False,
    ) -> dict[str, Any]:
        """Export the UCI config as text (backup-style).

        ``raw`` requires reveal_secrets=true because it contains proxy URLs and
        keys. By default the masked export from Forkop is returned.
        """

        router = router_for(services, target)
        if mode == "raw" and not reveal_secrets:
            raise SafetyError("Для mode='raw' требуется reveal_secrets=true")
        text = router.uci_export(mode=mode)
        return {"target": router.name, "package": router.profile.package, "mode": mode, "text": text}

    @mcp.tool()
    @guard
    def podkop_option_get(
        path: str,
        target: str | None = None,
        reveal_secrets: bool = False,
    ) -> dict[str, Any]:
        """Read a single UCI option or section by path, e.g. forkop.main.domain."""

        router = router_for(services, target)
        package, section, option = parse_path(path)
        if package != router.profile.package:
            raise SafetyError(
                f"Путь {path!r} относится к пакету {package!r}, а бэкенд использует {router.profile.package!r}"
            )
        config = router.config()
        section_obj = config.section(section)
        if option is None:
            data = section_obj.to_dict()
            if not reveal_secrets:
                data = {key: mask_value(key, value) for key, value in data.items()}
            return {
                "target": router.name,
                "path": path,
                "section": data,
                "type": section_obj.type,
            }
        value = section_obj.value(option)
        is_list = isinstance(value, list)
        if not reveal_secrets:
            value = mask_value(option, value)
        return {
            "target": router.name,
            "path": path,
            "value": value,
            "is_list": is_list,
            "exists": option in section_obj.options,
        }

    @mcp.tool()
    @guard
    def podkop_option_set(
        path: str,
        value: str | list[str] | None,
        target: str | None = None,
        value_type: Literal["auto", "option", "list"] = "auto",
        apply: bool = False,
        reason: str = "option-set",
    ) -> dict[str, Any]:
        """Set a UCI option or list. Dry-run by default; apply=true writes, backs up and reloads.

        ``value_type=auto`` sets a list when ``value`` is a list, otherwise an
        option. The tool preserves the backend package and never touches other
        OpenWrt packages.
        """

        router = router_for(services, target)
        package, section, option = parse_path(path)
        if package != router.profile.package:
            raise SafetyError(f"Запись разрешена только в пакет {router.profile.package!r}")
        if option is None:
            raise SafetyError("Нужен путь с опцией: pkg.section.option")
        builder = router.builder(reason, package=package)
        as_list = value_type == "list" or (value_type == "auto" and isinstance(value, list))
        if as_list:
            values = value if isinstance(value, list) else ([] if value is None else [str(value)])
            builder.set_list(section, option, [str(item) for item in values])
        else:
            builder.set_option(section, option, "" if value is None else str(value))
        return preview_or_apply(router, builder.build(), apply)

    @mcp.tool()
    @guard
    def podkop_option_delete(
        path: str,
        target: str | None = None,
        apply: bool = False,
        reason: str = "option-delete",
    ) -> dict[str, Any]:
        """Delete a UCI option. Dry-run by default; apply=true writes with backup."""

        router = router_for(services, target)
        package, section, option = parse_path(path)
        if package != router.profile.package:
            raise SafetyError(f"Удаление разрешено только из пакета {router.profile.package!r}")
        if option is None:
            raise SafetyError("Нужен путь с опцией: pkg.section.option")
        builder = router.builder(reason, package=package)
        builder.delete_option(section, option)
        return preview_or_apply(router, builder.build(), apply)

    @mcp.tool()
    @guard
    def podkop_backup_create(
        target: str | None = None,
        reason: str = "manual",
        apply: bool = False,
    ) -> dict[str, Any]:
        """Create a config backup on the router (dry-run by default)."""

        router = router_for(services, target)
        if not apply:
            return {
                "applied": False,
                "dry_run": True,
                "hint": "Повторите с apply=true, чтобы создать бэкап.",
                "package": router.profile.package,
                "dir": router.settings.backup_dir,
                "reason": reason,
            }
        return {"applied": True, **router.backup(reason)}

    @mcp.tool()
    @guard
    def podkop_backup_list(target: str | None = None, limit: int = 50) -> dict[str, Any]:
        """List config backups stored on the router."""

        router = router_for(services, target)
        items = router.list_backups(limit)
        return {
            "target": router.name,
            "dir": router.settings.backup_dir,
            "items": items,
            "count": len(items),
        }

    @mcp.tool()
    @guard
    def podkop_backup_restore(
        backup_id: str,
        target: str | None = None,
        apply: bool = False,
    ) -> dict[str, Any]:
        """Restore a config backup by id. Dry-run shows the action; apply=true restores and reloads."""

        router = router_for(services, target)
        if not apply:
            return {
                "applied": False,
                "dry_run": True,
                "hint": "Повторите с apply=true, чтобы восстановить бэкап и перезагрузить сервис.",
                "backup_id": backup_id,
                "package": router.profile.package,
            }
        return {"applied": True, **router.restore_backup(backup_id)}
