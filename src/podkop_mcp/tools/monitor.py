"""Инструменты мониторинга: сводка, живые соединения, скорость трафика.

Все тяжёлые/внешние вызовы вынесены в чистые helper-функции, которые можно
тестировать без SSH: агрегация соединений, фильтрация, расчёт скоростей.
"""

from __future__ import annotations

import time
from typing import Any

from mcp.server import MCPServer

from ..clash import Clash, _normalize_connection
from ..redaction import redact_text
from ..services import Services
from .common import guard, router_for

# Границы длительности сэмплирования трафика (секунды).
MIN_SAMPLE_SECONDS = 0.5
MAX_SAMPLE_SECONDS = 10.0
# Максимум соединений, выкачиваемых за один опрос Clash API.
MAX_CONNECTIONS = 500


def _aggregate_connections(
    items: list[dict[str, Any]],
    key_func: Any,
    top: int,
    *,
    key_name: str,
) -> list[dict[str, Any]]:
    """Считает count и bytes по ключу, возвращает топ-N записей."""

    agg: dict[str, dict[str, Any]] = {}
    for item in items:
        key = str(key_func(item) or "unknown")
        entry = agg.setdefault(key, {key_name: key, "count": 0, "upload": 0, "download": 0})
        entry["count"] += 1
        entry["upload"] += int(item.get("upload") or 0)
        entry["download"] += int(item.get("download") or 0)
    rows = sorted(agg.values(), key=lambda e: e["upload"] + e["download"], reverse=True)
    result: list[dict[str, Any]] = []
    for entry in rows[: max(1, top)]:
        result.append(
            {
                key_name: entry[key_name],
                "count": entry["count"],
                "upload": entry["upload"],
                "download": entry["download"],
                "bytes": entry["upload"] + entry["download"],
            }
        )
    return result


def _top_hosts(items: list[dict[str, Any]], top: int = 10) -> list[dict[str, Any]]:
    """Топ хостов по суммарному трафику."""

    return _aggregate_connections(items, lambda item: item.get("host"), top, key_name="host")


def _top_clients(items: list[dict[str, Any]], top: int = 10) -> list[dict[str, Any]]:
    """Топ клиентов (source_ip) по суммарному трафику."""

    return _aggregate_connections(items, lambda item: item.get("source_ip"), top, key_name="source_ip")


def _top_chains(items: list[dict[str, Any]], top: int = 10) -> list[dict[str, Any]]:
    """Топ цепочек (прокси-маршрутов) по суммарному трафику."""

    return _aggregate_connections(items, lambda item: item.get("chain"), top, key_name="chain")


def _filter_connections(
    items: list[dict[str, Any]],
    *,
    host: str | None = None,
    source_ip: str | None = None,
    chain: str | None = None,
) -> list[dict[str, Any]]:
    """Фильтрует нормализованные соединения.

    host и chain — подстрока без учёта регистра; source_ip — точное
    совпадение или префикс сети: совпадение считается префиксным, только
    если граница приходится на точку ("192.0.2." или "192.0.2"
    матчит 192.0.2.7, но "192.0.20" не матчит 192.0.2.7).
    """

    result = items
    if host:
        needle = host.casefold()
        result = [item for item in result if needle in str(item.get("host") or "").casefold()]
    if source_ip:
        prefix = source_ip if source_ip.endswith(".") else source_ip + "."
        result = [
            item
            for item in result
            if str(item.get("source_ip") or "") == source_ip
            or str(item.get("source_ip") or "").startswith(prefix)
        ]
    if chain:
        needle = chain.casefold()
        result = [item for item in result if needle in str(item.get("chain") or "").casefold()]
    return result


