# Contributing

## Development

```powershell
cd path\to\podkop-mcp
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check src tests
```

## Rules

- Python 3.10+, stdlib + `mcp` + `paramiko` only.
- Comments and docstrings may be in Russian or English; MCP tool descriptions
  stay in English.
- Any config schema change must come with a test on the Forkop 1.x, Forkop 2.x
  and Podkop fixtures (see `tests/fixtures/`).
- Dangerous operations (UCI writes, service actions, updates) only through
  `PlanBuilder` + `preview_or_apply`, with backup and rollback.
- Secrets must not end up in logs, tests or fixtures. For real snapshots use
  `scripts/capture_fixtures.py` (sanitization), or add only manually anonymized
  data.
- Do not commit `.env`, keys, `research/` or local MCP configs.

## Commit messages

`feat: ...`, `fix: ...`, `docs: ...`, `test: ...`, `chore: ...`.
One commit — one logical change.
