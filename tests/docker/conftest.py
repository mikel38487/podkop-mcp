"""Фикстуры Docker-эмулятора OpenWrt."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

from podkop_mcp.config import Settings, TargetConfig
from podkop_mcp.router import Router

DOCKER_DIR = Path(__file__).resolve().parent
if str(DOCKER_DIR) not in sys.path:
    sys.path.insert(0, str(DOCKER_DIR))

from session import DockerSession, LocalChrootSession, WslChrootSession  # noqa: E402

pytestmark = pytest.mark.docker

IMAGE = "podkop-mcp-emu:test"


def _chroot_root() -> str | None:
    return os.environ.get("PODKOP_MCP_EMU_ROOT") or None


def _reset_chroot(session: WslChrootSession) -> None:
    config = (DOCKER_DIR / "forkop-config").read_text(encoding="utf-8")
    session.run(
        "cat > /etc/config/forkop <<'PODKOP_EMU_CONFIG'\n"
        + config.rstrip("\n")
        + "\nPODKOP_EMU_CONFIG\n"
        + "echo 1.0.5 > /etc/forkop-version\n"
        + "rm -rf /tmp/podkop-mcp/backups /tmp/forkop-reload /tmp/forkop-fail-reload /tmp/forkop-stopped\n"
        + "uci commit forkop\n"
        + "sync",
        check=True,
    )


def _docker_ready() -> bool:
    if not shutil.which("docker"):
        return False
    try:
        result = subprocess.run(
            ["docker", "info"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


@pytest.fixture(scope="session")
def emu_image() -> str:
    if _chroot_root():
        return "chroot"
    if not _docker_ready():
        pytest.skip("Docker daemon недоступен")
    build = subprocess.run(
        ["docker", "build", "-q", "-t", IMAGE, str(DOCKER_DIR)],
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    if build.returncode != 0:
        pytest.skip(f"Не удалось собрать образ: {build.stderr[-2000:]}")
    return IMAGE


@pytest.fixture()
def emu(emu_image: str):
    root = _chroot_root()
    if root:
        if os.name == "posix" or os.environ.get("PODKOP_MCP_EMU_LOCAL_CHROOT"):
            session = LocalChrootSession(root)
        else:
            session = WslChrootSession(root, distro=os.environ.get("PODKOP_MCP_EMU_WSL_DISTRO", "Ubuntu"))
        _reset_chroot(session)
        try:
            yield session
        finally:
            pass
        return

    name = f"podkop-mcp-emu-{uuid.uuid4().hex[:10]}"
    run = subprocess.run(
        ["docker", "run", "-d", "--name", name, emu_image],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    if run.returncode != 0:
        pytest.skip(f"Не удалось запустить контейнер: {run.stderr[-2000:]}")
    session = DockerSession(name)
    try:
        yield session
    finally:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True, text=True, timeout=60, check=False)


@pytest.fixture()
def emu_router(emu) -> Router:
    target = TargetConfig(
        name="emu",
        host="docker-emulator",
        user="root",
        host_key_policy="ignore",
    )
    settings = Settings(
        targets={target.name: target},
        default_target=target.name,
        backup_dir="/tmp/podkop-mcp/backups",
        backup_keep=5,
    )
    return Router(emu, target, settings, name=target.name)
