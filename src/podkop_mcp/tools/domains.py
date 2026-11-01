"""MCP-инструменты работы с доменами секций.

Модуль — тонкая обёртка над ``domains_core`` и ``router``: он не знает,
как устроен SSH, и не пытается угадать формат хранения. Тип хранения
(``option_text`` / ``option_multiline`` / ``list``) берётся из результата
``Router.read_domains``, поэтому Forkop 1.x получает одну строку, Forkop 2.x —
текст по строке, Podkop — UCI-список.
"""

from __future__ import annotations

import ipaddress
import logging
import re
from typing import Any

from mcp.server import MCPServer

from ..clash import Clash
from ..domains_core import (
    coverage,
    entry_key,
    is_valid_entry,
    merge_entries,
    remove_entries,
    render_value,
    summarize_entries,
)
from ..errors import ConfigError
from ..router import PlanBuilder, Router
from ..services import Services
from .common import guard, preview_or_apply, router_for

log = logging.getLogger(__name__)

# FakeIP у sing-box по умолчанию — 198.18.0.0/15 (RFC 2544) и fc00::/18.
FAKEIP_V4_PREFIXES = ("198.18.", "198.19.")
FAKEIP_V6_NETWORK = ipaddress.ip_network("fc00::/18")
LOCAL_RESOLVER = "127.0.0.1"
ADDRESS_LINE_RE = re.compile(r"^\s*Address(?:es)?\s*:\s*(?P<value>.+?)\s*$", re.IGNORECASE)


# --------------------------------------------------------------------- helpers
def _write_domains(
    builder: PlanBuilder,
    section: str,
    option: str,
    storage: str,
    entries: list[str],
    raw: Any,
    *,
    preserve_comments: bool = True,
) -> None:
    """Пишет набор доменов в формате бэкенда: UCI-список или строка option."""

    if storage == "list":
        # Podkop читает user_domains только при явно заданном dynamic-режиме.
        if builder.router.profile.schema == "podkop" and option == "user_domains":
            builder.set_option(section, "user_domain_list_type", "dynamic")
        builder.set_list(section, option, list(entries))
        return
    builder.set_option(
        section,
        option,
        render_value(entries, storage, original=raw, preserve_comments=preserve_comments),  # type: ignore[arg-type]
    )


def _parse_addresses(text: str) -> list[str]:
    """Собирает IPv4/IPv6 из строк ``Address:`` вывода nslookup.

    Адрес локального резолвера (``127.0.0.1:53``) отбрасывается: это сам
    сервер, а не ответ по домену.
    """

    addresses: list[str] = []
    for line in text.splitlines():
        match = ADDRESS_LINE_RE.match(line)
        if not match:
            continue
        value = match.group("value").strip()
        candidates = [value]
        # «1.2.3.4:53» — так nslookup печатает адрес сервера с портом.
        if value.count(":") == 1:
            candidates.insert(0, value.split(":", 1)[0])
        for candidate in candidates:
            candidate = candidate.strip("[]() ").strip()
            try:
                address = str(ipaddress.ip_address(candidate))
            except ValueError:
                continue
            if address == LOCAL_RESOLVER:
                break
            if address not in addresses:
                addresses.append(address)
            break
    return addresses


def _is_fakeip(address: str) -> bool:
    """Признак FakeIP: трафик такого домена уходит в sing-box."""

    try:
        parsed = ipaddress.ip_address(address)
    except ValueError:
        return False
    if isinstance(parsed, ipaddress.IPv4Address):
        return str(parsed).startswith(FAKEIP_V4_PREFIXES)
    return parsed in FAKEIP_V6_NETWORK


def _is_ip(value: str) -> bool:
    try:
        ipaddress.ip_address(value)
        return True
    except ValueError:
        return False


def _entry_kind(entry: str) -> str:
    """Тип записи для отчёта: domain/zone/full/keyword/regex/ip."""

    lower = entry.lower()
    if lower.startswith("full:"):
        return "full"
    if lower.startswith("keyword:"):
        return "keyword"
    if lower.startswith("regex:"):
        return "regex"
    if entry.startswith("."):
        return "zone"
    if _is_ip(entry):
        return "ip"
    return "domain"


def _normalize_query(domain: str) -> str:
    value = str(domain).strip()
    if not value:
        raise ConfigError("Домен не указан")
    return value.lower().rstrip(".")


def _invalid_error(invalid: list[str]) -> ConfigError:
    return ConfigError("Недопустимые домены: " + ", ".join(invalid))


