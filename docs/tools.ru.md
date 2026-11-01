# Справочник инструментов

Все инструменты возвращают JSON-словарь. У каждого есть необязательный
`target` (имя из `PODKOP_MCP_TARGETS`; без него — target по умолчанию).
Изменяющие инструменты по умолчанию работают в **dry-run**: возвращают diff,
предпросмотр и предупреждения, ничего не записывая до `apply=true`.

Успешная запись возвращает:

```json
{
  "applied": true,
  "backup_id": "20261029T012345Z-domains-add.uci",
  "changes": [ ... ],
  "service": {"running": 1, "status": "running & enabled"},
  "health": { ... }
}
```

## Система и диагностика

### `podkop_doctor(target=None)`

Проверяет конфигурацию MCP, SSH, определённый бэкенд (Forkop 1.x/2.x или
Podkop), UCI-пакет и число секций. Принудительно перечитывает профиль, поэтому
замечает обновление роутера без перезапуска MCP. Вызывать первым при проблемах.
Не пишет.

### `podkop_status(target=None)`

Статус сервиса, sing-box, Zapret2 (если установлен), версии и сведения об
OpenWrt. Неподдерживаемые подкоманды попадают в `skipped`, а ненулевой код — в
`errors`, а не в usage-дамп. Только чтение.

### `podkop_core_features(target=None)`

`forkop get_core_features`: теги сборки sing-box и доступные возможности
(WireGuard, uTLS, QUIC, OpenVPN, Tailscale, …). Только чтение.

### `podkop_import_check(path, target=None)`

Проверяет конфиг Forkop через `import_config_check` без применения. `path` —
абсолютный путь на роутере без `..`. Только чтение.

### `podkop_installer_status(target=None, offset=0)` / `podkop_installer_log(target=None)`

Статус/лог последнего запуска установщика Forkop. Только чтение.

### `podkop_system_info(target=None)`

Модель, версия OpenWrt/ядра, load average, память, диск, интерфейсы, WAN,
процессы прокси. Только чтение.

### `podkop_health(target=None, full=False, timeout=60)`

Доступные read-only проверки. Forkop 2.0 использует `check_service`,
`check_dns`, `check_traffic` и `check_servers`; Forkop 1.x — старые
`check_sing_box`/nft/FakeIP. Неподдерживаемые проверки попадают в `skipped`;
`ok` относится к выполненным, `complete=true` только если ничего не пропущено.
`full=true` добавляет `global_check`, если он есть (сеть, до минуты).

### `podkop_logs(target=None, source="forkop", lines=80, filter=None)`

Логи Forkop/Podkop, sing-box или системы. Секреты в URL маскируются, ANSI-
последовательности удаляются. `lines` — 1..500.

### `podkop_cli(command, args=None, target=None, parse_json=True, reveal_secrets=False, timeout=45)`

Запускает **read-only** команду из allowlist Forkop/Podkop. Изменяющие
команды (`start`, `stop`, `reload`, `list_update`, `subscription_update`,
component actions, …) запрещены — для них есть отдельные инструменты с
бэкапом и откатом. `show_config raw` заменяется на `masked`, если не указан
`reveal_secrets=true`.

## Конфигурация и бэкапы

### `podkop_config_get(target=None, package=None, reveal_secrets=False, include_raw=False)`

Полный UCI-конфиг как JSON. Анонимные секции показываются как
`@subscription_url[0]` (как в `uci show`), исходные имена `cfg...` — в
`aliases`. Секреты маскируются, если не `reveal_secrets=true`.

### `podkop_config_export(target=None, mode="masked", reveal_secrets=False)`

UCI-экспорт текстом. `mode="raw"` требует `reveal_secrets=true`.

### `podkop_option_get(path, target=None, reveal_secrets=False)`

Чтение одной опции: `forkop.main.domain`, `forkop.settings.log_level`,
`forkop.@subscription_url[0].url`. Возвращает значение, `is_list`, `exists`.

### `podkop_option_set(path, value, target=None, value_type="auto", apply=False, reason="option-set")`

Запись опции или списка. `value_type="auto"` считает значением список, если
передан list. Пакет — только `forkop`/`podkop`.

