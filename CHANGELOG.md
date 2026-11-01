# Changelog

Формат — [Keep a Changelog](https://keepachangelog.com/), версии — SemVer.

## [Unreleased]

## [0.1.1] - 2026-10-30

### Added
- Forkop 2.0: `podkop_tunnel_create`, `podkop_child_create` для tunnel/
  urltest/fallback_group/link/interface/json; read-only `podkop_core_features`,
  `podkop_import_check`, `podkop_installer_status`, `podkop_installer_log`.
- `PODKOP_MCP_PROFILE_TTL` (по умолчанию 30 с): профиль бэкенда перепроверяется
  по `config_version` и пересобирается после обновления роутера.

### Fixed
- Профиль бэкенда больше не залипает на старой схеме после обновления
  Forkop 1.x → 2.x: TTL-проверка `config_version`, принудительный rescan в
  `podkop_doctor`, сброс кеша конфига.
- Поле доменов определяется по фактическому конфигу: `destinations` приоритетнее
  legacy `domain`; запись в отсутствующее поле для Forkop 2.0 запрещена.
- `podkop_health` для Forkop 2.0 агрегирует `check_service/check_dns/check_traffic/
  check_servers`, возвращает `skipped`/`complete`; старый Forkop 1.x сохранён.
- `podkop_status` больше не отдаёт usage-дамп как данные: ненулевой CLI-код
  попадает в `errors`, неподдерживаемые команды — в `skipped`.
- `podkop_subscription_update` без `apply` возвращает структурный dry-run.
- `podkop_section_create` для Forkop 2.0 разрешает только `connection`/`dns` и
  отклоняет legacy `zapret2`/`byedpi`.
- Исправлены регрессии из постмортема 30.10.2026 (кеш схемы, пустой domain-list,
  потеря health-проверок, ошибки CLI в `text`).

## [0.1.0] - 2026-10-29

### Added
- MCP-сервер `podkop-mcp` для Podkop и Forkop на OpenWrt по SSH.
- Чтение/запись UCI через `ubus`/`uci` с сохранением типа опций.
- Семантические операции с доменами для Forkop 1.x, Forkop 2.x и Podkop.
- Управление секциями, community lists, подписками.
- Мониторинг: статус, здоровье, логи, соединения, трафик, прокси.
- Dry-run по умолчанию, бэкапы и автоматический откат при ошибке reload.
- Маскирование секретов, read-only режим, allowlist CLI.
- Тесты на фикстурах и live read-only проверки.
- OpenWrt в Docker: реальный userland `openwrt/rootfs`, `ubus`/`rpcd`/`dropbear`,
  SSH и UCI; тесты `docker` (chroot/exec) и `docker_ssh` (SSH + apply).

### Security
- Закрыта shell-инъекция через параметр `package` в `podkop_config_get`:
  разрешён только текущий backend-пакет, путь в `uci export`/`uci commit`
  экранируется.
- `podkop_cli` парсит JSON из уже замаскированного текста — поле `json`
  больше не обходит redaction.
- Read-only блокирует runtime-переключение прокси и закрытие соединений.
- Маскирование proxy-ссылок по значению, UUID и `token=`/`uuid=` в логах;
  ключи секретов сравниваются без учёта регистра.
- `podkop_backup_list` больше не создаёт каталог на роутере в read-only.
- HTTP-probe домена не ходит на приватные/loopback/link-local адреса (SSRF);
  regex-правила ограничены по длине и вложенным квантификаторам.
- Примеры MCP используют `PODKOP_MCP_HOST_KEY_POLICY=reject` (TOFU только
  осознанно).
- Пустые `add_list`/`del_list` не создают «холостой» commit+reload.

### Fixed
- Forkop 2.0: subscription child получает обязательный `tag`, иначе core
  отказывается запускаться («subscription has no name»).
- Forkop 2.0: Clash API возвращает `{status, body}` с JSON-строкой — тело
  разворачивается; `podkop_proxies_list` и мониторинг снова работают.
- Forkop 2.0: обновление подписок через Clash `proxy-providers`, списков —
  через `rule-providers`, если CLI-команд обновления нет.
- `apply=true` при изначально остановленном сервисе коммитит конфиг и
  возвращает предупреждение вместо ложного отката; запуск — отдельным
  `podkop_service_action`.
- SSH-скрипты передаются в `stdin`, значения экранируются `shlex`; `base64`
  на роутере больше не требуется.
- Проверено end-to-end на реальном Forkop 2.0 в Docker: подписка (50 прокси),
  FakeIP, domain apply, backup/restore, другой egress IP через прокси.
- Проверено на реальном Podkop 0.7.24 в Docker: detect/config/domains/service,
  VLESS proxy_string, FakeIP; исправлен обязательный `user_domain_list_type=dynamic`.
- Отдельно проверен один VLESS WS/TLS URL из подписки: установлен как локальная
  подписка Forkop 2.0, core поднялся, egress IP через прокси отличался от прямого.
