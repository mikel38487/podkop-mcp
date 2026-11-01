# Contributing

## Разработка

```powershell
cd path\to\podkop-mcp
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check src tests
```

## Правила

- Python 3.10+, только stdlib + `mcp` + `paramiko`.
- Комментарии и docstring — по-русски; описания MCP-инструментов — по-английски.
- Любое изменение схемы конфига должно сопровождаться тестом на фикстурах
  Forkop 1.x, Forkop 2.x и Podkop (см. `tests/fixtures/`).
- Опасные операции (запись UCI, сервис, обновления) — только через
  `PlanBuilder` + `preview_or_apply`, с бэкапом и откатом.
- Секреты не попадают в логи, тесты и фикстуры. Для реальных снимков используйте
  `scripts/capture_fixtures.py` (санитизация) или добавляйте только вручную
  обезличенные данные.
- Не коммитьте `.env`, ключи, `research/` и локальные конфиги MCP.

## Commit messages

`feat: ...`, `fix: ...`, `docs: ...`, `test: ...`, `chore: ...`.
Один коммит — одна логическая правка.
