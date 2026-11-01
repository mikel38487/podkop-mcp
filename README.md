# podkop-mcp

**Languages:** [English](https://github.com/mikel38487/podkop-mcp/blob/main/README.md) · [Русский](https://github.com/mikel38487/podkop-mcp/blob/main/README.ru.md)

<!-- mcp-name: io.github.mikel38487/podkop-mcp -->

[![CI](https://github.com/mikel38487/podkop-mcp/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/mikel38487/podkop-mcp/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/podkop-mcp?logo=pypi&logoColor=white)](https://pypi.org/project/podkop-mcp/)
[![Python versions](https://img.shields.io/badge/python-3.10%20%7C%203.11%20%7C%203.12%20%7C%203.13%20%7C%203.14-blue)](https://github.com/mikel38487/podkop-mcp/blob/main/pyproject.toml)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](https://github.com/mikel38487/podkop-mcp/blob/main/LICENSE)

**MCP server for managing [Podkop](https://github.com/itdoginfo/podkop) and
[Forkop](https://github.com/ushan0v/forkop) on OpenWrt over SSH.**

Stop editing UCI by hand, remembering the `uci add_list` trap and passing
base64-wrapped scripts to PowerShell. Ask your MCP client to add domains, list
sections, inspect live connections, reload the service and roll back a bad
change — with dry-run, backups and redaction built in.

> Status: alpha. Live read-only tested against Forkop 1.0.5 and Forkop 2.0.0
> (OpenWrt 25.12.6). Write/apply paths were verified in disposable OpenWrt
> Docker containers with real Forkop 2.0.0 and Podkop 0.7.24. Backend schema is
> revalidated every `PODKOP_MCP_PROFILE_TTL` seconds (default 30).

## Why

Managing Podkop/Forkop by hand is error-prone:

- Forkop 1.x stores domains in **one space-separated string** (`section.domain`).
  One `uci add_list` turns it into an exact-match `domain` list and subdomains
  silently stop being routed.
- Forkop 2.x uses a multi-line `section.destinations` text with `#` comments.
- Podkop uses a real UCI list (`section.user_domains`).
- Forkop reloads asynchronously; a failed reload must be detected and rolled back.
- Subscription URLs and proxy strings are secrets and end up in logs.

Unlike MCP servers that run on the router itself, `podkop-mcp` runs on the
administrator's machine, talks to OpenWrt over SSH and understands Podkop/Forkop
semantics: dry-run, backups, rollback and secret redaction. All of that is
hidden behind typed MCP tools.

## Features

- **SSH transport** with a key from MCP parameters: key file, inline base64/PEM,
  or `~/.ssh/config` alias. Host key verification, keepalive, timeouts.
- **Backend detection**: Forkop 1.x, Forkop 2.x, Podkop; capabilities derived
  from the installed CLI.
- **Full UCI access** via `ubus call uci get` JSON and `uci` for writes. Any
  option can be read or changed; the package is restricted to `forkop`/`podkop`.
- **Domain operations** that preserve the backend format: add/remove/replace,
  search coverage, suggest uncovered hosts from live connections, check FakeIP.
- **Sections**: list, inspect, create, update, enable/disable, delete with
  cascade of child subscriptions/URLTest items.
- **Subscriptions and proxy groups**: list, add, delete, update, test latency,
  select a proxy (Forkop 2.x).
- **Monitoring**: service/system status, health checks, logs, live connections,
  traffic rates, proxy delays.
- **Safety**: mutating tools are dry-run by default; `apply=true` creates a
  router-side backup, commits UCI, reloads and verifies. On failure the backup is
  restored automatically. `PODKOP_MCP_READ_ONLY=1` blocks all writes.
- **Secrets redacted** by default: subscription URLs, `proxy_string`, keys,
  tokens. Raw export requires `reveal_secrets=true`.
- **Bilingual docs** (English / Russian) and CI.

## Support matrix

| Capability | Forkop 1.x | Forkop 2.x | Podkop |
|---|---|---|---|
| UCI package | `forkop` | `forkop` | `podkop` |
| Domain option | `domain` (string) | `destinations` (multiline) | `user_domains` (list) |
| Community lists | yes | yes | yes |
| Rule sets | no | yes | yes |
| Clash API | action CLI | HTTP CLI | action CLI |
| Proxy runtime select | — | yes | — |
| Read-only monitoring | yes | yes | yes |

## Install

**Requirements:**

- Python 3.10+ (CI tests 3.10 through 3.14)
- OpenWrt router with Forkop or Podkop installed
- SSH key access to the router

**Install from PyPI:**

```bash
pip install podkop-mcp
# or run without installing
uvx podkop-mcp
```

**Install from source:**

```bash
git clone https://github.com/mikel38487/podkop-mcp.git
cd podkop-mcp
python -m venv .venv
. .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e .
```

**Configure your MCP client** (Claude Desktop, Cursor, DSH, etc.):

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

Or use an existing `~/.ssh/config` alias:

```json
"env": {
  "PODKOP_MCP_SSH_CONFIG_HOST": "router-home",
  "PODKOP_MCP_SSH_KEY_PATH": "C:/Users/<user>/.ssh/router_key",
  "PODKOP_MCP_HOST_KEY_POLICY": "reject"
}
```

Host key policy defaults to `reject`. Add the router key to `known_hosts` first
(for example with a normal `ssh` login). `accept-new` is a one-time TOFU
convenience for a new router; it does not protect against a MITM on the first
connection.

Ask the model:

- “Run `podkop_doctor` and show the backend version.”
- “Add `example.com` and `cdn.example.com` to `main`; show me the diff first.”
- “List sections with domain counts.”
- “Which hosts in live connections are not covered by any section?”
- “Show traffic for the last 2 seconds and the top clients.”
- “Disable the `youtube` section and reload.”

## Documentation

- [Tool reference](https://github.com/mikel38487/podkop-mcp/blob/main/docs/tools.md) · [Справочник инструментов](https://github.com/mikel38487/podkop-mcp/blob/main/docs/tools.ru.md)
- [Architecture](https://github.com/mikel38487/podkop-mcp/blob/main/docs/architecture.md) · [Архитектура](https://github.com/mikel38487/podkop-mcp/blob/main/docs/architecture.ru.md)
- [Compatibility](https://github.com/mikel38487/podkop-mcp/blob/main/docs/compatibility.md) · [Совместимость](https://github.com/mikel38487/podkop-mcp/blob/main/docs/compatibility.ru.md)
- [Troubleshooting](https://github.com/mikel38487/podkop-mcp/blob/main/docs/troubleshooting.md) · [Диагностика](https://github.com/mikel38487/podkop-mcp/blob/main/docs/troubleshooting.ru.md)
- [Testing in OpenWrt Docker](https://github.com/mikel38487/podkop-mcp/blob/main/docs/testing.md) · [Тестирование в Docker](https://github.com/mikel38487/podkop-mcp/blob/main/docs/testing.ru.md)
- [Security](https://github.com/mikel38487/podkop-mcp/blob/main/docs/safety.md) · [Безопасность](https://github.com/mikel38487/podkop-mcp/blob/main/docs/safety.ru.md)
- [Security policy](https://github.com/mikel38487/podkop-mcp/blob/main/SECURITY.md), [contributing](https://github.com/mikel38487/podkop-mcp/blob/main/CONTRIBUTING.md)

## How write operations work

```
dry-run (default)
  → read config, compute normalized diff, show uci commands

apply=true
  → cp /etc/config/<pkg> /etc/podkop-mcp/backups/<ts>-<reason>.uci
  → uci set/add_list/delete (package restricted to forkop/podkop)
  → uci commit
  → /etc/init.d/<pkg> reload
  → poll get_status up to ~35 s
  → on failure: restore backup, commit, reload
  → return diff, backup id, health summary
```

No command is executed through a shell string built from user input; values are
shell-quoted with `shlex` and the script is sent to `sh` via stdin. Paths are
validated; no `base64` applet is required on the router.

## Tool overview

| Group | Tools |
|---|---|
| System | `podkop_doctor`, `podkop_status`, `podkop_system_info`, `podkop_health`, `podkop_logs`, `podkop_cli`, `podkop_core_features`, `podkop_import_check`, `podkop_installer_status`, `podkop_installer_log` |
| Config | `podkop_config_get`, `podkop_config_export`, `podkop_option_get/set/delete`, `podkop_backup_create/list/restore` |
| Sections | `podkop_sections_list`, `podkop_section_get/create/update/delete/enable`, `podkop_tunnel_create`, `podkop_child_create` |
| Domains | `podkop_domains_list/add/remove/replace/find/suggest`, `podkop_domain_check` |
| Lists | `podkop_community_lists_get/update` |
| Connections | `podkop_connections_config`, `podkop_subscription_add/delete/update`, `podkop_proxies_list`, `podkop_proxy_select/test` |
| Monitoring | `podkop_monitor_snapshot`, `podkop_connections_live`, `podkop_traffic` |
| Service | `podkop_service_action`, `podkop_lists_update` |

Full reference: [docs/tools.md](https://github.com/mikel38487/podkop-mcp/blob/main/docs/tools.md).

## Configuration reference

| Variable | Default | Meaning |
|---|---|---|
| `PODKOP_MCP_SSH_HOST` | — | Router address |
| `PODKOP_MCP_SSH_PORT` | `22` | SSH port |
| `PODKOP_MCP_SSH_USER` | `root` | SSH user |
| `PODKOP_MCP_SSH_KEY_PATH` | — | Private key path |
| `PODKOP_MCP_SSH_KEY` | — | Inline private key (PEM) |
| `PODKOP_MCP_SSH_KEY_B64` | — | Inline key, base64 |
| `PODKOP_MCP_SSH_KEY_PASSPHRASE` | — | Key passphrase |
| `PODKOP_MCP_SSH_PASSWORD` | — | Password instead of a key (secret: pass it through the environment, never commit it) |
| `PODKOP_MCP_SSH_CONFIG_HOST` | — | Alias from `~/.ssh/config` |
| `PODKOP_MCP_KNOWN_HOSTS` | `~/.ssh/known_hosts` | Host key file |
| `PODKOP_MCP_HOST_KEY_POLICY` | `reject` | `reject`, `accept-new`, `ignore` |
| `PODKOP_MCP_CONNECT_TIMEOUT` | `8` | SSH connect timeout, seconds |
| `PODKOP_MCP_COMMAND_TIMEOUT` | `45` | Command timeout, seconds |
| `PODKOP_MCP_BACKEND` | `auto` | `auto`, `forkop` or `podkop`; the requested CLI must be installed |
| `PODKOP_MCP_READ_ONLY` | `0` | Block all writes |
| `PODKOP_MCP_BACKUP_DIR` | `/etc/podkop-mcp/backups` | Router-side backups |
| `PODKOP_MCP_BACKUP_KEEP` | `30` | Number of backups to keep |
| `PODKOP_MCP_MAX_OUTPUT_BYTES` | `200000` | Cap on captured command output, bytes |
| `PODKOP_MCP_LOG_LEVEL` | `INFO` | Server log level on stderr (`DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`) |
| `PODKOP_MCP_PROFILE_TTL` | `30` | Seconds before the backend profile is revalidated against the router |
| `PODKOP_MCP_TARGETS` | — | JSON with multiple routers |
| `PODKOP_MCP_CONFIG` | — | Path to a JSON file with multiple routers; takes precedence over `PODKOP_MCP_TARGETS` and the single-router variables |
| `PODKOP_MCP_TARGET_NAME` | `router` | Name of the single router defined by `PODKOP_MCP_SSH_HOST` / `PODKOP_MCP_SSH_CONFIG_HOST`; ignored when a JSON target list is used |

To manage several routers, put them in a JSON file and point
`PODKOP_MCP_CONFIG` at it:

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

The `default_target` router (or the first one when it is omitted) is used when a
tool call does not pass `target`; pass `"target": "office"` to use the other
router. `PODKOP_MCP_TARGET_NAME` does not select a target from this file — it
only names the single router defined by `PODKOP_MCP_SSH_HOST` /
`PODKOP_MCP_SSH_CONFIG_HOST`.

## Testing

```bash
python -m pytest -q                 # unit + fake router + live/docker skipped
python scripts/smoke_mcp.py         # MCP stdio handshake
PODKOP_MCP_LIVE=1 python -m pytest -q -m live   # read-only, real router
```

For the real SSH/UCI/apply path there is an **OpenWrt Docker container**:
real `openwrt/rootfs` userland, real `ubus`/`rpcd`/`dropbear`/`uci`, with stubs
for the Forkop CLI/init (no sing-box/nft).

```powershell
.\scripts\openwrt-docker.ps1
. .\.tmp\openwrt-env.ps1
.\.venv\Scripts\python.exe -m pytest -q -m docker_ssh
.\scripts\openwrt-docker.ps1 -Stop
```

This is a container, not a VM: it shares the host kernel. Full VM instructions
and limitations are in [docs/testing.md](https://github.com/mikel38487/podkop-mcp/blob/main/docs/testing.md). Live tests never
write to the production router; write operations are tested in the Docker
container and through the fake transport.

## Limitations

- MCP transport is stdio; SSE/HTTP is out of scope.
- Reordering sections is not exposed yet (UCI order is file order); use export/edit
  + `podkop_config_export` if needed.
- Podkop subscription handling is best-effort; the active target is Forkop.
- Forkop 1.x does not support active per-proxy latency tests; use URLTest history.
- No automatic Forkop/Podkop upgrade.

## License

MIT. Forkop and Podkop are separate projects under their own licenses.

## Not affiliated

This is an independent, unofficial project. It is not affiliated with, endorsed
by or maintained by the Podkop, Forkop or OpenWrt projects.
