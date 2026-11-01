"""Live-интеграционные тесты podkop-mcp против реального роутера (только чтение).

Запуск::

    $env:PODKOP_MCP_LIVE = "1"
    .\\.venv\\Scripts\\python.exe -m pytest -q -m live

Без ``PODKOP_MCP_LIVE=1`` все тесты модуля пропускаются: обычный ``pytest``
работает без сети (см. ``tests/conftest.py``).

Правила безопасности:

1. Окружение жёстко фиксируется в :data:`LIVE_VARS`: SSH-алиас ``router-home``,
   ключ ``C:/Users/<user>/.ssh/router_key``, ``host_key_policy=reject`` и
   ``PODKOP_MCP_READ_ONLY=1``. Переменные, способные увести сервер на другой
   target, на время сессии удаляются.
2. Ни один тест не передаёт ``apply=true`` в изменяющие инструменты и не
   вызывает ``podkop_service_action`` / ``podkop_lists_update``. Единственная
   запись «в сторону роутера» — dry-run ``podkop_domains_add``, который по
   контракту не выполняет ни одной команды записи (только читает конфиг).

Инструменты вызываются в процессе через ``MCPServer.call_tool`` — это тот же
путь, которым пользуется MCP-клиент, и результат берётся из
``CallToolResult.structured_content``.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from podkop_mcp.server import create_server

pytestmark = pytest.mark.live

REPO_ROOT = Path(__file__).resolve().parents[2]
KEY_PATH = Path(
    os.environ.get("PODKOP_MCP_LIVE_KEY_PATH", str(Path("~/.ssh/id_ed25519").expanduser()))
)
SSH_CONFIG_HOST = os.environ.get("PODKOP_MCP_LIVE_SSH_CONFIG_HOST", "router-home")

# Окружение сервера: только чтение, SSH через алиас ~/.ssh/config.
LIVE_VARS = {
    "PODKOP_MCP_SSH_CONFIG_HOST": SSH_CONFIG_HOST,
    "PODKOP_MCP_SSH_KEY_PATH": str(KEY_PATH),
    "PODKOP_MCP_HOST_KEY_POLICY": "reject",
    "PODKOP_MCP_READ_ONLY": "1",
}
# Эти переменные переопределили бы target/конфиг — на время live-сессии их убираем.
CONFLICTING_VARS = ("PODKOP_MCP_SSH_HOST", "PODKOP_MCP_CONFIG", "PODKOP_MCP_TARGETS")

# Признак «длинного токена» в пути URL: так выглядит секрет подписки.
TOKEN_PATH_RE = re.compile(r"[A-Za-z0-9_\-]{18,}")
# Схемы прокси-ссылок: их не должно быть в маскированном выводе.
PROXY_SCHEME_RE = re.compile(r"\b(?:vless|vmess|ss|ssr|trojan|hysteria2?|tuic|wireguard)://", re.IGNORECASE)

# Состояние живого тестового роутера на 2026-10-29 (Forkop 1.0.5).
EXPECTED_MAIN_DOMAIN_COUNT = 42

CallTool = Callable[[str, dict[str, Any] | None], dict[str, Any]]


def _result_text(result: Any) -> str:
    """Текст ответа инструмента для сообщений об ошибке."""

    structured = getattr(result, "structured_content", None)
    if structured:
        return json.dumps(structured, ensure_ascii=False)[:2000]
    for item in getattr(result, "content", None) or []:
        text = getattr(item, "text", None)
        if text:
            return str(text)[:2000]
    return repr(result)[:1000]


def _without_aliases(config: dict[str, Any]) -> dict[str, Any]:
    """Копия конфига без ``aliases``.

    ``aliases`` — служебная карта ``cfg07a6c -> @subscription_url[0]``: имена
    ``cfg...`` генерирует ubus, и при любой перезаписи конфига они меняются,
    поэтому на равенство конфигурации они не влияют.
    """

    return {key: value for key, value in config.items() if key != "aliases"}


def _dump(payload: dict[str, Any]) -> str:
    return json.dumps(_without_aliases(payload), ensure_ascii=False, sort_keys=True)


def _is_masked(value: Any) -> bool:
    """Маскировано ли значение: есть ``***`` и нет длинного токена в пути."""

    text = str(value)
    if "***" not in text:
        return False
    return TOKEN_PATH_RE.search(text) is None


@pytest.fixture(scope="session")
def live_env() -> dict[str, str]:
    """Выставляет безопасное окружение live-сессии и восстанавливает его после."""

    if os.environ.get("PODKOP_MCP_LIVE") != "1":
        pytest.skip("live-тесты требуют PODKOP_MCP_LIVE=1 (реальный роутер, только чтение)")
    if not KEY_PATH.is_file():
        pytest.skip(f"нет SSH-ключа {KEY_PATH}")

    saved = {key: os.environ.get(key) for key in (*LIVE_VARS, *CONFLICTING_VARS)}
    for key in CONFLICTING_VARS:
        os.environ.pop(key, None)
    os.environ.update(LIVE_VARS)
    try:
        yield dict(LIVE_VARS)
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


@pytest.fixture(scope="session")
def live_server(live_env: dict[str, str]) -> MCPServer:
    """MCP-сервер, собранный из окружения (``create_server`` без аргументов)."""

    return create_server()


@pytest.fixture(scope="session")
def call_tool_raw(live_server: MCPServer) -> CallTool:
    """Вызывает инструмент и возвращает ``CallToolResult`` без проверки ошибок."""

    def _call(name: str, args: dict[str, Any] | None = None) -> Any:
        return asyncio.run(live_server.call_tool(name, args or {}))

    return _call


@pytest.fixture(scope="session")
def call_tool(call_tool_raw: CallTool) -> CallTool:
    """Вызывает read-only инструмент и возвращает его ``structured_content``."""

    def _call(name: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
        result = call_tool_raw(name, args)
        assert not getattr(result, "is_error", False), f"{name} вернул ошибку: {_result_text(result)}"
        content = getattr(result, "structured_content", None)
        assert isinstance(content, dict) and content, f"{name}: пустой structuredContent"
        return content

    return _call


def _fresh_config_get() -> dict[str, Any]:
    """``podkop_config_get`` через новый сервер: обходит кэш UciConfig в Router.

    ``Router.config`` кэширует разобранный конфиг до ``invalidate()`` (router.py:263),
    поэтому повторное чтение тем же сервером не докажет, что dry-run ничего не
    записал. Новый экземпляр сервера читает конфиг с роутера заново.
    """

    server = create_server()
    result = asyncio.run(server.call_tool("podkop_config_get", {}))
    assert not getattr(result, "is_error", False), f"config_get: {_result_text(result)}"
    content = getattr(result, "structured_content", None)
    assert isinstance(content, dict) and content
    return content


# --------------------------------------------------------------------------- система


def test_doctor_reports_forkop1_and_read_only(call_tool: CallTool) -> None:
    """doctor: SSH работает, бэкенд определён как Forkop 1.x, режим read-only."""

    doctor = call_tool("podkop_doctor")

    assert doctor["ok"] is True, doctor.get("error")
    assert doctor["checks"]["ssh"]["ok"] is True
    backend = doctor["checks"]["backend"]
    assert backend["kind"] == "forkop"
    assert backend["schema"] == "forkop1"
    assert backend["version"] == "1.0.5"
    assert backend["config_package"] == "forkop"
    assert backend["domain_storage"] == "option_text"
    assert doctor["read_only"] is True
    assert doctor["settings"]["read_only"] is True


def test_status_reports_running_service(call_tool: CallTool) -> None:
    """status: сервис запущен, backend и версии sing-box отдаются как JSON."""

    status = call_tool("podkop_status")

    assert status["backend"]["config_package"] == "forkop"
    service = status["service"]
    assert service["exit_code"] == 0
    assert service["data"]["running"] == 1
    assert status["system"]["data"]["forkop_version"] == "1.0.5"
    assert status["system"]["data"]["sing_box_version"]


def test_system_info_reports_hardware(call_tool: CallTool) -> None:
    """system_info: модель, ядро, load, память, аптайм и интерфейсы читаются из /proc."""

    info = call_tool("podkop_system_info")

    assert isinstance(info["system"]["device_model"], str) and info["system"]["device_model"]
    assert "OpenWrt" in info["system"]["openwrt_version"]
    assert float(info["load"]["1m"]) >= 0.0
    assert info["uptime_seconds"] > 0
    assert info["memory_kb"]["MemTotal"] > 0
    assert info["interfaces"], "нет вывода ip address"
    assert info["raw_exit_code"] == 0


def test_logs_returns_requested_count(call_tool: CallTool) -> None:
    """logs: счётчик совпадает с числом строк и не превышает запрошенное.

    ``source=forkop`` может вернуть 0 строк: ``forkop check_logs`` на этом
    роутере завершается с кодом 1 и пустым выводом (журнал без новых записей).
    Поэтому проверка «лог реально читается» делается на ``source=system``:
    у него есть fallback на ``logread``.
    """

    logs = call_tool("podkop_logs", {"lines": 7})
    assert logs["source"] == "forkop"
    assert logs["count"] == len(logs["lines"]) <= 7

    system = call_tool("podkop_logs", {"source": "system", "lines": 7})
    assert system["source"] == "system"
    assert system["count"] == len(system["lines"]) <= 7
    assert system["count"] > 0, "системный лог пуст — не работает logread"
    assert all(isinstance(line, str) and line.strip() for line in system["lines"])


# --------------------------------------------------------------------------- конфиг


def test_sections_list_contains_main(call_tool: CallTool) -> None:
    """sections_list: среди секций есть main с тем же числом доменов, что domains_list."""

    sections = call_tool("podkop_sections_list")
    by_name = {section["name"]: section for section in sections["sections"]}

    assert sections["count"] == len(sections["sections"])
    assert "main" in by_name
    main = by_name["main"]
    assert main["type"] == "section"
    assert main["domain_option"] == "domain"
    domains = call_tool("podkop_domains_list", {"section": "main"})
    assert main["domain_count"] == domains["count"]


def test_domains_list_main_is_option_text_with_42_entries(call_tool: CallTool) -> None:
    """domains_list main: Forkop 1.x хранит домены строкой (option_text), записей 42."""

    domains = call_tool("podkop_domains_list", {"section": "main"})

    assert domains["option"] == "domain"
    assert domains["storage"] == "option_text"
    assert isinstance(domains["raw"], str)
    assert domains["count"] == EXPECTED_MAIN_DOMAIN_COUNT
    assert len(domains["entries"]) == domains["count"]
    assert len(set(domains["entries"])) == domains["count"]
    assert domains["invalid"] == []
    assert domains["summary"]["domains"] == domains["count"]
    # Строковое хранение: ровно те же записи, что в сыром значении.
    assert domains["raw"].split() == domains["entries"]


def test_domains_find_matches_akamai_gethornet(call_tool: CallTool) -> None:
    """domains_find: поддомен покрыт секцией main через суффикс gethornet.com."""

    found = call_tool("podkop_domains_find", {"domain": "akamai.gethornet.com"})

    assert found["domain"] == "akamai.gethornet.com"
    assert found["matched"] is True
    assert found["matches"], "нет совпадений по секциям"
    covered = [match for match in found["matches"] if match["section"] == "main"]
    assert covered, found["matches"]
    domain = found["domain"]
    assert any(
        match["entry"].lstrip(".") in domain or domain.endswith(match["entry"].lstrip("."))
        for match in covered
    )
    assert isinstance(found["possible_lists"], list)
    assert all({"section", "list"} <= set(item) for item in found["possible_lists"])


def test_config_get_and_option_get_mask_subscription_token(call_tool: CallTool) -> None:
    """Секреты маскируются: URL подписки в config_get и в option_get без токена."""

    config = call_tool("podkop_config_get")

    subscriptions = [
        (name, section) for name, section in config["sections"].items() if section.get(".type") == "subscription_url"
    ]
    assert subscriptions, "на роутере нет секций subscription_url"
    for name, section in subscriptions:
        assert "url" in section, name
        assert _is_masked(section["url"]), f"{name}.url не маскирован: {section['url']!r}"

    # Ни в одной секции не должно остаться прокси-ссылок.
    for name, section in config["sections"].items():
        for key, value in section.items():
            assert PROXY_SCHEME_RE.search(str(value)) is None, f"{name}.{key} содержит прокси-ссылку"

    option_path = f"{config['package']}.{subscriptions[0][0]}.url"
    option = call_tool("podkop_option_get", {"path": option_path})
    assert option["exists"] is True
    assert option["is_list"] is False
    assert _is_masked(option["value"]), option["value"]


def test_option_get_section_dump_masks_secrets(call_tool: CallTool) -> None:
    """option_get по пути секции тоже обязан маскировать секреты.

    Регрессия: в проверяемой ревизии (до 2026-10-29 01:13) ``option_get`` для
    пути без опции возвращал ``section_obj.to_dict()`` как есть, и URL подписки
    уходил в ответ открытым текстом (``tools/config.py``). Сейчас маскирование
    есть — тест закрепляет контракт, чтобы утечка не вернулась.
    """

    config = call_tool("podkop_config_get")
    subscriptions = [
        name for name, section in config["sections"].items() if section.get(".type") == "subscription_url"
    ]
    assert subscriptions

    section = call_tool("podkop_option_get", {"path": f"{config['package']}.{subscriptions[0]}"})

    assert _is_masked(section["section"]["url"]), section["section"]["url"]


def test_option_get_accepts_section_path_without_package(call_tool_raw: CallTool) -> None:
    """Путь вида ``@subscription_url[0].url`` (как в config_get) принимается.

    Ограничение: ``parse_path`` требует префикс пакета, поэтому имя секции из
    ``aliases`` без пакета отвергается. Как только это исправят, тест начнёт
    проверять маскирование вместо xfail.
    """

    result = None
    try:
        result = call_tool_raw("podkop_option_get", {"path": "@subscription_url[0].url"})
    except ToolError as exc:
        pytest.xfail(
            "НАЙДЕНО (minor): podkop_option_get не принимает путь без пакета "
            "(@subscription_url[0].url), хотя этот вид имени приходит из самого конфига "
            f"(src/podkop_mcp/uci.py:32-40): {exc}"
        )
    if getattr(result, "is_error", False):
        pytest.xfail(f"podkop_option_get вернул ошибку для пути без пакета: {_result_text(result)[:200]}")
    assert _is_masked(result.structured_content["value"])


def test_domains_add_dry_run_keeps_config_identical(call_tool: CallTool, live_env: dict[str, str]) -> None:
    """dry-run domains_add: diff есть, роутер не изменён (config_get идентичен)."""

    assert live_env["PODKOP_MCP_READ_ONLY"] == "1"
    before = call_tool("podkop_config_get")
    package = before["package"]
    domains_before = call_tool("podkop_domains_list", {"section": "main"})
    new_domain = f"live-check-{os.getpid()}.example"
    assert new_domain not in domains_before["entries"]

    dry_run = call_tool("podkop_domains_add", {"section": "main", "domains": [new_domain]})

    assert dry_run["applied"] is False
    assert dry_run["dry_run"] is True
    assert dry_run["added"] == [new_domain]
    assert new_domain in dry_run["after"]
    assert dry_run["storage"] == "option_text"
    paths = [change["path"] for change in dry_run["changes"]]
    assert paths == [f"{package}.main.domain"]
    assert dry_run["change_count"] == 1
    assert any(preview.startswith(f"uci set '{package}.main.domain=") for preview in dry_run["preview"])
    assert f"uci commit {package}" in dry_run["preview"]
    # Строковое хранение не должно превращаться в UCI-список.
    assert not any("add_list" in preview for preview in dry_run["preview"])

    # Новый сервер = новое SSH-чтение конфига, без кэша Router.config.
    after = _fresh_config_get()
    assert _dump(before) == _dump(after), "dry-run изменил конфиг на роутере"

    domains_after = call_tool("podkop_domains_list", {"section": "main"})
    assert domains_after["entries"] == domains_before["entries"]
    assert new_domain not in domains_after["entries"]


# --------------------------------------------------------------------------- мониторинг


def test_monitor_snapshot_has_running_service_and_proxies(call_tool: CallTool) -> None:
    """monitor_snapshot: сервис запущен, прокси-узлы есть, часть ошибок пуста."""

    snapshot = call_tool("podkop_monitor_snapshot")

    assert snapshot["service"]["running"] == 1
    assert snapshot["proxies"]["node_count"] > 0
    assert snapshot["proxies"]["group_count"] >= 1
    assert snapshot["proxies"]["selected"], "нет выбранных прокси в группах"
    assert "proxies" not in snapshot["check_errors"]
    assert "connections" not in snapshot["check_errors"]
    assert snapshot["connections"]["count"] >= len(snapshot["connections"]["items"])
    assert len(snapshot["connections"]["items"]) <= 10


def test_connections_live_respects_limit_and_filters(call_tool: CallTool) -> None:
    """connections_live: limit ограничивает выдачу, фильтр source_ip сужает выборку."""

    live = call_tool("podkop_connections_live", {"limit": 3})

    assert live["returned"] == len(live["items"]) <= 3
    assert live["count"] >= live["returned"]
    assert live["filters"] == {"host": None, "source_ip": None, "chain": None}
    assert {"downloadTotal", "uploadTotal"} <= set(live["totals"])

    one = call_tool("podkop_connections_live", {"limit": 1})
    assert one["returned"] <= 1

    if live["items"]:
        # Фильтр по подсети, а не по конкретному IP: соединения успевают закрыться
        # между вызовами, а клиенты из той же подсети присутствуют стабильно.
        source_ip = live["items"][0]["source_ip"]
        assert source_ip, live["items"][0]
        subnet = source_ip.rsplit(".", 1)[0] + "." if source_ip.count(".") == 3 else source_ip
        filtered = call_tool("podkop_connections_live", {"source_ip": subnet, "limit": 50})
        assert filtered["filters"]["source_ip"] == subnet
        assert filtered["count"] >= 1, filtered
        for item in filtered["items"]:
            assert item["source_ip"] == subnet or item["source_ip"].startswith(subnet)


def test_proxies_list_returns_groups(call_tool: CallTool) -> None:
    """proxies_list: группы Clash с текущим выбором и списком участников."""

    proxies = call_tool("podkop_proxies_list")

    assert proxies["group_count"] == len(proxies["groups"]) > 0
    assert proxies["node_count"] == len(proxies["nodes"]) > 0
    for group in proxies["groups"]:
        assert group["name"]
        assert isinstance(group["all"], list)
        assert group["count"] == len(group["all"])
    names = {group["name"] for group in proxies["groups"]}
    assert "GLOBAL" in names

    first = proxies["groups"][0]["name"]
    single = call_tool("podkop_proxies_list", {"group": first})
    assert single["group"]["name"] == first
    assert single["all"] == single["group"]["all"]