def _resolve(router: Router, domain: str) -> dict[str, Any]:
    """nslookup через DNS роутера; ошибки не роняют инструмент."""

    try:
        result = router.session.run_argv("nslookup", [domain, LOCAL_RESOLVER])
    except Exception as exc:  # SSH/команда недоступна — это диагностика, не фатально
        log.warning("nslookup %s не выполнился: %s", domain, exc)
        return {"addresses": [], "fakeip": False, "error": str(exc), "exit_code": None}
    addresses = _parse_addresses(result.stdout or "")
    return {
        "addresses": addresses,
        "fakeip": any(_is_fakeip(address) for address in addresses),
        "error": None,
        "exit_code": result.exit_code,
    }


def _verify_domain(router: Router, domain: str) -> dict[str, Any]:
    """Проверка после записи: домен должен резолвиться через DNS роутера."""

    try:
        router.run("sleep 2")
    except Exception as exc:  # пауза не критична, но причину покажем
        log.warning("sleep перед проверкой домена не выполнился: %s", exc)
    resolved = _resolve(router, domain)
    addresses = resolved["addresses"]
    fakeip = bool(resolved["fakeip"])
    if fakeip:
        note = "Домен резолвится в FakeIP — трафик пойдёт через прокси."
    elif addresses:
        note = (
            "Домен резолвится в реальные адреса: возможно, правило ещё не подхватилось, "
            "секция выключена или DNS-кэш не обновился."
        )
    else:
        note = "nslookup не вернул адресов: проверьте DNS/FakeIP роутера (podkop_health)."
    result: dict[str, Any] = {"domain": domain, "addresses": addresses, "fakeip": fakeip, "note": note}
    if resolved.get("error"):
        result["error"] = resolved["error"]
    elif resolved.get("exit_code"):
        result["note"] = f"{note} (nslookup exit={resolved['exit_code']})"
    return result


def _probe_blocked(domain: str, addresses: list[str]) -> str | None:
    """Не даёт использовать HTTP-probe как SSRF на приватные адреса."""

    def blocked(value: str) -> bool:
        try:
            ip = ipaddress.ip_address(value.strip("[]"))
        except ValueError:
            return False
        # FakeIP — это не реальный адрес, а маркер маршрутизации через прокси.
        if ip in ipaddress.ip_network("198.18.0.0/15") or ip in ipaddress.ip_network("fc00::/18"):
            return False
        return ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved

    candidates = [domain, *addresses]
    for candidate in candidates:
        if blocked(str(candidate)):
            return f"HTTP-проверка приватного/локального адреса запрещена: {candidate}"
    return None


def _probe_http(router: Router, domain: str, addresses: list[str] | None = None) -> tuple[int | None, str | None]:
    """HTTPS HEAD через curl на роутере; возвращает ``(http_code, error)``."""

    blocked = _probe_blocked(domain, addresses or [])
    if blocked:
        return None, blocked
    args = ["-m", "8", "-sS", "-o", "/dev/null", "-w", "%{http_code}", "--head", f"https://{domain}/"]
    try:
        result = router.session.run_argv("curl", args)
    except Exception as exc:
        log.warning("curl для %s не выполнился: %s", domain, exc)
        return None, str(exc)
    text = (result.stdout or "").strip()
    if text.isdigit():
        code = int(text)
        if 100 <= code <= 599:
            return code, None
        error = (result.stderr or "").strip() or f"curl вернул код {code} (exit={result.exit_code})"
        return None, error
    error = (result.stderr or "").strip() or f"curl завершился с кодом {result.exit_code} без HTTP-кода"
    return None, error


def _coverage_details(router: Router, domain: str) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """Совпадения по секциям и выбранные community lists (их содержимое неизвестно)."""

    matches: list[dict[str, Any]] = []
    possible_lists: list[dict[str, str]] = []
    config = router.config()
    for section in config.top_sections():
        data = router.read_domains(section.name)
        for entry in data["entries"]:
            if coverage(entry, domain):
                matches.append({"section": section.name, "entry": entry, "kind": _entry_kind(entry)})
        for list_name in section.list_value("community_lists"):
            possible_lists.append({"section": section.name, "list": list_name})
    return matches, possible_lists


def _all_entries(router: Router) -> list[str]:
    """Все домены всех top-level секций (для проверки покрытия)."""

    entries: list[str] = []
    for section in router.config().top_sections():
        entries.extend(router.read_domains(section.name)["entries"])
    return entries


def _section_base(router: Router, section: str, data: dict[str, Any]) -> dict[str, Any]:
    return {
        "target": router.name,
        "section": section,
        "option": data["option"],
        "storage": data["storage"],
    }


