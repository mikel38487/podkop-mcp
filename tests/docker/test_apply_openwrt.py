"""Интеграционные тесты записи на OpenWrt-контейнере.

Проверяется реальный ``uci``, реальные cp/sha256sum и наш apply/rollback.
Настоящие forkop/sing-box/nft не запускаются: CLI и init-скрипт — заглушки.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from podkop_mcp.config import Settings, TargetConfig
from podkop_mcp.domains_core import merge_entries, render_value
from podkop_mcp.errors import BackendError
from podkop_mcp.router import Router

DOCKER_DIR = Path(__file__).resolve().parent
if str(DOCKER_DIR) not in sys.path:
    sys.path.insert(0, str(DOCKER_DIR))

from session import DockerSession  # noqa: E402

pytestmark = pytest.mark.docker


def _uci(session: DockerSession, path: str) -> str:
    return session.run_argv("uci", ["get", path], check=True).stdout.strip()


def _domain_entries(router: Router) -> list[str]:
    return router.read_domains("main")["entries"]


def test_detects_forkop1(emu_router: Router) -> None:
    profile = emu_router.profile

    assert profile.kind == "forkop"
    assert profile.schema == "forkop1"
    assert profile.config_version == "1.0.5"
    assert {section.name for section in emu_router.config().top_sections()} >= {"main", "AI"}


def test_add_domain_keeps_domain_option_string(emu_router: Router) -> None:
    before = _uci(emu_router.session, "forkop.main.domain")
    merged, added, duplicates, invalid = merge_entries(_domain_entries(emu_router), ["new.example"])
    assert added == ["new.example"]
    assert duplicates == []
    assert invalid == []

    builder = emu_router.builder("docker-add")
    builder.set_option("main", "domain", render_value(merged, "option_text"))
    plan = builder.build()
    assert "add_list" not in "\n".join(plan.preview)

    emu_router.apply_plan(plan)

    after = _uci(emu_router.session, "forkop.main.domain")
    assert after != before
    assert "new.example" in after
    assert " " in after
    assert emu_router.session.run("test -f /tmp/forkop-reload").ok
    assert emu_router.list_backups()


def test_failed_reload_restores_backup(emu_router: Router) -> None:
    original = _uci(emu_router.session, "forkop.main.domain")
    emu_router.session.run("touch /tmp/forkop-fail-reload", check=True)

    builder = emu_router.builder("docker-rollback")
    builder.set_option("main", "domain", "should-not-stick.example")
    with pytest.raises(BackendError, match="не поднялся|reload|откат"):
        emu_router.apply_plan(builder.build())

    assert _uci(emu_router.session, "forkop.main.domain") == original
    assert emu_router.list_backups()
    emu_router.session.run("rm -f /tmp/forkop-fail-reload")


def test_backup_and_restore_roundtrip(emu_router: Router) -> None:
    original = _uci(emu_router.session, "forkop.main.domain")
    backup = emu_router.backup("roundtrip")

    emu_router.session.run("uci set forkop.main.domain='changed.example'; uci commit forkop", check=True)
    assert _uci(emu_router.session, "forkop.main.domain") == "changed.example"

    result = emu_router.restore_backup(backup["id"])
    assert result["ok"] is True
    assert _uci(emu_router.session, "forkop.main.domain") == original


def test_forkop2_destinations(emu: DockerSession) -> None:
    emu.run(
        "echo 2.0.0 > /etc/forkop-version\n"
        "uci set forkop.settings.config_version='2.0.0'\n"
        "uci set forkop.main.destinations='example.com'\n"
        "uci delete forkop.main.domain\n"
        "uci commit forkop",
        check=True,
    )
    target = TargetConfig(name="emu", host="docker-emulator", user="root", host_key_policy="ignore")
    router = Router(emu, target, Settings(targets={target.name: target}, backup_dir="/tmp/podkop-mcp/backups"))
    assert router.profile.schema == "forkop2"
    data = router.read_domains("main")
    assert data["option"] == "destinations"
    assert data["storage"] == "option_multiline"

    merged = [*data["entries"], "new.example"]
    builder = router.builder("docker-forkop2")
    builder.set_option("main", "destinations", render_value(merged, "option_multiline", original=data["raw"]))
    router.apply_plan(builder.build())

    assert "new.example" in _uci(emu, "forkop.main.destinations")