def _compute_traffic(
    first: list[dict[str, Any]],
    second: list[dict[str, Any]],
    totals_first: dict[str, int],
    totals_second: dict[str, int],
    seconds: float,
    *,
    max_top: int = 20,
) -> dict[str, Any]:
    """Считает дельты трафика и скорости по двум сэмплам соединений.

    first/second — списки нормализованных соединений (Clash._normalize_connection);
    totals_* — downloadTotal/uploadTotal соответствующих сэмплов.
    """

    seconds = max(0.001, float(seconds))
    dl_delta = max(0, int(totals_second.get("downloadTotal", 0)) - int(totals_first.get("downloadTotal", 0)))
    ul_delta = max(0, int(totals_second.get("uploadTotal", 0)) - int(totals_first.get("uploadTotal", 0)))
    reset_detected = (
        int(totals_second.get("downloadTotal", 0)) < int(totals_first.get("downloadTotal", 0))
        or int(totals_second.get("uploadTotal", 0)) < int(totals_first.get("uploadTotal", 0))
    )
    rates = {
        "download_bps": round(dl_delta / seconds, 1),
        "upload_bps": round(ul_delta / seconds, 1),
        "download_delta": dl_delta,
        "upload_delta": ul_delta,
    }
    # Индекс первого сэмпла по id: чтобы считать дельты per-connection.
    first_by_id = {item.get("id"): item for item in first if item.get("id")}
    per_id: list[dict[str, Any]] = []
    for item in second:
        cid = item.get("id")
        prev = first_by_id.get(cid) if cid is not None else None
        upload_delta = int(item.get("upload") or 0) - int((prev or {}).get("upload") or 0)
        download_delta = int(item.get("download") or 0) - int((prev or {}).get("download") or 0)
        # Новое соединение: предыдущего сэмпла нет, bytes = весь накопленный
        # трафик (занижение невозможно, возможно завышение за период сэмпла —
        # помечаем флагом new, чтобы consumer мог скорректировать).
        is_new = prev is None
        per_id.append(
            {
                "id": cid,
                "host": item.get("host") or "",
                "source_ip": item.get("source_ip") or "",
                "chain": item.get("chain") or "",
                "upload_delta": max(0, upload_delta),
                "download_delta": max(0, download_delta),
                "bytes": max(0, upload_delta) + max(0, download_delta),
                "new": is_new,
            }
        )
    per_id.sort(key=lambda row: row["bytes"], reverse=True)
    return {
        "totals_first": totals_first,
        "totals_second": totals_second,
        "rates": rates,
        "top_connections": per_id[: max(1, max_top)],
        "reset_detected": reset_detected,
    }


def _safe_call(func: Any, default: Any) -> tuple[Any, str | None]:
    """Вызывает func, при ошибке возвращает (default, текст ошибки)."""

    try:
        return func(), None
    except Exception as exc:  # noqa: BLE001 — сводка не должна падать целиком
        return default, f"{type(exc).__name__}: {exc}"


def _build_snapshot(
    router: Any,
    *,
    include_connections: bool = True,
    top: int = 10,
    sample_seconds: float = 2.0,
    clash_factory: Any = Clash,
) -> dict[str, Any]:
    """Собирает сводку мониторинга; каждая часть — опциональна и не валит остальное."""

    profile = router.profile
    check_errors: dict[str, str] = {}

    service: dict[str, Any] = {}
    if profile.supports("get_status"):
        service, err = _safe_call(lambda: router.cli_json("get_status", timeout=15), {})
        if err:
            check_errors["get_status"] = err

    system: dict[str, Any] = {}
    if profile.supports("get_system_info"):
        system, err = _safe_call(lambda: router.cli_json("get_system_info", timeout=20), {})
        if err:
            check_errors["get_system_info"] = err

    clash = clash_factory(router)
    proxies: dict[str, Any] = {}
    proxies, err = _safe_call(clash.proxies_summary, {})
    if err:
        check_errors["proxies"] = err

    connections: dict[str, Any] = {}
    if include_connections:
        connections, err = _safe_call(lambda: clash.connections_summary(limit=MAX_CONNECTIONS), {})
        if err:
            check_errors["connections"] = err

    traffic: dict[str, Any] = {"rates": {}, "top_connections": [], "reset_detected": False}
    if include_connections and not check_errors.get("connections") and connections:
        # Скорость в одном сэмпле неизвестна — её считает podkop_traffic.
        traffic["rates"] = {"download_bps": 0.0, "upload_bps": 0.0, "download_delta": 0, "upload_delta": 0}
        traffic["top_connections"] = connections.get("connections", [])[: max(1, top)]
        traffic["count"] = connections.get("count", 0)

    checks: dict[str, Any] = {}
    if profile.supports("check_sing_box"):
        result, err = _safe_call(lambda: router.cli("check_sing_box", timeout=30), None)
        if err:
            check_errors["check_sing_box"] = err
        elif result is not None:
            checks["check_sing_box"] = {
                "exit_code": getattr(result, "exit_code", None),
                "output": redact_text((getattr(result, "stdout", "") or "")[-4000:]),
            }

    items = list(connections.get("connections") or [])
    proxies_summary = {
        "group_count": proxies.get("group_count", 0),
        "node_count": proxies.get("node_count", 0),
        "selected": [
            {"name": group.get("name"), "now": group.get("now"), "delay": group.get("delay")}
            for group in proxies.get("selected", [])
        ],
    }
    totals = {
        "downloadTotal": connections.get("downloadTotal", 0),
        "uploadTotal": connections.get("uploadTotal", 0),
        "count": connections.get("count", 0),
    }
    return {
        "target": router.name,
        "backend": profile.public_dict(),
        "service": service,
        "system": system,
        "proxies": proxies_summary,
        "connections": {
            "count": totals["count"],
            "items": items[: max(1, top)],
            "top_hosts": _top_hosts(items, top),
            "top_clients": _top_clients(items, top),
            "top_chains": _top_chains(items, top),
        },
        "traffic": traffic,
        "checks": checks,
        "check_errors": check_errors,
        "sample_seconds": sample_seconds,
    }


