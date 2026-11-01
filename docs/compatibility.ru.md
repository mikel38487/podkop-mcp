# Совместимость

## Бэкенды

| | Forkop 1.x | Forkop 2.x | Podkop |
|---|---|---|---|
| Определение | `forkop show_version`, `settings.config_version` | то же, major ≥ 2 | `podkop show_version` |
| UCI-пакет | `forkop` | `forkop` | `podkop` |
| Init | `/etc/init.d/forkop` | то же | `/etc/init.d/podkop` |
| Домены | `section.domain` — одна строка через пробел | `section.destinations` — по одному значению в строке, комментарии `#`/`//` | `section.user_domains` UCI-список; legacy `domain_list` |
| Community lists | `section.community_lists` | `community_lists` + `rule_set` | `community_lists` |
| Подписки | дочерние `@subscription_url` | то же | inline-опции секции |
| Clash | action CLI (`clash_api get_connections`) | HTTP CLI (`clash_api GET /connections`) | action CLI |
| Runtime-выбор прокси | сервер не поддерживает | `PUT /proxies/<group>` | не поддерживается |
| Проверено | **живой роутер** (1.0.5, OpenWrt 25.12.6) | фикстуры + real 2.0 в Docker (подписка, providers, apply) | real 0.7.24 в Docker (detect/config/domains/service, VLESS) |

## Что покрыто

- **Все настройки UCI** через `podkop_config_get`, `podkop_option_get/set/delete`.
  Это generic-слой: новая опция upstream читается и пишется без обновления сервера,
  если пакет — `forkop` или `podkop`.
- **Секции**: список, просмотр, создание, изменение, удаление, включение/выключение.
- **Домены**: list/add/remove/replace/find/suggest/check с учётом формата бэкенда.
- **Community lists**: выбранные и каталог; добавление/удаление настоящим UCI-списком.
- **Подключения**: подписки, URLTest, ссылки, туннели (чтение); добавление,
  удаление и обновление подписок.
- **Мониторинг**: статус, здоровье, логи, прокси, живые соединения, скорость.
- **Безопасность**: dry-run, бэкап, проверка reload, автооткат, read-only,
  маскирование секретов.

## Известные ограничения

- **Перестановка секций** не вынесена в инструменты: порядок = порядок в UCI.
  Для перестановки используйте `podkop_config_export` и правку/импорт.
- **Подписки Podkop** — best-effort: сервер пишет `subscription_url` и флаги
  обновления, но версии Podkop по-разному хранят источники. Живой target — Forkop.
- **Домены Podkop** работают через `user_domains` только при
  `user_domain_list_type=dynamic`; сервер выставляет это автоматически при
  добавлении/удалении.
- **Podkop 0.7.24** проверен на реальном OpenWrt-контейнере; другие версии
  могут отличаться CLI/опциями. После обновления смотрите `podkop_doctor` и
  начинайте с read-only инструментов.
- **Forkop 1.x** не умеет активный тест задержки отдельного прокси;
  `podkop_proxies_list` отдаёт историю URLTest. `podkop_proxy_select` — только 2.x.
- **Диапазоны FakeIP** распознаются как `198.18.0.0/15` и `fc00::/18`;
  кастомные диапазоны из sing-box пока не читаются.
- **Одновременные правки**: сервер читает конфиг, строит план и сразу применяет;
  не редактируйте роутер через LuCI, пока идёт `apply=true`.
- **Podkop 0.7.24** проверен на одноразовом OpenWrt-контейнере, но не на
  боевом роутере. Другие версии Podkop считайте непроверенными, пока
  `podkop_doctor` и read-only инструменты не отработают.

## Проверка нового бэкенда

```text
podkop_doctor
podkop_status
podkop_config_get(include_raw=False)
podkop_sections_list
podkop_domains_list(section="main")
podkop_monitor_snapshot(include_connections=False)
```

Если установлены и Forkop, и Podkop и определение неверное, задайте
`PODKOP_MCP_BACKEND=forkop` или `podkop` явно.
