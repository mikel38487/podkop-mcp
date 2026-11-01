# Архитектура

`podkop-mcp` — тонкий и безопасный мост между MCP-клиентом и роутером OpenWrt
с [Podkop](https://github.com/itdoginfo/podkop) или
[Forkop](https://github.com/ushan0v/forkop). Демон на роутере не запускается,
пакеты туда не устанавливаются.

## Схема процесса

```
MCP-клиент
   │  JSON-RPC по stdio
   ▼
┌──────────────────────────────────────────────────────┐
│ podkop-mcp (один Python-процесс на машине админа)    │
│                                                      │
│  server.py        MCPServer, регистрация инструментов│
│  tools/*.py       MCP-обёртки                        │
│  services.py      кэш Router по target               │
│  router.py        UCI, CLI, бэкапы, apply/откат      │
│  backends.py      профили Forkop 1.x / 2.x / Podkop  │
│  domains_core.py  чистый разбор доменов и покрытия   │
│  clash.py         Clash API через CLI                │
│  ssh.py           paramiko, скрипты через stdin           │
│  redaction.py     маскирование секретов              │
└──────────────────────────────────────────────────────┘
   │  SSH (ключ, проверка host key)
   ▼
OpenWrt: ubus/uci, forkop|podkop CLI, logread, /proc
```

Процесс синхронный в рамках одного вызова, держит одно SSH-соединение на
target. Команды на соединении сериализуются блокировкой; оборванное соединение
переподключается один раз.

## Чтение конфигурации

`ubus call uci get '{"config":"<pkg>"}'` возвращает всё UCI-дерево JSON с
сохранением типов (строка/список). Это основной источник. Если `ubus` недоступен,
используется `uci export` и небольшой парсер.

Поэтому сервер отдаёт **все** настройки, а не только заранее перечисленные:
generic-инструменты работают с путями `pkg.section.option`, а семантические
добавляют валидацию и форматирование под бэкенд.

## Профили бэкендов

| | Forkop 1.x | Forkop 2.x | Podkop |
|---|---|---|---|
| Определение | `forkop show_version`, `settings.config_version` | то же, major ≥ 2 | `podkop show_version` |
| Домены | `domain`, строка через пробел | `destinations`, текст по строке | `user_domains`, UCI-список |
| Списки | `community_lists` | `community_lists`, `rule_set` | `community_lists` |
| Clash | action CLI | HTTP CLI | action CLI |
| Init | `/etc/init.d/forkop` | то же | `/etc/init.d/podkop` |

Возможности берутся из help установленного CLI; инструмент с отсутствующей
командой возвращает понятную ошибку, а не угадывает.

## Путь записи

```
вызов (apply=false) → PlanBuilder
    ├── читает текущий конфиг
    ├── валидирует пути и значения
    ├── строит нормализованное after-состояние
    └── возвращает diff + предпросмотр (секреты замаскированы)

вызов (apply=true)
    ├── отказ при PODKOP_MCP_READ_ONLY=1
    ├── бэкап /etc/config/<pkg> → /etc/podkop-mcp/backups
    ├── uci set/add_list/delete + uci commit
    │     (значения экранируются; скрипт идёт в sh через stdin)
    ├── /etc/init.d/<pkg> reload, проверка exit code
    ├── опрос get_status до готовности (~35 с)
    ├── быстрые health-проверки
    └── при ошибке: восстановление бэкапа, commit, reload, отчёт
```

Ни одно пользовательское значение не склеивается с shell-строкой. В строку
попадают только валидированные UCI-пути и имя пакета бэкенда.

## Границы безопасности

- Запись — только в пакет `forkop`/`podkop`.
- Произвольный shell не выставляется: `podkop_cli` принимает только read-only
  команды из allowlist; изменяющие команды — через инструменты с бэкапом/откатом.
- Чтение файлов — только `/etc`, `/var`, `/tmp`, `/proc`, `/sys`, `/rom`,
  `/usr/share`, без `..`.
- Секреты маскируются по умолчанию; `reveal_secrets=true` — явное исключение.
- Host key проверяется; `accept-new` только добавляет неизвестный ключ, но не
  прощает несовпадение.

## Модель ошибок

- `PodkopError` и наследники — ожидаемые доменные ошибки.
- `guard` превращает их в MCP `ToolError` с понятным текстом.
- Неожиданные исключения пишутся в stderr (stdout — канал протокола MCP) и
  возвращаются как generic internal error.

## Уровни тестирования

1. **Чистые unit-тесты**: разбор конфига, UCI, семантика доменов, redaction,
   diff/план.
2. **Fake-роутер**: инструменты на in-memory `Router`/`Services`, без SSH.
3. **MCP protocol smoke**: запуск реального сервера, `initialize`, `tools/list`,
   вызов `podkop_doctor`.
4. **Live read-only**: реальный роутер, opt-in `PODKOP_MCP_LIVE=1`; только чтение,
   ни одного `apply=true`.

Запись на живом production-роутере тестами не выполняется: пользователь
проверяет план с `apply=false` и применяет осознанно.