def register(mcp: MCPServer, services: Services) -> None:
    @mcp.tool()
    @guard
    def podkop_monitor_snapshot(
        target: str | None = None,
        include_connections: bool = True,
        top: int = 10,
    ) -> dict[str, Any]:
        """Return a monitoring snapshot: service, system, proxies, connections, traffic.

        Collects everything available in one call; each part fails
        independently (errors are reported in check_errors instead of raising).
        Read-only and safe to call repeatedly.
        """

        router = router_for(services, target)
        top = max(1, min(int(top), 50))
        return _build_snapshot(router, include_connections=include_connections, top=top)

    @mcp.tool()
    @guard
    def podkop_connections_live(
        target: str | None = None,
        host: str | None = None,
        source_ip: str | None = None,
        chain: str | None = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        """List live connections with optional filters (host, source_ip, chain).

        ``host`` and ``chain`` are case-insensitive substrings; ``source_ip``
        matches exactly or as a network prefix ("192.0.2." matches all
        clients in that subnet). Read-only.
        """

        router = router_for(services, target)
        clash = Clash(router)
        summary = clash.connections_summary(limit=MAX_CONNECTIONS)
        items = _filter_connections(
            summary.get("connections", []),
            host=host,
            source_ip=source_ip,
            chain=chain,
        )
        limit = max(1, min(int(limit), MAX_CONNECTIONS))
        return {
            "target": router.name,
            "items": items[:limit],
            "count": len(items),
            "returned": min(len(items), limit),
            "filters": {"host": host, "source_ip": source_ip, "chain": chain},
            "totals": {
                "downloadTotal": summary.get("downloadTotal", 0),
                "uploadTotal": summary.get("uploadTotal", 0),
            },
        }

    @mcp.tool()
    @guard
    def podkop_traffic(
        target: str | None = None,
        sample_seconds: float = 2.0,
        top: int = 10,
    ) -> dict[str, Any]:
        """Measure traffic rate by sampling Clash connections twice.

        Returns download/upload bps, per-connection deltas and the top
        consumers. ``sample_seconds`` is clamped to 0.5..10 s. Read-only; the
        call blocks for the sampling interval.
        """

        router = router_for(services, target)
        sample_seconds = max(MIN_SAMPLE_SECONDS, min(float(sample_seconds), MAX_SAMPLE_SECONDS))
        clash = Clash(router)
        payload_first = clash.connections()
        first = [_normalize_connection(item) for item in (payload_first.get("connections") or []) if isinstance(item, dict)]
        totals_first = {
            "downloadTotal": int(payload_first.get("downloadTotal") or 0),
            "uploadTotal": int(payload_first.get("uploadTotal") or 0),
        }
        time.sleep(sample_seconds)
        payload_second = clash.connections()
        second = [_normalize_connection(item) for item in (payload_second.get("connections") or []) if isinstance(item, dict)]
        totals_second = {
            "downloadTotal": int(payload_second.get("downloadTotal") or 0),
            "uploadTotal": int(payload_second.get("uploadTotal") or 0),
        }
        computed = _compute_traffic(first, second, totals_first, totals_second, sample_seconds)
        top = max(1, min(int(top), 50))
        return {
            "target": router.name,
            "sample_seconds": sample_seconds,
            "totals": totals_second,
            "rates": computed["rates"],
            "top_connections": computed["top_connections"][:top],
            "reset_detected": computed["reset_detected"],
        }
