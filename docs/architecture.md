# Architecture

`podkop-mcp` is a thin, safety-oriented bridge between an MCP client and an
OpenWrt router running [Podkop](https://github.com/itdoginfo/podkop) or
[Forkop](https://github.com/ushan0v/forkop). It does not run a daemon on the
router and does not install anything there.

## Process layout

```
MCP client
   │  JSON-RPC over stdio
   ▼
┌──────────────────────────────────────────────────────┐
│ podkop-mcp (one Python process on the admin machine) │
│                                                      │
│  server.py        MCPServer, tool registration       │
│  tools/*.py       MCP-facing wrappers                │
│  services.py      per-target Router cache            │
│  router.py        UCI, CLI, backups, apply/rollback  │
│  backends.py      Forkop 1.x / 2.x / Podkop profiles │
│  domains_core.py  pure domain parsing and coverage   │
│  clash.py         Clash API via CLI                  │
│  ssh.py           paramiko, scripts over stdin   │
│  redaction.py     secret masking                     │
└──────────────────────────────────────────────────────┘
   │  SSH (key, host key verification)
   ▼
OpenWrt: ubus/uci, forkop|podkop CLI, logread, /proc
```

The process is synchronous per tool call and keeps one SSH connection per
target. A lock serializes commands on a connection; a failed connection is
re-established once.

## Reading configuration

`ubus call uci get '{"config":"<pkg>"}'` returns the full UCI tree as JSON with
types preserved (strings vs lists). That is the primary source. If `ubus` is not
available, the server falls back to `uci export` and a small parser.

This is why the server can expose *all* settings, not just a curated list: the
generic tools operate on `pkg.section.option` paths, while semantic tools add
validation and backend-specific formatting.

## Backend profiles

| | Forkop 1.x | Forkop 2.x | Podkop |
|---|---|---|---|
| Detection | `forkop show_version`, `settings.config_version` | same, major ≥ 2 | `podkop show_version` |
| Domain storage | `domain` option, space separated | `destinations` multiline text | `user_domains` UCI list |
| Category lists | `community_lists` list | `community_lists`, `rule_set` | `community_lists` list |
| Clash transport | action CLI (`clash_api get_connections`) | HTTP CLI (`clash_api GET /connections`) | action CLI |
| Init script | `/etc/init.d/forkop` | same | `/etc/init.d/podkop` |

Capabilities are derived from the CLI help output; a tool that needs a missing
command returns a clear error instead of guessing.

## Write path

```
tool call (apply=false) → PlanBuilder
    ├── read current config
    ├── validate paths and values
    ├── compute normalized after-state
    └── return diff + preview commands (secrets masked)

tool call (apply=true)
    ├── refuse if PODKOP_MCP_READ_ONLY=1
    ├── backup /etc/config/<pkg> → /etc/podkop-mcp/backups
    ├── run uci set/add_list/delete + uci commit
    │     (values are shell-quoted; the script is fed to sh via stdin)
    ├── /etc/init.d/<pkg> reload, check exit code
    ├── poll get_status until running (timeout ~35 s)
    ├── quick health checks
    └── on failure: restore backup, commit, reload, report error
```

No user value is concatenated into a shell line. The only interpolated strings
are validated UCI paths and the backend package name.

## Security boundaries

- Writes are restricted to the `forkop`/`podkop` UCI package.
- Arbitrary shell is not exposed. `podkop_cli` accepts only read-only commands
  from an allowlist; mutating CLI commands are reserved for tools with
  backup/rollback.
- File reads are restricted to `/etc`, `/var`, `/tmp`, `/proc`, `/sys`, `/rom`,
  `/usr/share`, with no `..`.
- Secrets are masked in tool responses by default. `reveal_secrets=true` is an
  explicit, documented exception.
- Host keys are verified; `accept-new` only adds unknown keys, never ignores a
  mismatch.

## Error model

- `PodkopError` and subclasses are expected domain errors.
- `guard` converts them to MCP `ToolError` with a readable message.
- Unexpected exceptions are logged to stderr (never stdout, which is the MCP
  protocol channel) and returned as a generic internal error.

## Testing layers

1. **Pure unit tests** for config parsing, UCI, domain semantics, redaction,
   diff/plan building.
2. **Fake router** tests for tools: an in-memory `Router`/`Services` pair, no
   SSH.
3. **MCP protocol smoke**: spawn the real server, `initialize`, `tools/list`,
   call `podkop_doctor`.
4. **Live read-only** tests against a real router (opt-in via
   `PODKOP_MCP_LIVE=1`). They never call `apply=true`.

Write operations are never tested against a live production router by the test
suite; users can preview them with `apply=false` and apply intentionally.
