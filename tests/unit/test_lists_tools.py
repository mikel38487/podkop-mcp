"""Тесты инструментов community lists на общих фейках ``tests/fakes.py``."""

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

from fakes import FakeRouter, FakeServices, make_forkop1_profile  # noqa: E402
from mcp.server.mcpserver.exceptions import ToolError  # noqa: E402

from podkop_mcp.tools import lists  # noqa: E402


class FakeMCP:
    """Собирает зарегистрированные инструменты по имени."""

    def __init__(self) -> None:
        self.tools: dict[str, Any] = {}

    def tool(self, *args: Any, **kwargs: Any) -> Any:
        def decorator(fn: Any) -> Any:
            self.tools[fn.__name__] = fn
            return fn

        return decorator


def make_router(config_payload: dict[str, Any]) -> FakeRouter:
    return FakeRouter(profile=make_forkop1_profile(), config=config_payload, name="fake")


def tools_for(router: FakeRouter) -> dict[str, Any]:
    mcp = FakeMCP()
    lists.register(mcp, FakeServices({"router": router}))
    return mcp.tools


def config_with(selected: list[str], *, yt: list[str] | None = None) -> dict[str, Any]:
    sections: dict[str, Any] = {
        "settings": {".type": "settings", ".name": "settings", ".index": 0},
        "main": {
            ".type": "section",
            ".name": "main",
            ".index": 1,
            "enabled": "1",
            "domain": "example.com",
            "community_lists": list(selected),
        },
    }
    if yt is not None:
        sections["yt"] = {
            ".type": "section",
            ".name": "yt",
            ".index": 2,
            "enabled": "1",
            "domain": "youtube.com",
            "community_lists": list(yt),
        }
    return {"values": sections}


# -------------------------------------------------------------- каталог имён
def test_known_lists_sorted_unique_and_complete() -> None:
    known = lists.KNOWN_COMMUNITY_LISTS
    assert isinstance(known, list)
    assert known == sorted(set(known))
    required = {
        "telegram",
        "tiktok",
        "geoblock",
        "block",
        "porn",
        "news",
        "anime",
        "meta",
        "twitter",
        "hdrezka",
        "google_ai",
        "supercell",
        "discord",
        "youtube",
        "github",
        "russia_inside",
        "cloudflare",
        "amazon",
        "apple",
        "microsoft",
        "steam",
        "epicgames",
        "spotify",
        "twitch",
        "openai",
        "anthropic",
        "google",
        "youtube_music",
        "instagram",
        "facebook",
        "whatsapp",
    }
    assert required <= set(known)


# -------------------------------------------------------------------- get
def test_get_single_section_returns_selected_and_unknown() -> None:
    router = make_router(config_with(["telegram", "custom_set"]))
    result = tools_for(router)["podkop_community_lists_get"]("main")

    assert result["section"] == "main"
    assert result["selected"] == ["telegram", "custom_set"]
    assert result["unknown"] == ["custom_set"]
    assert result["count"] == 2
    assert result["available"] == lists.KNOWN_COMMUNITY_LISTS


def test_get_all_sections_returns_map() -> None:
    router = make_router(config_with(["telegram"], yt=["youtube"]))
    result = tools_for(router)["podkop_community_lists_get"]()

    assert result["count"] == 2
    assert result["sections"]["main"]["selected"] == ["telegram"]
    assert result["sections"]["yt"]["selected"] == ["youtube"]
    assert result["sections"]["yt"]["unknown"] == []


def test_get_missing_section_raises_tool_error() -> None:
    router = make_router(config_with(["telegram"]))
    with pytest.raises(ToolError) as excinfo:
        tools_for(router)["podkop_community_lists_get"]("nope")
    assert "не найдена" in str(excinfo.value)


# ----------------------------------------------------------------- update
def test_update_add_dry_run_writes_whole_list() -> None:
    router = make_router(config_with(["telegram"]))
    result = tools_for(router)["podkop_community_lists_update"]("main", add=["discord", "youtube"])

    assert result["applied"] is False
    assert result["dry_run"] is True
    assert result["selected_before"] == ["telegram"]
    assert result["selected_after"] == ["telegram", "discord", "youtube"]
    assert result["added"] == ["discord", "youtube"]
    assert result["removed"] == []
    assert result["changes"][0]["after"] == ["telegram", "discord", "youtube"]
    preview = "\n".join(result["preview"])
    assert "uci delete 'forkop.main.community_lists'" in preview
    assert "uci add_list 'forkop.main.community_lists=discord'" in preview


def test_update_add_and_remove_keeps_order() -> None:
    router = make_router(config_with(["telegram", "youtube"]))
    result = tools_for(router)["podkop_community_lists_update"](
        "main",
        add=["discord"],
        remove=["telegram"],
    )

    assert result["selected_before"] == ["telegram", "youtube"]
    assert result["selected_after"] == ["youtube", "discord"]
    assert result["added"] == ["discord"]
    assert result["removed"] == ["telegram"]


def test_update_deduplicates_and_reports_unknown() -> None:
    router = make_router(config_with(["telegram"]))
    result = tools_for(router)["podkop_community_lists_update"](
        "main",
        add=["custom_set", "custom_set"],
    )

    assert result["selected_after"] == ["telegram", "custom_set"]
    assert result["added"] == ["custom_set"]
    assert result["unknown"] == ["custom_set"]


def test_update_invalid_name_raises() -> None:
    router = make_router(config_with(["telegram"]))
    with pytest.raises(ToolError) as excinfo:
        tools_for(router)["podkop_community_lists_update"]("main", add=["Telegram!"])
    assert "Недопустимые имена community lists" in str(excinfo.value)
    assert "^[a-z0-9_]+$" in str(excinfo.value)


def test_update_empty_name_raises_instead_of_silent_skip() -> None:
    router = make_router(config_with(["telegram"]))
    with pytest.raises(ToolError) as excinfo:
        tools_for(router)["podkop_community_lists_update"]("main", add=["  "])
    assert "Недопустимые имена community lists" in str(excinfo.value)
    assert router.plans == []


def test_update_without_changes_is_noop() -> None:
    router = make_router(config_with(["telegram"]))
    result = tools_for(router)["podkop_community_lists_update"]("main", add=["telegram"])

    assert result["applied"] is False
    assert result["changed"] is False
    assert result["added"] == []
    assert result["removed"] == []
    assert result["message"] == "Набор community lists не изменился"
    assert router.applied == []
    assert router.plans == []


def test_update_empty_arguments_raise() -> None:
    router = make_router(config_with(["telegram"]))
    with pytest.raises(ToolError):
        tools_for(router)["podkop_community_lists_update"]("main")


def test_update_apply_sets_uci_list() -> None:
    router = make_router(config_with(["telegram"]))
    result = tools_for(router)["podkop_community_lists_update"](
        "main",
        add=["discord"],
        apply=True,
    )

    assert result["applied"] is True
    assert len(router.plans) == 1
    ops = router.plans[0].ops
    assert [op.op for op in ops] == ["set_list"]
    assert ops[0].value == ["telegram", "discord"]
    assert result["selected_after"] == ["telegram", "discord"]
