"""Инструменты управления секциями."""

from __future__ import annotations

import re
from typing import Any

from mcp.server import MCPServer

from ..domains_core import summarize_entries
from ..errors import ConfigError, SafetyError
from ..redaction import mask_value
from ..services import Services
from .common import guard, preview_or_apply, router_for, section_enabled

CHILD_TYPES = (
    "subscription_url",
    "urltest",
    "connection_link",
    "tunnel",
    "tunnel_peer",
    "interface",
    "link",
    "json",
    "section_interface",
    "fallback_group",
)
FORKOP2_ACTIONS = {"connection", "dns"}
FORKOP2_TUNNEL_KINDS = {
    "warp",
    "wireguard",
    "amneziawg",
    "tailscale",
    "openvpn-client",
    "openconnect",
    "json",
}
FORKOP2_CHILD_TYPES = {"urltest", "fallback_group", "link", "interface", "json", "tunnel_peer"}
CHILD_NAME_RE = re.compile(r"[A-Za-z0-9_-]{1,32}")


def _validate_child_name(name: str) -> str:
    value = name.strip()
    if not CHILD_NAME_RE.fullmatch(value):
        raise ConfigError("Имя дочерней секции: только A-Za-z0-9_- (1..32 символа)")
    return value


def _section_summary(router: Any, name: str, section: Any) -> dict[str, Any]:
    profile = router.profile
    try:
        domain_data = router.read_domains(name)
        domain_option = domain_data["option"]
        domain_entries = domain_data["entries"]
        domain_invalid = domain_data["invalid"]
    except Exception as exc:  # секция с конфликтом полей не должна ломать весь список
        domain_option = profile.domain_option
        domain_entries = []
        domain_invalid = [f"{type(exc).__name__}: {exc}"]
    config = router.config()
    children = {
        child_type: len(config.children(child_type, name))
        for child_type in CHILD_TYPES
        if config.children(child_type, name)
    }
    if "enabled" in section.options or profile.kind != "podkop":
        enabled = section_enabled(section.options)
    else:
        # В Podkop 0.7.x у обычной секции нет option enabled: она активна,
        # пока не выключена/удалена.
        enabled = True
    return {
        "name": name,
        "type": section.type,
        "label": section.value("label") or name,
        "action": section.value("action") or section.value("connection_type") or "",
        "enabled": enabled,
        "domain_option": domain_option,
        "domain_count": len(domain_entries),
        "domains": summarize_entries(domain_entries),
        "domain_errors": domain_invalid,
        "community_lists": section.list_value("community_lists"),
        "children": children,
        "options_count": len(section.options),
    }


