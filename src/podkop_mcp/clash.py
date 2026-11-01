"""Клиент Clash API через CLI Forkop/Podkop.

Forkop 1.x и Podkop используют action-интерфейс:
``forkop clash_api get_connections``.
Forkop 2.x — HTTP-транспорт:
``forkop clash_api GET /connections``.

Наружу отдаются нормализованные словари, чтобы инструменты не зависели
от версии бэкенда.
"""

from __future__ import annotations

import json
import urllib.parse
from typing import Any

from .errors import BackendError, SafetyError
from .router import Router


class Clash:
    def __init__(self, router: Router) -> None:
        self.router = router

    @property
    def mode(self) -> str:
        return self.router.profile.clash_mode

    def _call(self, action: str, http_method: str, path: str, body: str = "") -> dict[str, Any]:
        if self.mode == "http":
            args = [http_method, path]
            if body:
                args.append(body)
            payload = self.router.cli_json("clash_api", *args, timeout=60)
            status = payload.get("status")
            response_body = payload.get("body")
            if isinstance(status, int) and status >= 400:
                raise BackendError(f"Clash API {http_method} {path}: HTTP {status}: {response_body}")
            if isinstance(response_body, str):
                try:
                    parsed = json.loads(response_body)
                except json.JSONDecodeError:
                    return payload
                if isinstance(parsed, dict):
                    return parsed
            elif isinstance(response_body, dict):
                return response_body
            return payload
        args = [action]
        if body:
            args.append(body)
        return self.router.cli_json("clash_api", *args, timeout=60)

    def connections(self) -> dict[str, Any]:
        return self._call("get_connections", "GET", "/connections")

    def proxies(self) -> dict[str, Any]:
        return self._call("get_proxies", "GET", "/proxies")

    def refresh_rules(self) -> dict[str, Any]:
        """Обновляет rule-providers (встроенные/удалённые списки) на Forkop 2.x."""

        if self.mode != "http":
            raise BackendError("Обновление rule-providers поддерживается только Forkop 2.x")
        return self._call("", "PUT", "/providers/rules")

    def refresh_providers(self) -> dict[str, Any]:
        """Обновляет proxy-providers (подписки) через Clash API Forkop 2.x."""

        if self.mode != "http":
            raise BackendError(
                "Обновление provider-подписок через Clash API поддерживается только Forkop 2.x"
            )
        listing = self._call("", "GET", "/providers/proxies")
        providers = listing.get("providers")
        names = sorted(providers) if isinstance(providers, dict) else []
        refreshed: list[str] = []
        errors: dict[str, str] = {}
        for name in names:
            quoted = urllib.parse.quote(name, safe="")
            try:
                self._call("", "PUT", f"/providers/proxies/{quoted}")
                refreshed.append(name)
            except Exception as exc:  # noqa: BLE001 — один provider не должен валить остальные
                errors[name] = str(exc)
        return {"providers": names, "refreshed": refreshed, "errors": errors}

    def proxy_latency(self, name: str, *, url: str = "https://www.gstatic.com/generate_204", timeout_ms: int = 5000) -> dict[str, Any]:
        if self.mode != "http":
            raise BackendError(
                "Активный тест задержки отдельного прокси поддерживается только Forkop 2.x. "
                "Для Forkop 1.x используйте podkop_proxies_list: задержки из истории URLTest."
            )
        quoted_name = urllib.parse.quote(name, safe="")
        query = urllib.parse.urlencode({"url": url, "timeout": timeout_ms})
        return self._call("", "GET", f"/proxies/{quoted_name}/delay?{query}")

    def group_latency(self, name: str, *, url: str = "https://www.gstatic.com/generate_204", timeout_ms: int = 5000) -> dict[str, Any]:
        if self.mode != "http":
            raise BackendError("Групповой тест задержки поддерживается только Forkop 2.x")
        quoted_name = urllib.parse.quote(name, safe="")
        query = urllib.parse.urlencode({"url": url, "timeout": timeout_ms})
        return self._call("", "GET", f"/group/{quoted_name}/delay?{query}")

    def select_proxy(self, group: str, proxy: str) -> dict[str, Any]:
        if self.router.settings.read_only:
            raise SafetyError("PODKOP_MCP_READ_ONLY=1: переключение прокси запрещено")
        if self.mode != "http":
            raise BackendError(
                "Переключение прокси в runtime поддерживается только Forkop 2.x. "
                "На Forkop 1.x выберите сервер в LuCI или перезапустите сервис."
            )
        quoted_group = urllib.parse.quote(group, safe="")
        body = f'{{"name":{_json_string(proxy)}}}'
        return self._call("", "PUT", f"/proxies/{quoted_group}", body)

    def close_connection(self, connection_id: str) -> dict[str, Any]:
        if self.router.settings.read_only:
            raise SafetyError("PODKOP_MCP_READ_ONLY=1: закрытие соединений запрещено")
        if self.mode == "http":
            return self._call("", "DELETE", f"/connections/{urllib.parse.quote(connection_id, safe='')}")
        return self._call("close_connection", "", "", body=connection_id)

    def close_all_connections(self) -> dict[str, Any]:
        if self.router.settings.read_only:
            raise SafetyError("PODKOP_MCP_READ_ONLY=1: закрытие соединений запрещено")
        if self.mode == "http":
            return self._call("", "DELETE", "/connections")
        return self._call("close_all_connections", "", "")

    # ------------------------------------------------------------------ normalize
    def proxies_summary(self) -> dict[str, Any]:
        payload = self.proxies()
        proxies = payload.get("proxies")
        if not isinstance(proxies, dict):
            raise BackendError("Clash API /proxies вернул неожиданный формат")
        groups: list[dict[str, Any]] = []
        nodes: list[dict[str, Any]] = []
        for name, data in proxies.items():
            if not isinstance(data, dict):
                continue
            proxy_type = str(data.get("type") or "")
            history = data.get("history") if isinstance(data.get("history"), list) else []
            last_delay = None
            if history:
                last = history[-1]
                if isinstance(last, dict):
                    last_delay = last.get("delay")
            item = {
                "name": name,
                "type": proxy_type,
                "now": data.get("now"),
                "alive": data.get("alive"),
                "udp": data.get("udp"),
                "delay": last_delay,
            }
            if proxy_type in {"Selector", "URLTest", "Fallback", "LoadBalance"} or data.get("all"):
                item["all"] = data.get("all") or []
                item["count"] = len(item["all"])
                groups.append(item)
            elif proxy_type not in {"Direct", "Block", "Reject", "Compatible", "Pass"} and name != "GLOBAL":
                nodes.append(item)
        selected = [group for group in groups if group.get("now")]
        return {
            "groups": groups,
            "selected": selected,
            "nodes": nodes,
            "group_count": len(groups),
            "node_count": len(nodes),
        }

    def connections_summary(self, *, limit: int = 50) -> dict[str, Any]:
        payload = self.connections()
        raw = payload.get("connections")
        if not isinstance(raw, list):
            raw = []
        items = [_normalize_connection(item) for item in raw if isinstance(item, dict)]
        items.sort(key=lambda item: (item.get("upload") or 0) + (item.get("download") or 0), reverse=True)
        return {
            "downloadTotal": payload.get("downloadTotal", 0),
            "uploadTotal": payload.get("uploadTotal", 0),
            "connections": items[: max(1, min(limit, 500))],
            "count": len(items),
        }


def _normalize_connection(data: dict[str, Any]) -> dict[str, Any]:
    metadata = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
    chains = data.get("chains") if isinstance(data.get("chains"), list) else []
    return {
        "id": data.get("id"),
        "host": metadata.get("host") or metadata.get("destinationIP") or "",
        "destination_ip": metadata.get("destinationIP") or "",
        "destination_port": metadata.get("destinationPort") or "",
        "source_ip": metadata.get("sourceIP") or "",
        "source_port": metadata.get("sourcePort") or "",
        "network": metadata.get("network") or "",
        "type": metadata.get("type") or "",
        "process_path": metadata.get("processPath") or "",
        "chains": chains,
        "chain": chains[0] if chains else "direct",
        "rule": (data.get("rule") or "")[:400],
        "upload": int(data.get("upload") or 0),
        "download": int(data.get("download") or 0),
        "start": data.get("start") or "",
    }


def _json_string(value: str) -> str:
    import json

    return json.dumps(value, ensure_ascii=False)