### `podkop_option_delete(path, target=None, apply=False, reason="option-delete")`

Удаление опции с бэкапом и reload при `apply=true`.

### `podkop_backup_create(target=None, reason="manual", apply=False)`

Создаёт `/etc/podkop-mcp/backups/<UTC>-<reason>.uci` с sha256. По умолчанию
dry-run.

### `podkop_backup_list(target=None, limit=50)`

Список бэкапов на роутере.

### `podkop_backup_restore(backup_id, target=None, apply=False)`

Восстановление бэкапа, commit и reload. `backup_id` — из `podkop_backup_list`.

## Секции

### `podkop_sections_list(target=None, include_children=False, reveal_secrets=False)`

Обзор: имя, label, action, enabled, число доменов/сводка, community lists,
число дочерних элементов. `include_children=true` возвращает подписки,
URLTest, ссылки и туннели (URL маскируются).

### `podkop_section_get(section, target=None, reveal_secrets=False, include_children=True)`

Все опции и дочерние элементы секции + нормализованные домены.

### `podkop_section_create(name, action="connection", options=None, target=None, apply=False)`

Создаёт top-level секцию с минимальными дефолтами бэкенда. Forkop 2.0
принимает `action="connection"` или `"dns"`; старые `zapret2`/`byedpi`
запрещены. Для `action="zapret2"` на Forkop 1.x передайте `nfqws2_opt`, иначе
ядро отклонит конфиг. Секции connection нужен источник подключения —
инструмент предупреждает об этом.

### `podkop_tunnel_create(section, name, kind, label=None, options=None, target=None, apply=False)`

Создаёт `tunnel`-ребёнка Forkop 2.x (`warp`, `wireguard`, `amneziawg`,
`tailscale`, `openvpn-client`, `openconnect`, `json`). Дополнительные опции
(`warp_profile_*`, `detour_section`, …) передаются как есть; секреты в preview
маскируются. По умолчанию dry-run.

### `podkop_child_create(section, child_type, name, label=None, options=None, target=None, apply=False)`

Низкоуровневое создание `urltest`, `fallback_group`, `link`, `interface`,
`json` или `tunnel_peer`. Для подписок и туннелей есть отдельные инструменты.
По умолчанию dry-run.

### `podkop_section_update(section, options, target=None, apply=False)`

Частичное обновление опций секции. Для доменов используйте domain-инструменты:
они сохраняют формат хранения.

### `podkop_section_delete(section, target=None, cascade=True, allow_main=False, apply=False)`

Удаление секции и (при `cascade=true`) её дочерних элементов. Для `main`
нужен `allow_main=true`.

### `podkop_section_enable(section, enabled, target=None, apply=False)`

Включить/выключить секцию, не удаляя её.

## Домены

### `podkop_domains_list(section="main", target=None)`

Нормализованные записи, сырое значение, тип хранения (`option_text`,
`option_multiline`, `list`), невалидные строки и сводка по видам. Поле берётся
из фактической секции (`destinations` приоритетнее legacy `domain`), а не из
закешированной схемы.

### `podkop_domains_add(section, domains, target=None, apply=False, verify=True)`

Добавляет записи, убирает дубли, отклоняет невалидные. Forkop 1.x хранит
`section.domain` одной строкой через пробел; Forkop 2.x — многострочный
`destinations`; Podkop — список `user_domains`. При `apply=true` первый
добавленный домен проверяется через DNS роутера на FakeIP
(`verify=false` отключает).

### `podkop_domains_remove(section, domains, target=None, apply=False)`

Удаляет записи; отсутствующие возвращаются в `not_found`, это не ошибка.

### `podkop_domains_replace(section, domains, target=None, apply=False)`

Заменяет весь набор. Пустой список запрещён — для очистки используйте remove.
Комментарии/пустые строки Forkop 2.x по возможности сохраняются.

### `podkop_domains_find(domain, target=None)`

Где покрыт домен: совпавшие записи секций и вид (`domain`, зона, `full:`,
`keyword:`, `regex:`, IP/CIDR). Выбранные community lists возвращаются как
`possible_lists`, потому что их содержимое серверу неизвестно.

### `podkop_domains_suggest(target=None, limit=20, only_direct=True)`