def register(mcp: MCPServer, services: Services) -> None:
    @mcp.tool()
    @guard
    def podkop_sections_list(
        target: str | None = None,
        include_children: bool = False,
        reveal_secrets: bool = False,
    ) -> dict[str, Any]:
        """List routing sections with action, enabled flag, domain counts and lists.

        This is the fastest overview of what Forkop/Podkop is doing. Child UCI
        sections (subscriptions, URLTest groups, links) are counted; set
        include_children=true to return them too.
        """

        router = router_for(services, target)
        config = router.config()
        sections = [_section_summary(router, name, section) for name, section in config.sections.items() if section.type == "section"]
        result: dict[str, Any] = {
            "target": router.name,
            "backend": router.profile.public_dict(),
            "sections": sections,
            "count": len(sections),
        }
        if include_children:
            children: dict[str, Any] = {}
            for section in config.sections.values():
                for child_type in CHILD_TYPES:
                    if section.type != child_type:
                        continue
                    parent = str(section.value("section") or section.value("group") or "")
                    item = section.to_dict()
                    if not reveal_secrets:
                        item = {key: mask_value(key, value) for key, value in item.items()}
                    children.setdefault(parent, []).append(item)
            result["children"] = children
        return result

    @mcp.tool()
    @guard
    def podkop_section_get(
        section: str,
        target: str | None = None,
        reveal_secrets: bool = False,
        include_children: bool = True,
    ) -> dict[str, Any]:
        """Return all options of one section plus its child items."""

        router = router_for(services, target)
        config = router.config()
        section_obj = config.section(section)
        options = section_obj.to_dict()
        if not reveal_secrets:
            options = {key: mask_value(key, value) for key, value in options.items()}
        children: dict[str, list[dict[str, Any]]] = {}
        if include_children:
            for child_type in CHILD_TYPES:
                items = []
                for child in config.children(child_type, section):
                    data = child.to_dict()
                    if not reveal_secrets:
                        data = {key: mask_value(key, value) for key, value in data.items()}
                    items.append(data)
                if items:
                    children[child_type] = items
        return {
            "target": router.name,
            "section": section,
            "options": options,
            "children": children,
            "domains": router.read_domains(section),
        }

    @mcp.tool()
    @guard
    def podkop_section_create(
        name: str,
        action: str = "connection",
        options: dict[str, Any] | None = None,
        target: str | None = None,
        apply: bool = False,
    ) -> dict[str, Any]:
        """Create a new routing section. Dry-run by default.

        Minimal defaults are backend-specific: Forkop gets enabled=1 and the
        requested action; Podkop gets connection_type=proxy. For zapret2 on
        Forkop 1.x you must pass nfqws2_opt in options, otherwise the generated
        config will be rejected by the core.
        """

        router = router_for(services, target)
        profile = router.profile
        normalized_name = name.strip()
        if not normalized_name or normalized_name == "settings":
            raise SafetyError("Недопустимое имя секции")
        if profile.schema == "forkop2" and action not in FORKOP2_ACTIONS:
            raise ConfigError(
                f"Forkop 2.0 поддерживает action={sorted(FORKOP2_ACTIONS)}, получено {action!r}"
            )
        builder = router.builder("section-create")
        builder.create_section(normalized_name, "section")
        builder.set_option(normalized_name, "enabled", "1")
        if profile.schema in {"forkop1", "forkop2"}:
            builder.set_option(normalized_name, "action", action)
        if profile.schema == "forkop2" and "destinations" not in (options or {}):
            builder.set_option(normalized_name, "destinations", "")
        if profile.schema == "forkop1" and "domain" not in (options or {}):
            builder.set_option(normalized_name, "domain", "")
        if profile.schema == "podkop":
            builder.set_option(normalized_name, "connection_type", "proxy")
            builder.set_option(normalized_name, "proxy_config_type", "url")
            builder.set_option(normalized_name, "user_domain_list_type", "dynamic")

        for key, value in (options or {}).items():
            _set_section_option(builder, normalized_name, key, value)
        if profile.schema == "forkop1" and action == "zapret2" and "nfqws2_opt" not in (options or {}):
            builder.warnings.append(
                "Для action=zapret2 нужен nfqws2_opt: без стратегии Forkop не сможет запустить nfqws2."
            )
        if action == "connection" and not any(
            key in (options or {})
            for key in ("proxy_string", "subscription_url", "interface", "outbound_json", "selector_proxy_links", "urltest_proxy_links")
        ):
            builder.warnings.append(
                "Секция connection создана без источника подключения. "
                "Добавьте подписку/ссылку/интерфейс, иначе reload может отклонить конфиг."
            )
        return preview_or_apply(router, builder.build(), apply)

    @mcp.tool()
    @guard
    def podkop_tunnel_create(
        section: str,
        name: str,
        kind: str,
        label: str | None = None,
        options: dict[str, Any] | None = None,
        target: str | None = None,
        apply: bool = False,
    ) -> dict[str, Any]:
        """Create a Forkop 2.x tunnel child (WARP/WireGuard/OpenVPN/...). Dry-run by default.

        ``kind`` is one of warp, wireguard, amneziawg, tailscale,
        openvpn-client, openconnect, json. Extra backend options are passed
        through; secrets in preview are masked.
        """

        router = router_for(services, target)
        profile = router.profile
        if profile.schema not in {"forkop1", "forkop2"}:
            raise ConfigError("Туннели поддерживаются только Forkop")
        if profile.schema == "forkop2" and kind not in FORKOP2_TUNNEL_KINDS:
            raise ConfigError(f"Неизвестный kind туннеля: {kind!r}")
        config = router.config()
        config.section(section)
        child = _validate_child_name(name)
        payload = dict(options or {})
        for reserved in ("section", "kind", "name", "label"):
            payload.pop(reserved, None)
        detour = payload.get("detour_section")
        if detour and detour not in config.sections:
            raise ConfigError(f"detour_section {detour!r} не существует")
        builder = router.builder("tunnel-create")
        builder.create_section(child, "tunnel")
        builder.set_option(child, "section", section)
        builder.set_option(child, "name", label or name)
        builder.set_option(child, "kind", kind)
        for key, value in payload.items():
            _set_section_option(builder, child, key, value)
        if kind == "warp" and not any(key.startswith("warp_profile_") for key in payload):
            builder.warnings.append(
                "Для kind=warp нет warp_profile_*: профиль можно получить через generate_warp_profile."
            )
        return preview_or_apply(router, builder.build(), apply)

    @mcp.tool()
    @guard
    def podkop_child_create(
        section: str,
        child_type: str,
        name: str,
        label: str | None = None,
        options: dict[str, Any] | None = None,
        target: str | None = None,
        apply: bool = False,
    ) -> dict[str, Any]:
        """Create a generic Forkop 2.x child (urltest/fallback_group/link/interface/json/tunnel_peer).

        This is a low-level escape hatch for new 2.0 entities; domain tools and
        podkop_subscription_add remain the safe high-level paths. Dry-run by default.
        """

        router = router_for(services, target)
        profile = router.profile
        if profile.schema not in {"forkop1", "forkop2"}:
            raise ConfigError("Дочерние секции поддерживаются только Forkop")
        if child_type not in FORKOP2_CHILD_TYPES:
            raise ConfigError(
                f"child_type должен быть одним из {sorted(FORKOP2_CHILD_TYPES)}; "
                "для tunnel используйте podkop_tunnel_create, для subscription_url — podkop_subscription_add"
            )
        config = router.config()
        config.section(section)
        child = _validate_child_name(name)
        payload = dict(options or {})
        for reserved in ("section", "kind", "name", "label"):
            payload.pop(reserved, None)
        builder = router.builder("child-create")
        builder.create_section(child, child_type)
        builder.set_option(child, "section", section)
        if label:
            builder.set_option(child, "name", label)
        for key, value in payload.items():
            _set_section_option(builder, child, key, value)
        return preview_or_apply(router, builder.build(), apply)

    @mcp.tool()
    @guard
    def podkop_section_update(
        section: str,
        options: dict[str, Any],
        target: str | None = None,
        apply: bool = False,
    ) -> dict[str, Any]:
        """Update selected options of a section (partial update). Dry-run by default.

        Use podkop_domains_add/remove for domain lists: this tool writes raw
        values but does not normalize domain syntax.
        """

        router = router_for(services, target)
        if not options:
            raise ConfigError("options пуст")
        profile = router.profile
        if profile.schema == "forkop2" and "action" in options and str(options["action"]) not in FORKOP2_ACTIONS:
            raise ConfigError(
                f"Forkop 2.0 поддерживает action={sorted(FORKOP2_ACTIONS)}, получено {options['action']!r}"
            )
        router.config().section(section)
        builder = router.builder("section-update")
        for key, value in options.items():
            _set_section_option(builder, section, key, value)
        return preview_or_apply(router, builder.build(), apply)

    @mcp.tool()
    @guard
    def podkop_section_delete(
        section: str,
        target: str | None = None,
        cascade: bool = True,
        allow_main: bool = False,
        apply: bool = False,
    ) -> dict[str, Any]:
        """Delete a section. Child items are deleted when cascade=true.

        Deleting the main section is refused unless allow_main=true; it is
        usually a configuration error. Dry-run by default.
        """

        router = router_for(services, target)
        if section == "main" and not allow_main:
            raise SafetyError("Удаление секции main запрещено без allow_main=true")
        config = router.config()
        config.section(section)
        builder = router.builder("section-delete")
        if cascade:
            for child_type in CHILD_TYPES:
                for child in config.children(child_type, section):
                    builder.delete_section(child.name)
        builder.delete_section(section)
        return preview_or_apply(router, builder.build(), apply)

    @mcp.tool()
    @guard
    def podkop_section_enable(
        section: str,
        enabled: bool,
        target: str | None = None,
        apply: bool = False,
    ) -> dict[str, Any]:
        """Enable or disable a section without deleting it."""

        router = router_for(services, target)
        router.config().section(section)
        builder = router.builder("section-enable")
        builder.set_option(section, "enabled", "1" if enabled else "0")
        return preview_or_apply(router, builder.build(), apply)


def _set_section_option(builder: Any, section: str, key: str, value: Any) -> None:
    if isinstance(value, bool):
        builder.set_option(section, key, "1" if value else "0")
    elif isinstance(value, list):
        builder.set_list(section, key, [str(item) for item in value])
    elif value is None:
        builder.delete_option(section, key)
    else:
        builder.set_option(section, key, str(value))
