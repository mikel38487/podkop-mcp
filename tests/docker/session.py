"""Транспорт «роутер в Docker»: docker exec вместо SSH.

Нужен для проверки реальных команд ``uci``/``cp``/``sha256sum`` и
последовательности apply/rollback без риска для живого роутера.

Для локальной среды без доступа к Docker-сокету (sandbox named pipe)
есть режим ``WslChrootSession``: тот же rootfs, но команды идут через
``wsl chroot``.
"""

from __future__ import annotations

import subprocess
from collections.abc import Sequence

from podkop_mcp.errors import RemoteCommandError
from podkop_mcp.ssh import SSHResult


class DockerSession:
    def __init__(self, container: str, *, timeout: float = 60.0) -> None:
        self.container = container
        self.timeout = timeout

    def _exec(self, argv: Sequence[str], *, timeout: float | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["docker", "exec", self.container, *argv],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout or self.timeout,
            check=False,
        )

    def run(self, script: str, *, timeout: float | None = None, check: bool = False) -> SSHResult:
        result = self._exec(["sh", "-c", script], timeout=timeout)
        payload = SSHResult(
            stdout=result.stdout or "",
            stderr=result.stderr or "",
            exit_code=result.returncode,
            command=script[:400],
        )
        if check and not payload.ok:
            raise RemoteCommandError(
                f"docker exec завершился с кодом {payload.exit_code}: {payload.tail()}",
                exit_code=payload.exit_code,
                stdout=payload.stdout,
                stderr=payload.stderr,
            )
        return payload

    def run_argv(
        self,
        program: str,
        args: Sequence[str],
        *,
        timeout: float | None = None,
        check: bool = False,
    ) -> SSHResult:
        result = self._exec([program, *[str(arg) for arg in args]], timeout=timeout)
        payload = SSHResult(
            stdout=result.stdout or "",
            stderr=result.stderr or "",
            exit_code=result.returncode,
            command=f"{program} {' '.join(map(str, args))}"[:400],
        )
        if check and not payload.ok:
            raise RemoteCommandError(
                f"docker exec {program} завершился с кодом {payload.exit_code}: {payload.tail()}",
                exit_code=payload.exit_code,
                stdout=payload.stdout,
                stderr=payload.stderr,
            )
        return payload

    def close(self) -> None:
        """Соединения нет — совместимость с SSHSession."""


class WslChrootSession:
    """Тот же интерфейс, но команды выполняются в WSL через ``chroot``."""

    def __init__(self, rootfs: str, *, distro: str = "Ubuntu", timeout: float = 60.0) -> None:
        self.rootfs = rootfs
        self.distro = distro
        self.timeout = timeout

    def _exec(self, argv: Sequence[str], *, timeout: float | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["wsl", "-d", self.distro, "--", "chroot", self.rootfs, *argv],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout or self.timeout,
            check=False,
        )

    def run(self, script: str, *, timeout: float | None = None, check: bool = False) -> SSHResult:
        result = self._exec(["sh", "-c", script], timeout=timeout)
        payload = SSHResult(
            stdout=result.stdout or "",
            stderr=result.stderr or "",
            exit_code=result.returncode,
            command=script[:400],
        )
        if check and not payload.ok:
            raise RemoteCommandError(
                f"wsl chroot завершился с кодом {payload.exit_code}: {payload.tail()}",
                exit_code=payload.exit_code,
                stdout=payload.stdout,
                stderr=payload.stderr,
            )
        return payload

    def run_argv(
        self,
        program: str,
        args: Sequence[str],
        *,
        timeout: float | None = None,
        check: bool = False,
    ) -> SSHResult:
        result = self._exec([program, *[str(arg) for arg in args]], timeout=timeout)
        payload = SSHResult(
            stdout=result.stdout or "",
            stderr=result.stderr or "",
            exit_code=result.returncode,
            command=f"{program} {' '.join(map(str, args))}"[:400],
        )
        if check and not payload.ok:
            raise RemoteCommandError(
                f"wsl chroot {program} завершился с кодом {payload.exit_code}: {payload.tail()}",
                exit_code=payload.exit_code,
                stdout=payload.stdout,
                stderr=payload.stderr,
            )
        return payload

    def close(self) -> None:
        """Соединения нет — совместимость с SSHSession."""


class LocalChrootSession(WslChrootSession):
    """Chroot напрямую — для запуска тестов внутри Linux/WSL."""

    def __init__(self, rootfs: str, *, timeout: float = 60.0) -> None:
        super().__init__(rootfs, distro="", timeout=timeout)

    def _exec(self, argv: Sequence[str], *, timeout: float | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["chroot", self.rootfs, *argv],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout or self.timeout,
            check=False,
        )


