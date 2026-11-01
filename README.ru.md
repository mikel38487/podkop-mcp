# podkop-mcp

<!-- mcp-name: io.github.mikel38487/podkop-mcp -->

[![CI](https://github.com/mikel38487/podkop-mcp/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/mikel38487/podkop-mcp/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/podkop-mcp)](https://pypi.org/project/podkop-mcp/)
[![Версии Python](https://img.shields.io/badge/python-3.10%20%7C%203.11%20%7C%203.12%20%7C%203.13%20%7C%203.14-blue)](https://github.com/mikel38487/podkop-mcp/blob/main/pyproject.toml)
[![Лицензия: MIT](https://img.shields.io/badge/license-MIT-green)](https://github.com/mikel38487/podkop-mcp/blob/main/LICENSE)

**MCP-сервер для управления [Podkop](https://github.com/itdoginfo/podkop) и
[Forkop](https://github.com/ushan0v/forkop) на OpenWrt по SSH.**

Больше не нужно вручную править UCI, помнить ловушку `uci add_list` и
оборачивать команды в base64 для PowerShell. Попросите MCP-клиента добавить
домены, показать секции, посмотреть живые соединения, перезагрузить сервис и
откатить неудачное изменение — с dry-run, бэкапами и маскированием секретов.

> Статус: alpha. Live read-only проверено на Forkop 1.0.5 и Forkop 2.0.0
> (OpenWrt 25.12.6). Запись/apply проверены в одноразовых OpenWrt-контейнерах
> с реальными Forkop 2.0.0 и Podkop 0.7.24. Профиль бэкенда перепроверяется
> каждые `PODKOP_MCP_PROFILE_TTL` секунд (по умолчанию 30).

## Зачем

Ручное управление Podkop/Forkop легко ломает конфиг:

- Forkop 1.x хранит домены **одной строкой** (`section.domain`). Один
  `uci add_list` превращает поле в список точных доменов — поддомены перестают
  маршрутизироваться.
- Forkop 2.x использует многострочный `section.destinations` с комментариями.
- Podkop использует настоящий UCI-список (`section.user_domains`).
- Forkop перезагружается асинхронно; неудачный reload нужно поймать и откатить.
- URL подписок и proxy-строки — секреты, которые попадают в логи.

В отличие от MCP-серверов, которые работают на самом роутере, `podkop-mcp`
запускается на машине администратора, ходит на OpenWrt по SSH и понимает
семантику Podkop/Forkop: dry-run, бэкапы, откат и маскирование секретов. Всё
это спрятано за типизированными MCP-инструментами.

## Возможности

- **SSH-транспорт**: ключ из параметров MCP (файл, inline base64/PEM,
  `~/.ssh/config`-алиас), проверка host key, keepalive, таймауты.
- **Определение бэкенда**: Forkop 1.x, Forkop 2.x, Podkop; возможности — из
  установленного CLI.
- **Полный доступ к UCI** через JSON `ubus call uci get` и `uci` для записи.
  Любая опция; пакет ограничен `forkop`/`podkop`.
- **Домены** с сохранением формата бэкенда: add/remove/replace, поиск покрытия,
  подсказки по живым соединениям, проверка FakeIP.
- **Секции**: список, просмотр, создание, изменение, включение/выключение,
  удаление с каскадом дочерних подписок/URLTest.
- **Подписки и прокси**: список, добавление, удаление, обновление, задержки,
  выбор прокси (Forkop 2.x).
- **Мониторинг**: статус, здоровье, логи, соединения, скорость, задержки.
- **Безопасность**: изменения по умолчанию dry-run; `apply=true` делает бэкап,
  commit, reload и проверку; при ошибке — автооткат. `PODKOP_MCP_READ_ONLY=1`
  запрещает запись.
- **Секреты маскируются** по умолчанию; raw-экспорт — только с `reveal_secrets=true`.
- **Двуязычная документация** (RU/EN) и CI.

## Матрица поддержки

| Возможность | Forkop 1.x | Forkop 2.x | Podkop |
|---|---|---|---|
| UCI-пакет | `forkop` | `forkop` | `podkop` |
| Опция доменов | `domain` (строка) | `destinations` (текст) | `user_domains` (list) |
| Community lists | да | да | да |
| Rule sets | нет | да | да |
| Clash API | action CLI | HTTP CLI | action CLI |
| Выбор прокси в runtime | — | да | — |
| Мониторинг | да | да | да |

## Установка

**Требования:**

- Python 3.10+ (CI проверяет 3.10–3.14)
- OpenWrt с установленным Forkop или Podkop
- SSH-доступ к роутеру по ключу

**Установка из PyPI:**

```bash
pip install podkop-mcp
# или запуск без установки
uvx podkop-mcp
```

**Установка из исходников:**

```bash
git clone https://github.com/mikel38487/podkop-mcp.git
cd podkop-mcp
python -m venv .venv
. .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e .
```

**Настройте MCP-клиент** (Claude Desktop, Cursor, DSH и т.п.):

```json
{
  "mcpServers": {
    "podkop": {
      "command": "podkop-mcp",
      "env": {
        "PODKOP_MCP_SSH_HOST": "192.168.1.1",
        "PODKOP_MCP_SSH_USER": "root",
        "PODKOP_MCP_SSH_KEY_PATH": "C:/Users/<user>/.ssh/router_key",
        "PODKOP_MCP_KNOWN_HOSTS": "C:/Users/<user>/.ssh/known_hosts",
        "PODKOP_MCP_HOST_KEY_POLICY": "reject"
      }
    }
  }
}
```

Или через SSH-алиас:

```json
"env": {
  "PODKOP_MCP_SSH_CONFIG_HOST": "router-home",
  "PODKOP_MCP_SSH_KEY_PATH": "C:/Users/<user>/.ssh/router_key",
  "PODKOP_MCP_HOST_KEY_POLICY": "reject"
}
```

Политика host key по умолчанию — `reject`. Сначала добавьте ключ роутера в
`known_hosts` (например, обычным `ssh`-входом). `accept-new` — разовое
удобство TOFU для нового роутера; от MITM при первом подключении оно не
защищает.

Примеры запросов:

- «Запусти `podkop_doctor` и покажи версию бэкенда».
- «Добавь `example.com` и `cdn.example.com` в `main`, сначала покажи diff».
- «Покажи секции и количество доменов».
- «Какие хосты в живых соединениях не покрыты ни одной секцией?»
- «Покажи трафик за 2 секунды и топ клиентов».
- «Выключи секцию `youtube` и перезагрузи сервис».

## Документация

- [Справочник инструментов](https://github.com/mikel38487/podkop-mcp/blob/main/docs/tools.ru.md) · [Tool reference](https://github.com/mikel38487/podkop-mcp/blob/main/docs/tools.md)
- [Архитектура](https://github.com/mikel38487/podkop-mcp/blob/main/docs/architecture.ru.md) · [Architecture](https://github.com/mikel38487/podkop-mcp/blob/main/docs/architecture.md)
- [Совместимость](https://github.com/mikel38487/podkop-mcp/blob/main/docs/compatibility.ru.md) · [Compatibility](https://github.com/mikel38487/podkop-mcp/blob/main/docs/compatibility.md)
- [Диагностика](https://github.com/mikel38487/podkop-mcp/blob/main/docs/troubleshooting.ru.md) · [Troubleshooting](https://github.com/mikel38487/podkop-mcp/blob/main/docs/troubleshooting.md)
- [Тестирование в Docker](https://github.com/mikel38487/podkop-mcp/blob/main/docs/testing.ru.md) · [Testing in Docker](https://github.com/mikel38487/podkop-mcp/blob/main/docs/testing.md)
- [Безопасность](https://github.com/mikel38487/podkop-mcp/blob/main/docs/safety.ru.md) · [Security](https://github.com/mikel38487/podkop-mcp/blob/main/docs/safety.md)
- [Политика безопасности](https://github.com/mikel38487/podkop-mcp/blob/main/SECURITY.ru.md), [участие](https://github.com/mikel38487/podkop-mcp/blob/main/CONTRIBUTING.ru.md)

## Как работает запись

```
dry-run (по умолчанию)
  → чтение конфига, нормализованный diff, команды uci

apply=true
  → cp /etc/config/<pkg> /etc/podkop-mcp/backups/<ts>-<reason>.uci
  → uci set/add_list/delete (только forkop/podkop)
  → uci commit
  → /etc/init.d/<pkg> reload
  → опрос get_status до ~35 с
  → при ошибке: восстановление бэкапа, commit, reload
  → diff, id бэкапа, health-summary
```

Пользовательские значения не подставляются в shell-строку: они экранируются
через `shlex`, а скрипт передаётся в `sh` через stdin. Пути валидируются;
`base64` на роутере не требуется.

## Инструменты

| Группа | Инструменты |
|---|---|
| Система | `podkop_doctor`, `podkop_status`, `podkop_system_info`, `podkop_health`, `podkop_logs`, `podkop_cli`, `podkop_core_features`, `podkop_import_check`, `podkop_installer_status`, `podkop_installer_log` |
| Конфиг | `podkop_config_get`, `podkop_config_export`, `podkop_option_get/set/delete`, `podkop_backup_create/list/restore` |
| Секции | `podkop_sections_list`, `podkop_section_get/create/update/delete/enable`, `podkop_tunnel_create`, `podkop_child_create` |
| Домены | `podkop_domains_list/add/remove/replace/find/suggest`, `podkop_domain_check` |
| Списки | `podkop_community_lists_get/update` |
| Подключения | `podkop_connections_config`, `podkop_subscription_add/delete/update`, `podkop_proxies_list`, `podkop_proxy_select/test` |
| Мониторинг | `podkop_monitor_snapshot`, `podkop_connections_live`, `podkop_traffic` |
| Сервис | `podkop_service_action`, `podkop_lists_update` |

Полный справочник: [docs/tools.ru.md](https://github.com/mikel38487/podkop-mcp/blob/main/docs/tools.ru.md).

## Переменные окружения

| Переменная | По умолчанию | Что значит |
|---|---|---|
| `PODKOP_MCP_SSH_HOST` | — | Адрес роутера |
| `PODKOP_MCP_SSH_PORT` | `22` | SSH-порт |
| `PODKOP_MCP_SSH_USER` | `root` | SSH-пользователь |
| `PODKOP_MCP_SSH_KEY_PATH` | — | Путь к приватному ключу |
| `PODKOP_MCP_SSH_KEY` | — | Ключ inline (PEM) |
| `PODKOP_MCP_SSH_KEY_B64` | — | Ключ inline, base64 |
| `PODKOP_MCP_SSH_KEY_PASSPHRASE` | — | Passphrase ключа |
| `PODKOP_MCP_SSH_PASSWORD` | — | Пароль вместо ключа (секрет: передавайте через окружение, не коммитьте) |
| `PODKOP_MCP_SSH_CONFIG_HOST` | — | Алиас из `~/.ssh/config` |
| `PODKOP_MCP_KNOWN_HOSTS` | `~/.ssh/known_hosts` | Файл host key |
| `PODKOP_MCP_HOST_KEY_POLICY` | `reject` | `reject`, `accept-new`, `ignore` |
| `PODKOP_MCP_CONNECT_TIMEOUT` | `8` | Таймаут SSH-подключения, секунды |
| `PODKOP_MCP_COMMAND_TIMEOUT` | `45` | Таймаут команды, секунды |
| `PODKOP_MCP_BACKEND` | `auto` | `auto`, `forkop` или `podkop`; нужный CLI должен быть установлен |
| `PODKOP_MCP_READ_ONLY` | `0` | Запретить запись |
| `PODKOP_MCP_BACKUP_DIR` | `/etc/podkop-mcp/backups` | Бэкапы на роутере |
| `PODKOP_MCP_BACKUP_KEEP` | `30` | Сколько бэкапов хранить |
| `PODKOP_MCP_MAX_OUTPUT_BYTES` | `200000` | Ограничение захваченного вывода команды, байты |
| `PODKOP_MCP_LOG_LEVEL` | `INFO` | Уровень логов сервера в stderr (`DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`) |
| `PODKOP_MCP_PROFILE_TTL` | `30` | Секунд до перепроверки профиля бэкенда на роутере |
| `PODKOP_MCP_TARGETS` | — | JSON с несколькими роутерами |
| `PODKOP_MCP_CONFIG` | — | Путь к JSON-файлу с несколькими роутерами; приоритетнее `PODKOP_MCP_TARGETS` и переменных для одного роутера |
| `PODKOP_MCP_TARGET_NAME` | `router` | Имя единственного роутера из `PODKOP_MCP_SSH_HOST` / `PODKOP_MCP_SSH_CONFIG_HOST`; игнорируется, если список роутеров задан JSON |

Для нескольких роутеров положите их в JSON-файл и укажите его в
`PODKOP_MCP_CONFIG`:

```json
{
  "targets": {
    "home": {"host": "192.0.2.10"},
    "office": {"host": "192.0.2.11", "port": 2222}
  },
  "default_target": "home"
}
```

```json
"env": {
  "PODKOP_MCP_CONFIG": "~/.config/podkop-mcp/targets.json"
}
```

Роутер из `default_target` (или первый, если поле не задано) используется, когда
в вызове инструмента не передан `target`; передайте `"target": "office"`, чтобы
выбрать другой. На этот файл `PODKOP_MCP_TARGET_NAME` не влияет: он задаёт имя
единственного роутера из `PODKOP_MCP_SSH_HOST` / `PODKOP_MCP_SSH_CONFIG_HOST`.

## Тестирование

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe scripts\smoke_mcp.py
$env:PODKOP_MCP_LIVE=1; .\.venv\Scripts\python.exe -m pytest -q -m live
```

Для реального SSH/UCI/apply есть **OpenWrt в Docker**: настоящий userland
`openwrt/rootfs`, настоящие `ubus`/`rpcd`/`dropbear`/`uci`, а CLI/init Forkop —
заглушки (без sing-box/nft).

```powershell
.\scripts\openwrt-docker.ps1
. .\.tmp\openwrt-env.ps1
.\.venv\Scripts\python.exe -m pytest -q -m docker_ssh
.\scripts\openwrt-docker.ps1 -Stop
```

Это контейнер, а не VM: он использует ядро хоста. Полные инструкции и
ограничения — [docs/testing.ru.md](https://github.com/mikel38487/podkop-mcp/blob/main/docs/testing.ru.md). Live-тесты ничего не
пишут на боевой роутер; запись проверяется в Docker-контейнере и на fake-транспорте.

## Ограничения

- Транспорт MCP — stdio; SSE/HTTP вне рамок.
- Перестановка секций пока не поддерживается (порядок = порядок в UCI).
- Обработка подписок Podkop — best-effort; активный target — Forkop.
- Forkop 1.x не умеет активный тест задержки отдельного прокси — только история URLTest.
- Автоматическое обновление Forkop/Podkop не выполняется.

## Лицензия

MIT. Forkop и Podkop — отдельные проекты со своими лицензиями.

## Не связан

Это независимый неофициальный проект. Он не связан с проектами Podkop, Forkop
и OpenWrt, не поддерживается и не одобряется ими.