Подсказывает хосты из живых соединений Clash, не покрытые ни одной секцией.
При `only_direct=true` учитываются только соединения, чья цепочка содержит
`direct`. Эвристика: проверяйте перед добавлением.

### `podkop_domain_check(domain, target=None, probe_http=True)`

Проверяет домен, резолвит через роутер (`nslookup`), определяет FakeIP,
показывает покрытие секциями и при `probe_http` делает HEAD-запрос
`https://<domain>/` (ошибки curl не выбрасываются). HTTP-проверка приватных,
loopback и link-local адресов запрещена (SSRF-защита).

## Community lists

### `podkop_community_lists_get(section=None, target=None)`

Выбранные списки для одной секции (или всех) и статический каталог известных
имён. `unknown` — выбранные имена вне каталога.

### `podkop_community_lists_update(section, add=None, remove=None, target=None, apply=False)`

Добавляет/удаляет имена в `community_lists` как настоящий UCI-список; сеть не
используется.

## Подключения и прокси

### `podkop_connections_config(target=None, section=None, reveal_secrets=False)`

Подписки, URLTest-группы, ссылки и туннели по секциям. URL и proxy-строки
маскируются, если не `reveal_secrets=true`.

### `podkop_subscription_add(section, url, options=None, target=None, apply=False)`

Forkop: создаёт дочернюю `subscription_url` (`<section>_sub_N`) с дефолтами
обновления; Podkop: пишет inline-опции `subscription_url`. URL — `http(s)://…`
или абсолютный путь.

### `podkop_subscription_delete(identifier, section=None, target=None, apply=False)`

Удаление по имени child-секции, индексу (с 1), tag или сырому URL.

### `podkop_subscription_update(section=None, target=None, apply=False, wait=True, timeout=180)`

Обновление подписок (запись кеша; нужен `apply=true`). Forkop 1.x использует
`subscription_update` / async-опрос статуса; Forkop 2.x обновляет Clash
`proxy-providers` через Clash API. Без `apply` возвращает структурный
`dry_run` с выбранным режимом. Ограничено `timeout`.

### `podkop_proxies_list(target=None, group=None)`

Группы и узлы Clash: тип, выбранный прокси, последняя задержка. `group`
возвращает состав одной группы.

### `podkop_proxy_select(group, proxy, target=None, apply=False)`

Переключение прокси в runtime (не конфиг). Поддерживается на Forkop 2.x; на
1.x возвращает понятную ошибку.

### `podkop_proxy_test(target=None)`

Read-only `check_proxy` + задержки выбранных групп из истории.

## Мониторинг

### `podkop_monitor_snapshot(target=None, include_connections=True, top=10)`

Сводка одним вызовом: сервис, система, прокси, соединения (top N, count,
totals), выбранные группы и быстрая проверка sing-box. Части падают
независимо и попадают в `check_errors`.

### `podkop_connections_live(target=None, host=None, source_ip=None, chain=None, limit=50)`

Живые соединения с фильтрами. `host`/`chain` — подстрока без регистра;
`source_ip` — точное совпадение или префикс сети. Поля: host, source, chain,
rule, bytes, start.

### `podkop_traffic(target=None, sample_seconds=2.0, top=10)`

Два сэмпла соединений Clash: скорости download/upload (байт/с), дельты по
соединениям и топ потребителей. Блокирует поток на интервал; `sample_seconds`
зажат в 0.5..10.

## Сервис и обновления

### `podkop_service_action(action, target=None, apply=False, wait=True, timeout=45)`

`start`, `stop`, `restart`, `reload`, `enable`, `disable`. По умолчанию
dry-run; `stop` запрещён в read-only.

### `podkop_lists_update(kind="lists", target=None, apply=False, wait=True, timeout=180)`

Обновление встроенных списков, подписок или обоих. Меняет кеш роутера,
поэтому нужен `apply=true`.

## Ошибки

- `ConfigError` — неверный путь, нет секции, невалидное значение.
- `BackendError` — команда не поддерживается бэкендом или reload не удался.
- `SafetyError` — read-only, чужой пакет, неверный id бэкапа.
- `ToolError` — сообщение для MCP; без трассировок и секретов.
