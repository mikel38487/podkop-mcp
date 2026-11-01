"""Аргументы командной строки ``podkop-mcp``.

``--version`` и ``--help`` должны завершать процесс до загрузки настроек и
запуска stdio-сервера, а запуск без аргументов — работать как раньше.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import podkop_mcp
from podkop_mcp import __main__ as cli
from podkop_mcp.errors import ConfigError


def test_version_prints_version_and_exits_zero(capsys) -> None:
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["--version"])

    assert excinfo.value.code == 0
    captured = capsys.readouterr()
    assert captured.out == f"podkop-mcp {podkop_mcp.__version__}\n"
    assert captured.err == ""


def test_help_prints_usage_with_both_options_and_exits_zero(capsys) -> None:
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["--help"])

    assert excinfo.value.code == 0
    out = capsys.readouterr().out
    assert "usage: podkop-mcp" in out
    assert "--version" in out
    assert "-h, --help" in out


def test_version_does_not_create_server(monkeypatch, capsys) -> None:
    def forbidden(*args, **kwargs):
        raise AssertionError("--version must not load settings or create the server")

    monkeypatch.setattr(cli, "load_settings", forbidden)
    monkeypatch.setattr(cli, "create_server", forbidden)

    with pytest.raises(SystemExit) as excinfo:
        cli.main(["--version"])

    assert excinfo.value.code == 0
    assert capsys.readouterr().out == f"podkop-mcp {podkop_mcp.__version__}\n"


def test_no_arguments_starts_server(monkeypatch) -> None:
    settings = SimpleNamespace(log_level="INFO")
    started = []

    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    monkeypatch.setattr(cli, "create_server", lambda s: SimpleNamespace(run=lambda: started.append(s)))

    cli.main([])

    assert started == [settings]


def test_config_error_exits_two_with_same_message(monkeypatch, capsys) -> None:
    def broken_settings():
        raise ConfigError("нет хоста")

    monkeypatch.setattr(cli, "load_settings", broken_settings)

    with pytest.raises(SystemExit) as excinfo:
        cli.main([])

    assert excinfo.value.code == 2
    assert capsys.readouterr().err == "podkop-mcp: нет хоста\n"
