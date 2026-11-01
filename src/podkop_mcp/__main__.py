"""Точка входа ``python -m podkop_mcp`` / ``podkop-mcp``."""

from __future__ import annotations

import argparse
import logging
import sys

from . import __version__
from .config import load_settings
from .errors import ConfigError
from .server import create_server

PROG = "podkop-mcp"


def build_parser() -> argparse.ArgumentParser:
    """Парсер аргументов командной строки.

    ``--version`` и ``--help`` обрабатывает сам ``argparse``: процесс
    завершается до загрузки настроек, поэтому сервер не запускается.
    """

    parser = argparse.ArgumentParser(
        prog=PROG,
        description="MCP server for managing Podkop and Forkop on OpenWrt over SSH.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"{PROG} {__version__}",
        help="print the version and exit",
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    build_parser().parse_args(argv)

    try:
        settings = load_settings()
    except ConfigError as exc:
        print(f"{PROG}: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc

    logging.basicConfig(
        stream=sys.stderr,
        level=getattr(logging, settings.log_level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    server = create_server(settings)
    server.run()


if __name__ == "__main__":
    main()
