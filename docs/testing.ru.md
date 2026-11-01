# Тестирование в OpenWrt-контейнере Docker

Это локальная/CI-среда для проверки SSH + UCI + apply/rollback. В Docker
запускается **userland OpenWrt**, а не виртуальная машина: контейнер использует
ядро хоста. Для полноценного роутера с модулями ядра, nftables и sing-box нужен
QEMU/Proxmox/другая VM (см. конец страницы).

## Что внутри настоящее

| Компонент | В контейнере | Примечание |
|---|---|---|
| OpenWrt rootfs | **настоящий** `openwrt/rootfs:x86_64-25.12.6` | та же версия, что на тестовом роутере |
| `uci` / `ubus` / `ubusd` / `rpcd` | **настоящие** | проверяется путь `ubus call uci get` |
| `dropbear` + SSH | **настоящий** | тесты подключаются к `127.0.0.1:2222` |
| backup (`cp`, `sha256sum`) | **настоящий** | проверяется apply/rollback |
| `forkop` CLI / init | **заглушка** | детерминированный JSON/reload; без sing-box и nft |
| ядро / nftables / sing-box | ядро хоста | это не полный роутер |

Заглушки нужны потому, что Forkop требует совместимый sing-box, nftables и
procd; в контейнере это тяжело и архитектурно-зависимо. Заглушки позволяют
быстро проверить всё, чем владеет сам podkop-mcp: SSH, UCI JSON, типы
option/list, бэкапы, проверку reload, откат и маскирование.

## Запуск в Windows

Docker Desktop должен быть запущен.

```powershell
cd path\to\podkop-mcp
.\scripts\openwrt-docker.ps1
. .\.tmp\openwrt-env.ps1
.\.venv\Scripts\python.exe -m pytest -q -m docker_ssh
```

Скрипт генерирует одноразовый SSH-ключ, собирает образ
`podkop-mcp-openwrt:test`, поднимает контейнер на порту 2222 и пишет
`.tmp/openwrt-env.ps1`.

Остановить:

```powershell
.\scripts\openwrt-docker.ps1 -Stop
```

## Запуск в Linux / CI

```bash
cd /path/to/podkop-mcp
bash scripts/openwrt-docker.sh
source .tmp/openwrt-env.sh
python -m pytest -q -m docker_ssh
docker rm -f podkop-mcp-openwrt
```

В GitHub Actions это делает job `docker-ssh`.

## Прямые UCI-тесты без SSH и Docker-сокета

`tests/docker/test_apply_openwrt.py` (маркер `docker`) проверяет `Router` через
`docker exec` или, если сокет недоступен, через локальный `chroot` того же
rootfs:

```bash
PODKOP_MCP_EMU_ROOT=/opt/openwrt-emu PODKOP_MCP_EMU_LOCAL_CHROOT=1 \
  python -m pytest -q -m docker
```

Это удобно в песочнице и для быстрой проверки `uci` без SSH.

## Что покрывают тесты

- `podkop_doctor` находит Forkop 1.0.5 по реальному SSH и реальному `ubus`/`uci`.
- `podkop_domains_add(apply=true)` сохраняет `section.domain` **строкой**, пишет
  бэкап и делает reload.
- `podkop_domains_remove(apply=true)` возвращает исходный список.
- `podkop_option_set` меняет настройку, `podkop_option_get` читает обратно.
- Прямые тесты `Router.apply_plan` проверяют падение reload → автооткат и
  восстановление бэкапа.

## Ограничения

- Это **не** полноценная OpenWrt VM. Модули ядра, реальный nft-пайплайн,
  sing-box, procd-супервизия и железная сеть здесь не проверяются.
- CLI/init Forkop в контейнере — заглушки; реальное read-only поведение Forkop
  1.0.5 покрывают live-тесты (`-m live`) на тестовом роутере.
- Docker Desktop на Windows использует ядро хоста; поведение `iptables`/`nftables`
  может отличаться от OpenWrt.
- Для полноценного теста нужен x86_64 OpenWrt под QEMU (с KVM) или Proxmox,
  установка `forkop` + `sing-box-forkop` и SSH к этой VM. В обычный CI это не
  входит: нужна аппаратная виртуализация и больший образ.

## Безопасность

Тестовый ключ генерируется в `.tmp/` (игнорируется git), контейнер одноразовый,
реальный ключ роутера не используется. Не коммитьте `.tmp/` и ключ.

## Реальный Forkop 2.0: smoke-тест

Образ по умолчанию использует заглушку forkop CLI. Для полной проверки
запустите тот же OpenWrt rootfs с `/sbin/init` (procd), поставьте Forkop
upstream-установщиком и направьте MCP на SSH-порт контейнера:

```bash
# внутри одноразового privileged-контейнера с интернетом
sh <(wget -O - https://raw.githubusercontent.com/ushan0v/forkop/main/install.sh) --variant base --lang en --quiet
```

Затем задайте `PODKOP_MCP_SSH_HOST/PORT/USER/KEY_PATH` на этот контейнер и
используйте `podkop_section_create` + `podkop_subscription_add` +
`podkop_service_action`. Так проверены: определение реального Forkop 2.0,
импорт подписки (50 прокси), `subscription_update` через proxy-providers,
запись домена, FakeIP-резолв, backup/restore и другой egress IP через прокси.

Не кладите реальный URL подписки в репозиторий и отслеживаемые фикстуры —
только env одноразового контейнера.

## Реальный Podkop 0.7.24: smoke-тест

Тот же одноразовый OpenWrt-контейнер использовался для установки Podkop
upstream-установщиком (`sh <(wget -O - https://raw.githubusercontent.com/itdoginfo/podkop/refs/heads/main/install.sh)`).
Podkop 0.7.24 определился автоматически (`schema=podkop`, список `user_domains`).

Проверено на реальном сервисе:

- `podkop_doctor`, `podkop_status`, `podkop_config_get`, `podkop_sections_list`;
- `podkop_option_set` для `proxy_config_type=url` и VLESS `proxy_string`;
- `podkop_domains_add(apply=true)` — пишет `user_domains` **и** выставляет
  `user_domain_list_type=dynamic` (без этого Podkop молча игнорирует список);
- запуск сервиса (`sing-box` работает), `podkop_monitor_snapshot`,
  `podkop_proxies_list` (одна VLESS-нода);
- `podkop_domain_check` отдаёт FakeIP `198.18.0.2` для добавленного домена.

Podkop **не стартует из reload**, если он остановлен и outbound не настроен;
поэтому сервер коммитит конфиг с предупреждением, а запуск оставляет
`podkop_service_action`.
