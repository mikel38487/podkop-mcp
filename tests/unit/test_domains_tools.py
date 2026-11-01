"""Тесты инструментов доменов на общих фейках ``tests/fakes.py`` (без SSH и сети)."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

# Конфиг pytest подхватывает tests/conftest.py, но оставляем подстраховку
# для запуска файла напрямую.
ROOT = Path(__file__).resolve().parents[2]
for _path in (ROOT / "src", ROOT):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from fakes import (  # noqa: E402
    FakeRouter,
    FakeServices,
    FakeSession,
    fail,
    json_ok,
    make_forkop1_profile,
    make_forkop2_profile,
    make_podkop_profile,
    ok,
)
from mcp.server.mcpserver.exceptions import ToolError  # noqa: E402

from podkop_mcp.tools import domains  # noqa: E402

PROFILE_FACTORIES = {
    "forkop1": make_forkop1_profile,
    "forkop2": make_forkop2_profile,
    "podkop": make_podkop_profile,
}

NSLOOKUP_FAKEIP = """Server:\t\t127.0.0.1
Address:\t127.0.0.1:53

Non-authoritative answer:
Name:\tnew.com
Address: 198.18.0.7
"""


class FakeMCP:
    """Собирает зарегистрированные инструменты по имени."""

    def __init__(self) -> None:
        self.tools: dict[str, Any] = {}

    def tool(self, *args: Any, **kwargs: Any) -> Any:
        def decorator(fn: Any) -> Any:
            self.tools[fn.__name__] = fn
            return fn

        return decorator


def make_router(
    schema: str, config_payload: dict[str, Any], **responses: Any
) -> tuple[FakeRouter, FakeSession]:
    profile = PROFILE_FACTORIES[schema]()
    session = FakeSession(responses=responses, strict=False)
    router = FakeRouter(profile=profile, config=config_payload, session=session, name="fake")
    return router, session


def tools_for(router: FakeRouter) -> dict[str, Any]:
    mcp = FakeMCP()
    domains.register(mcp, FakeServices({"router": router}))
    return mcp.tools


def forkop1_config(domain: str = "example.com existing.org") -> dict[str, Any]:
    return {
        "values": {
            "settings": {".type": "settings", ".name": "settings", ".index": 0, "config_version": "1.0.5"},
            "main": {
                ".type": "section",
                ".name": "main",
                ".index": 1,
                "enabled": "1",
                "action": "connection",
                "domain": domain,
                "community_lists": ["telegram"],
            },
            "yt": {
                ".type": "section",
                ".name": "yt",
                ".index": 2,
                "enabled": "1",
                "action": "connection",
                "domain": "youtube.com",
                "community_lists": ["youtube", "custom_set"],
            },
            "sub1": {".type": "subscription_url", ".name": "sub1", ".index": 3, "section": "main", "url": "vless://x"},
        }
    }


def forkop2_config(destinations: str = "example.com\n# комментарий\nexisting.org") -> dict[str, Any]:
    return {
        "values": {
            "settings": {".type": "settings", ".name": "settings", ".index": 0},
            "main": {
                ".type": "section",
                ".name": "main",
                ".index": 1,
                "enabled": "1",
                "destinations": destinations,
                "community_lists": ["telegram"],
            },
        }
    }


def podkop_config() -> dict[str, Any]:
    return {
        "values": {
            "settings": {".type": "settings", ".name": "settings", ".index": 0},
            "main": {
                ".type": "section",
                ".name": "main",
                ".index": 1,
                "enabled": "1",
                "connection_type": "proxy",
                "user_domains": ["example.com", "existing.org"],
                "community_lists": ["telegram"],
            },
        }
    }


# --------------------------------------------------------------------- list
def test_domains_list_forkop1_keeps_option_string() -> None:
    router, _session = make_router("forkop1", forkop1_config())
    result = tools_for(router)["podkop_domains_list"]("main")

    assert result["option"] == "domain"
    assert result["storage"] == "option_text"
    assert result["entries"] == ["example.com", "existing.org"]
    assert result["count"] == 2
    assert isinstance(result["raw"], str)
    assert result["summary"]["domains"] == 2
    assert result["invalid"] == []


def test_domains_list_podkop_returns_list_raw() -> None:
    router, _session = make_router("podkop", podkop_config())
    result = tools_for(router)["podkop_domains_list"]("main")

    assert result["option"] == "user_domains"
    assert result["storage"] == "list"
    assert result["raw"] == ["example.com", "existing.org"]


def test_domains_list_missing_section_is_tool_error() -> None:
    router, _session = make_router("forkop1", forkop1_config())
    with pytest.raises(ToolError) as excinfo:
        tools_for(router)["podkop_domains_list"]("nope")
    assert "не найдена" in str(excinfo.value)


# --------------------------------------------------------------------- add
def test_domains_add_forkop1_writes_string_option_not_list() -> None:
    router, _session = make_router("forkop1", forkop1_config())
    result = tools_for(router)["podkop_domains_add"]("main", ["new.com"])

    assert result["dry_run"] is True
    assert result["applied"] is False
    assert result["option"] == "domain"
    assert result["before"] == ["example.com", "existing.org"]
    assert result["after"] == ["example.com", "existing.org", "new.com"]
    assert result["added"] == ["new.com"]
    change = result["changes"][0]
    assert change["op"] == "set"
    assert isinstance(change["after"], str)
    assert change["after"] == "example.com existing.org new.com"
    preview = "\n".join(result["preview"])
    assert "uci set 'forkop.main.domain=example.com existing.org new.com'" in preview
    assert "add_list" not in preview


def test_domains_add_podkop_writes_uci_list() -> None:
    router, _session = make_router("podkop", podkop_config())
    result = tools_for(router)["podkop_domains_add"]("main", ["new.com"])

    assert result["storage"] == "list"
    changes = {change["path"]: change for change in result["changes"]}
    assert changes["podkop.main.user_domains"]["after"] == ["example.com", "existing.org", "new.com"]
    assert changes["podkop.main.user_domain_list_type"]["after"] == "dynamic"
    preview = "\n".join(result["preview"])
    assert "uci add_list 'podkop.main.user_domains=new.com'" in preview
    assert "user_domain_list_type" in preview


def test_domains_add_duplicates_only_reports_no_change() -> None:
    router, session = make_router("forkop1", forkop1_config())
    result = tools_for(router)["podkop_domains_add"]("main", ["example.com"])

    assert result["applied"] is False
    assert result["changed"] is False
    assert result["duplicates"] == ["example.com"]
    assert result["invalid"] == []
    assert result["message"] == "Все домены уже есть"
    assert session.argv_calls == []
    assert router.plans == []


def test_domains_add_invalid_domain_raises() -> None:
    router, _session = make_router("forkop1", forkop1_config())
    with pytest.raises(ToolError) as excinfo:
        tools_for(router)["podkop_domains_add"]("main", ["not a domain"])
    assert "Недопустимые домены: not a domain" in str(excinfo.value)


def test_domains_add_dry_run_never_calls_apply_plan() -> None:
    router, _session = make_router("forkop1", forkop1_config())
    result = tools_for(router)["podkop_domains_add"]("main", ["new.com"], apply=False)

    assert result["dry_run"] is True
    assert result["change_count"] == 1
    assert router.applied == []
    assert router.plans == []


def test_domains_add_apply_verifies_fakeip() -> None:
    router, session = make_router(
        "forkop1",
        forkop1_config(),
        nslookup=ok(NSLOOKUP_FAKEIP),
    )

    result = tools_for(router)["podkop_domains_add"]("main", ["new.com"], apply=True)

    assert result["applied"] is True
    assert len(router.plans) == 1
    assert [op.op for op in router.plans[0].ops] == ["set"]
    assert isinstance(router.plans[0].ops[0].value, str)
    assert "sleep 2" in session.scripts
    nslookup_calls = session.find_argv("nslookup")
    assert [call.args for call in nslookup_calls] == [["new.com", "127.0.0.1"]]
    verification = result["verification"]
    assert verification["domain"] == "new.com"
    assert verification["addresses"] == ["198.18.0.7"]
    assert verification["fakeip"] is True
    assert verification["note"].startswith("Домен резолвится в FakeIP")


def test_domains_add_forkop2_preserves_comments() -> None:
    router, _session = make_router("forkop2", forkop2_config())
    result = tools_for(router)["podkop_domains_add"]("main", ["new.com"])

    after = result["changes"][0]["after"]
    assert isinstance(after, str)
    assert after.splitlines() == ["example.com", "# комментарий", "existing.org", "new.com"]


def test_domains_add_podkop_apply_verifies_list_storage() -> None:
    router, _session = make_router(
        "podkop",
        podkop_config(),
        nslookup=ok(NSLOOKUP_FAKEIP),
    )

    result = tools_for(router)["podkop_domains_add"]("main", ["new.com"], apply=True)

    assert result["applied"] is True
    assert [op.op for op in router.plans[0].ops] == ["set", "set_list"]
    assert router.plans[0].ops[0].path == "podkop.main.user_domain_list_type"
    assert router.plans[0].ops[0].value == "dynamic"
    assert result["verification"]["fakeip"] is True


# ------------------------------------------------------------------ remove
def test_domains_remove_keeps_remaining_entries() -> None:
    router, _session = make_router("forkop1", forkop1_config())
    result = tools_for(router)["podkop_domains_remove"]("main", ["existing.org"])

    assert result["removed"] == ["existing.org"]
    assert result["not_found"] == []
    assert result["changes"][0]["after"] == "example.com"


def test_domains_remove_unknown_domain_reports_no_change() -> None:
    router, _session = make_router("podkop", podkop_config())
    result = tools_for(router)["podkop_domains_remove"]("main", ["absent.com"])

    assert result["applied"] is False
    assert result["changed"] is False
    assert result["not_found"] == ["absent.com"]
    assert result["removed"] == []
    assert router.plans == []


def test_domains_remove_forkop2_keeps_comments() -> None:
    router, _session = make_router("forkop2", forkop2_config())
    result = tools_for(router)["podkop_domains_remove"]("main", ["existing.org"])

    after = result["changes"][0]["after"]
    assert isinstance(after, str)
    assert after.splitlines() == ["example.com", "# комментарий"]


# ----------------------------------------------------------------- replace
def test_domains_replace_validates_and_replaces_whole_set() -> None:
    router, _session = make_router("forkop1", forkop1_config())
    result = tools_for(router)["podkop_domains_replace"]("main", ["a.com", "b.com"])

    assert result["after"] == ["a.com", "b.com"]
    assert result["added"] == ["a.com", "b.com"]
    assert result["removed"] == ["example.com", "existing.org"]
    assert result["changes"][0]["after"] == "a.com b.com"

    with pytest.raises(ToolError):
        tools_for(router)["podkop_domains_replace"]("main", ["a.com", "*bad*"])


def test_domains_replace_same_set_is_noop() -> None:
    router, _session = make_router("forkop1", forkop1_config())
    result = tools_for(router)["podkop_domains_replace"]("main", ["example.com", "existing.org"])

    assert result["applied"] is False
    assert result["changed"] is False
    assert result["message"] == "Набор доменов не изменился"
    assert router.plans == []


# -------------------------------------------------------------------- find
def test_domains_find_matches_sections_and_lists() -> None:
    router, _session = make_router("forkop1", forkop1_config())
    tools = tools_for(router)

    found = tools["podkop_domains_find"]("m.youtube.com")
    assert found["domain"] == "m.youtube.com"
    assert found["matched"] is True
    assert {"section": "yt", "entry": "youtube.com", "kind": "domain"} in found["matches"]
    assert {"section": "main", "list": "telegram"} in found["possible_lists"]
    assert {"section": "yt", "list": "custom_set"} in found["possible_lists"]

    missed = tools["podkop_domains_find"]("unknown.example")
    assert missed["matched"] is False
    assert missed["matches"] == []


# ----------------------------------------------------------------- suggest
def suggest_payload() -> dict[str, Any]:
    return {
        "downloadTotal": 10,
        "uploadTotal": 5,
        "connections": [
            {
                "metadata": {"host": "ads.example.net", "destinationIP": "1.2.3.4", "network": "tcp"},
                "chains": ["DIRECT"],
                "upload": 100,
                "download": 900,
            },
            {
                "metadata": {"host": "youtube.com", "destinationIP": "1.2.3.5"},
                "chains": ["DIRECT"],
                "upload": 10,
                "download": 10,
            },
            {
                "metadata": {"host": "1.1.1.1"},
                "chains": ["DIRECT"],
                "upload": 10,
                "download": 10,
            },
            {
                "metadata": {"host": "localhost"},
                "chains": ["DIRECT"],
                "upload": 10,
                "download": 10,
            },
            {
                "metadata": {"host": "proxied.example.net"},
                "chains": ["proxy-1"],
                "upload": 50,
                "download": 50,
            },
        ],
    }


def test_domains_suggest_skips_covered_ips_and_localhost() -> None:
    router, _session = make_router("forkop1", forkop1_config(), clash_api=json_ok(suggest_payload()))
    result = tools_for(router)["podkop_domains_suggest"]()

    assert [item["host"] for item in result["hosts"]] == ["ads.example.net"]
    item = result["hosts"][0]
    assert item["count"] == 1
    assert item["upload"] == 100
    assert item["download"] == 900
    assert item["chains"] == ["DIRECT"]
    assert item["suggested_section"] == "main"
    assert result["only_direct"] is True


def test_domains_suggest_can_include_proxied_hosts() -> None:
    router, _session = make_router("forkop1", forkop1_config(), clash_api=json_ok(suggest_payload()))
    result = tools_for(router)["podkop_domains_suggest"](only_direct=False)

    hosts = [item["host"] for item in result["hosts"]]
    assert hosts == ["ads.example.net", "proxied.example.net"]


def test_domains_suggest_respects_limit() -> None:
    router, _session = make_router("forkop1", forkop1_config(), clash_api=json_ok(suggest_payload()))
    result = tools_for(router)["podkop_domains_suggest"](only_direct=False, limit=1)

    assert len(result["hosts"]) == 1
    assert result["hosts"][0]["host"] == "ads.example.net"
    assert result["count"] == 1


# ------------------------------------------------------------------- check
def test_domain_check_reports_fakeip_coverage_and_http() -> None:
    router, session = make_router(
        "forkop1",
        forkop1_config(),
        nslookup=ok(NSLOOKUP_FAKEIP),
        curl=ok("204"),
    )
    result = tools_for(router)["podkop_domain_check"]("youtube.com")

    assert result["domain"] == "youtube.com"
    assert result["fakeip"] is True
    assert result["covered"] is True
    assert {"section": "yt", "entry": "youtube.com", "kind": "domain"} in result["matches"]
    assert result["http_code"] == 204
    assert result["http_error"] is None
    curl_calls = session.find_argv("curl")
    assert len(curl_calls) == 1
    assert "https://youtube.com/" in curl_calls[0].args


def test_domain_check_curl_failure_does_not_raise() -> None:
    router, _session = make_router(
        "forkop1",
        forkop1_config(),
        curl=fail(stderr="curl: (6) Could not resolve host", exit_code=6, stdout="000"),
    )
    result = tools_for(router)["podkop_domain_check"]("example.com", probe_http=True)

    assert result["http_code"] is None
    assert "Could not resolve host" in (result["http_error"] or "")


def test_domain_check_curl_exception_does_not_raise() -> None:
    router, _session = make_router(
        "forkop1",
        forkop1_config(),
        nslookup=ok(NSLOOKUP_FAKEIP),
        curl=RuntimeError("SSH-канал оборвался"),
    )
    result = tools_for(router)["podkop_domain_check"]("example.com", probe_http=True)

    assert result["http_code"] is None
    assert "SSH-канал оборвался" in (result["http_error"] or "")
    assert result["fakeip"] is True


def test_domain_check_rejects_invalid_domain() -> None:
    router, _session = make_router("forkop1", forkop1_config())
    with pytest.raises(ToolError):
        tools_for(router)["podkop_domain_check"]("not_a_domain")


def test_domain_check_without_probe_skips_curl() -> None:
    router, session = make_router("forkop1", forkop1_config(), nslookup=ok(""))
    result = tools_for(router)["podkop_domain_check"]("example.com", probe_http=False)

    assert result["http_code"] is None
    assert result["http_error"] is None
    assert [call.text.split()[0] for call in session.argv_calls] == ["nslookup"]


# ------------------------------------------------------- разбор ответа nslookup
def test_parse_addresses_skips_resolver_and_keeps_all_answers() -> None:
    text = (
        "Server:\t\t127.0.0.1\n"
        "Address:\t127.0.0.1:53\n"
        "\n"
        "Name:\texample.com\n"
        "Address: 1.2.3.4\n"
        "Address: 2001:db8::1\n"
        "Address: 1.2.3.4\n"
    )
    assert domains._parse_addresses(text) == ["1.2.3.4", "2001:db8::1"]


def test_fakeip_detection_uses_fakeip_ranges_only() -> None:
    assert domains._is_fakeip("198.18.0.7")
    assert domains._is_fakeip("198.19.1.1")
    assert domains._is_fakeip("fc00::1")
    # fd00::/8 — обычная ULA-сеть, не FakeIP.
    assert not domains._is_fakeip("fd00::1")
    assert not domains._is_fakeip("8.8.8.8")
