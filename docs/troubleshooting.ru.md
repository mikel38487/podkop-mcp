# Диагностика проблем

Начинайте с `podkop_doctor`: он показывает состояние конфига, SSH и бэкенда,
ничего не меняя.

## `podkop_doctor` вернул `ok: false`

### SSH-ключ не найден

Путь в `PODKOP_MCP_SSH_KEY_PATH` проверяется на машине, где запущен MCP-сервер,
а не на роутере. Указывайте абсолютный путь (`C:/Users/<user>/.ssh/router_key`
в Windows, `/home/<user>/.ssh/router_key` в Linux) или алиас из `~/.ssh/config`:

```json
"PODKOP_MCP_SSH_CONFIG_HOST": "router-home",
"PODKOP_MCP_SSH_KEY_PATH": "C:/Users/<user>/.ssh/router_key"
```

### Аутентификация не прошла

- Нужен приватный ключ, не `.pub`.
- Проверьте пользователя (на OpenWrt обычно `root`).
- Проверьте вне MCP: `ssh -o BatchMode=yes router-home "echo ok"`.

### Ключ хоста не совпадает

По умолчанию `reject`. После переустановки роутера удалите старую запись:

```bash
ssh-keygen -R 192.168.1.1
```

Затем один раз подключитесь с `PODKOP_MCP_HOST_KEY_POLICY=accept-new` или
добавьте новый ключ в `known_hosts` вручную. `ignore` — только для стенда.

### Бэкенд не найден

Сервер ищет CLI `forkop`/`podkop` и UCI-пакет. Проверьте:

```sh
command -v forkop; uci -q show forkop | head
```

Если есть только старый `/etc/config/podkop`, а бинаря `podkop` нет — это не
бэкенд. `PODKOP_MCP_BACKEND=forkop|podkop` задавайте, только если установлены
оба и нужно выбрать конкретный.

## Запись не проходит или откатывается

Перед каждым `apply=true` создаётся бэкап. Если reload не удался, сервер
восстанавливает бэкап и возвращает ошибку. Проверьте:

1. `podkop_backup_list` — бэкап создан?
2. `podkop_logs(source="forkop", lines=100)` — первая строка `[error]`.
3. `podkop_health(full=true)` — состояние sing-box/nft/DNS.
4. `podkop_config_export(mode="masked")` — тип и значение опции.

Частые причины:

- секция `connection` без подписки/ссылки/интерфейса;
- `action="zapret2"` без `nfqws2_opt` на Forkop 1.x;
- домен записан UCI-списком там, где Forkop 1.x ждёт одну строку
  (используйте `podkop_domains_add`, не `uci add_list` вручную);
- невалидный JSON в кастомном outbound.

Если сервис не поднялся совсем, восстановите рабочий бэкап:

```text
podkop_backup_restore(backup_id="<id>", apply=true)
```

## Домен добавлен, но не идёт через прокси

Проверяйте по цепочке:

1. `podkop_domains_list(section="main")` — запись есть, тип хранения для
   Forkop 1.x — `option_text` (не `list`).
2. `podkop_domain_check(domain="example.com")` — `covered=true`,
   `fakeip=true`.
3. `podkop_connections_live(host="example.com")` — соединения идут через
   ожидаемую цепочку, а не `direct`.
4. DNS клиента: Private DNS на Android, iCloud Private Relay на iOS, DoH в
   браузере или вручную прописанный DNS обходят роутер. Отключите на время.
5. Приложение кеширует неудачи: закройте и откройте его.

Если `fakeip=false`, а покрытие есть — конфиг sing-box ещё не перегенерирован:
reload отклонён или секция выключена. Смотрите `podkop_sections_list`
(`enabled`) и логи.

## `podkop_status` работает, а Clash/мониторинг — нет

Forkop 1.x использует action-интерфейс (`forkop clash_api get_connections`),
Forkop 2.x — HTTP (`forkop clash_api GET /connections`). Сервер определяет
режим по версии. Если не работает:

- `podkop_cli(command="get_status")` отвечает?
- `podkop_logs(source="sing-box")` — ядро запущено?
- Clash API может быть выключен или привязан к другому интерфейсу; CLI-путь
  сам разбирается с авторизацией.

## Read-only режим

`PODKOP_MCP_READ_ONLY=1` блокирует запись, действия сервиса и обновление
списков. `podkop_doctor` показывает `read_only: true`. Dry-run продолжает
работать.

## Пустой вывод или `***`

- `***` — секрет (URL подписки, proxy-строка, ключ). Если действительно нужен,
  укажите `reveal_secrets=true` осознанно.
- `podkop_config_get` показывает анонимные секции как `@subscription_url[0]`;
  старые имена `cfg...` — в `aliases`.

## Windows/PowerShell

- Значения экранируются внутри скрипта, который уходит в `sh` через stdin,
  поэтому `|`, `$`, кавычки и переводы строк не нужно экранировать локально.
  Не «чините» команды лишними кавычками; `base64` на роутере не требуется.
- При проблемах с русским выводом задайте `PYTHONIOENCODING=utf-8`.
- MCP-сервер пишет логи в stderr; stdout занят протоколом MCP.

## Сбор отчёта

```text
podkop_doctor
podkop_status
podkop_health(full=false)
podkop_logs(source="forkop", lines=100)
podkop_config_export(mode="masked")
```

Masked-вывод безопасен для публикации, но перед отправкой всё равно проверьте.