# --------------------------------------------------------------------- tools
def register(mcp: MCPServer, services: Services) -> None:
    @mcp.tool()
    @guard
    def podkop_domains_list(section: str = "main", target: str | None = None) -> dict[str, Any]:
        """List domains of one section, normalized plus the raw stored value.

        Works for all backends: Forkop 1.x keeps domain as one space-separated
        string, Forkop 2.x keeps one entry per line in destinations, Podkop uses
        the user_domains UCI list. Use the returned storage/option values to
        understand why entries are written the way they are.
        """

        router = router_for(services, target)
        data = router.read_domains(section)
        return {
            **_section_base(router, section, data),
            "entries": data["entries"],
            "count": len(data["entries"]),
            "invalid": data["invalid"],
            "summary": summarize_entries(data["entries"]),
            "raw": data["raw"],
        }

    @mcp.tool()
    @guard
    def podkop_domains_add(
        section: str,
        domains: list[str],
        target: str | None = None,
        apply: bool = False,
        verify: bool = True,
    ) -> dict[str, Any]:
        """Add domains to a section preserving the backend storage type.

        Forkop 1.x keeps section.domain as a single string: the tool never turns
        it into a UCI list (that would break domain_suffix matching). Forkop 2.x
        writes multiline destinations, Podkop uses the user_domains list.
        Duplicates are reported and not written again; invalid entries raise an
        error. Dry-run by default: the diff and UCI preview are returned and the
        router is untouched. With apply=true the config is written, the service
        is reloaded and, if verify=true, the first added domain is resolved via
        the router DNS to confirm it maps to a FakeIP address.
        """

        router = router_for(services, target)
        if not domains:
            raise ConfigError("Список доменов пуст")
        data = router.read_domains(section)
        merged, added, duplicates, invalid = merge_entries(data["entries"], domains)
        if invalid:
            raise _invalid_error(invalid)
        base = _section_base(router, section, data)
        if not added:
            return {
                **base,
                "applied": False,
                "dry_run": True,
                "changed": False,
                "duplicates": duplicates,
                "invalid": [],
                "hint": "Изменений нет; apply=false не требуется.",
                "message": "Все домены уже есть",
            }

        builder = router.builder("domains-add")
        _write_domains(builder, section, data["option"], data["storage"], merged, data["raw"])
        result = preview_or_apply(router, builder.build(), apply)
        result.update(
            {
                **base,
                "before": data["entries"],
                "after": merged,
                "added": added,
                "duplicates": duplicates,
                "invalid": [],
            }
        )
        if apply and verify:
            result["verification"] = _verify_domain(router, added[0])
        return result

    @mcp.tool()
    @guard
    def podkop_domains_remove(
        section: str,
        domains: list[str],
        target: str | None = None,
        apply: bool = False,
    ) -> dict[str, Any]:
        """Remove domains from a section, keeping the storage type intact.

        Domains that are not present are reported in not_found and do not make
        the call fail. Dry-run by default.
        """

        router = router_for(services, target)
        if not domains:
            raise ConfigError("Список доменов пуст")
        data = router.read_domains(section)
        remaining, removed, not_found = remove_entries(data["entries"], domains)
        base = _section_base(router, section, data)
        if not removed:
            return {
                **base,
                "applied": False,
                "dry_run": True,
                "changed": False,
                "removed": [],
                "not_found": not_found,
                "hint": "Изменений нет; apply=false не требуется.",
                "message": "Указанные домены не найдены в секции",
            }

        builder = router.builder("domains-remove")
        _write_domains(builder, section, data["option"], data["storage"], remaining, data["raw"])
        result = preview_or_apply(router, builder.build(), apply)
        result.update(
            {
                **base,
                "before": data["entries"],
                "after": remaining,
                "removed": removed,
                "not_found": not_found,
            }
        )
        return result

    @mcp.tool()
    @guard
    def podkop_domains_replace(
        section: str,
        domains: list[str],
        target: str | None = None,
        apply: bool = False,
    ) -> dict[str, Any]:
        """Replace the whole domain set of a section.

        Every entry is validated first; on Forkop 2.x comments and blank lines of
        the existing destinations block are preserved. Use podkop_domains_add or
        podkop_domains_remove for incremental changes. Dry-run by default.
        """

        router = router_for(services, target)
        if not domains:
            raise ConfigError("Список доменов пуст: для очистки используйте podkop_domains_remove")
        invalid_entries = [item for item in domains if not is_valid_entry(str(item).strip())]
        if invalid_entries:
            raise _invalid_error(invalid_entries)
        merged, _added, duplicates, _invalid = merge_entries([], domains)
        data = router.read_domains(section)
        base = _section_base(router, section, data)
        if merged == data["entries"]:
            return {
                **base,
                "applied": False,
                "changed": False,
                "duplicates": duplicates,
                "invalid": [],
                "message": "Набор доменов не изменился",
            }

        existing_keys = {entry_key(item) for item in data["entries"]}
        merged_keys = {entry_key(item) for item in merged}
        added = [item for item in merged if entry_key(item) not in existing_keys]
        removed = [item for item in data["entries"] if entry_key(item) not in merged_keys]

        builder = router.builder("domains-replace")
        _write_domains(
            builder,
            section,
            data["option"],
            data["storage"],
            merged,
            data["raw"],
            preserve_comments=True,
        )
        result = preview_or_apply(router, builder.build(), apply)
        result.update(
            {
                **base,
                "before": data["entries"],
                "after": merged,
                "added": added,
                "removed": removed,
                "duplicates": duplicates,
                "invalid": [],
            }
        )
        return result

    @mcp.tool()
    @guard
    def podkop_domains_find(domain: str, target: str | None = None) -> dict[str, Any]:
        """Find where a domain is covered by section entries.

        Checks every top-level section and returns exact matches with the entry
        kind (domain, zone, full, keyword, regex, ip). possible_lists shows
        community lists selected in sections: their content lives on the router
        as rule-sets, so the server cannot match a domain against them.
        """

        router = router_for(services, target)
        normalized = _normalize_query(domain)
        matches, possible_lists = _coverage_details(router, normalized)
        return {
            "target": router.name,
            "domain": normalized,
            "matched": bool(matches),
            "matches": matches,
            "possible_lists": possible_lists,
        }

    @mcp.tool()
    @guard
    def podkop_domains_suggest(
        target: str | None = None,
        limit: int = 20,
        only_direct: bool = True,
    ) -> dict[str, Any]:
        """Suggest hosts seen in live connections but not covered by any section.

        Groups hosts from the Clash API by traffic, skips IP addresses,
        localhost and already covered hosts. With only_direct=true (default) only
        hosts whose chain is direct are suggested, i.e. traffic that currently
        bypasses the proxy and may need a rule. Feed the result to
        podkop_domains_add (suggested_section is usually main).
        """

        router = router_for(services, target)
        limit = max(1, min(int(limit), 100))
        summary = Clash(router).connections_summary(limit=500)
        connections = summary.get("connections") or []
        covered_entries = _all_entries(router)

        aggregated: dict[str, dict[str, Any]] = {}
        for connection in connections:
            host = str(connection.get("host") or "").strip().lower().rstrip(".")
            if not host or _is_ip(host) or host == "localhost":
                continue
            chain = str(connection.get("chain") or "")
            if only_direct and "direct" not in chain.lower():
                continue
            if any(coverage(entry, host) for entry in covered_entries):
                continue
            item = aggregated.setdefault(
                host,
                {"host": host, "count": 0, "upload": 0, "download": 0, "chains": []},
            )
            item["count"] += 1
            item["upload"] += int(connection.get("upload") or 0)
            item["download"] += int(connection.get("download") or 0)
            if chain and chain not in item["chains"]:
                item["chains"].append(chain)

        ranked = sorted(
            aggregated.values(),
            key=lambda item: (item["upload"] + item["download"], item["count"]),
            reverse=True,
        )[:limit]
        for item in ranked:
            item["suggested_section"] = "main"
        return {
            "target": router.name,
            "only_direct": only_direct,
            "scanned": len(connections),
            "count": len(ranked),
            "hosts": ranked,
        }

    @mcp.tool()
    @guard
    def podkop_domain_check(
        domain: str,
        target: str | None = None,
        probe_http: bool = True,
    ) -> dict[str, Any]:
        """Check one domain: DNS resolution, FakeIP, section coverage, HTTPS probe.

        Resolves the domain through the router DNS (127.0.0.1) without touching
        the configuration. fakeip=true means the domain maps to 198.18.x.x or
        fc00::/18 (sing-box FakeIP ranges) and therefore goes through the proxy.
        probe_http runs a curl HEAD request on the router; curl failures are
        reported in http_error instead of raising.
        """

        router = router_for(services, target)
        normalized = _normalize_query(domain)
        if not is_valid_entry(normalized):
            raise ConfigError(f"Некорректный домен: {domain!r}")
        matches, possible_lists = _coverage_details(router, normalized)
        resolved = _resolve(router, normalized)
        http_code: int | None = None
        http_error: str | None = None
        if probe_http:
            http_code, http_error = _probe_http(router, normalized, resolved["addresses"])
        result: dict[str, Any] = {
            "target": router.name,
            "domain": normalized,
            "addresses": resolved["addresses"],
            "fakeip": bool(resolved["fakeip"]),
            "covered": bool(matches),
            "matches": matches,
            "possible_lists": possible_lists,
            "http_code": http_code,
            "http_error": http_error,
        }
        if resolved.get("error"):
            result["resolve_error"] = resolved["error"]
        return result
