# Tool reference

All tools return JSON-serializable dictionaries. Every tool accepts an optional
`target` (name from `PODKOP_MCP_TARGETS`; the default target is used when
omitted). Mutating tools are **dry-run by default**: they return the diff,
preview and warnings, and write nothing until `apply=true`.

A successful write returns:

```json
{
  "applied": true,
  "backup_id": "20261029T012345Z-domains-add.uci",
  "changes": [ ... ],
  "service": {"running": 1, "status": "running & enabled"},
  "health": { ... }
}
```

## System and diagnostics

### `podkop_doctor(target=None)`

Checks MCP configuration, SSH connectivity, the detected backend (Forkop 1.x /
2.x or Podkop), config package and section counts. Forces a fresh backend
rescan, so it also detects a router upgrade without restarting the MCP process.
Use it first when a call fails. Never writes.

### `podkop_status(target=None)`

Service status, sing-box status, Zapret2 status (if installed), versions and
OpenWrt system info. Unsupported CLI subcommands are listed in `skipped`; a
non-zero command is reported in `errors` instead of being returned as a usage
dump. Read-only.

### `podkop_core_features(target=None)`

`forkop get_core_features`: sing-box build tags and feature availability
(WireGuard, uTLS, QUIC, OpenVPN, Tailscale, …). Read-only.

### `podkop_import_check(path, target=None)`

Validate a Forkop config file on the router with `import_config_check` without
applying it. `path` must be absolute and contain no `..`. Read-only.

### `podkop_installer_status(target=None, offset=0)` / `podkop_installer_log(target=None)`

Read-only status/log of the last Forkop installer run.

### `podkop_system_info(target=None)`

Model, OpenWrt/kernel version, load average, memory, disk, interfaces, WAN
status and running proxy processes. Read-only.

### `podkop_health(target=None, full=False, timeout=60)`

Runs the available read-only checks. Forkop 2.0 uses `check_service`,
`check_dns`, `check_traffic` and `check_servers`; Forkop 1.x keeps the legacy
`check_sing_box`/nft/FakeIP checks. Unsupported checks are listed in `skipped`;
`ok` covers executed checks, `complete` is true only when nothing was skipped.
`full=true` adds `global_check` when available (network, may take a minute).

### `podkop_logs(target=None, source="forkop", lines=80, filter=None)`

Recent logs from the Forkop/Podkop journal, sing-box journal or system log.
Secrets in URLs are masked; ANSI sequences are stripped. `lines` is clamped to
1..500.

### `podkop_cli(command, args=None, target=None, parse_json=True, reveal_secrets=False, timeout=45)`

Runs a **read-only** command from the Forkop/Podkop allowlist. Mutating commands
(`start`, `stop`, `reload`, `list_update`, `subscription_update`, component
actions, …) are rejected; use the dedicated tools so backups and rollback are
applied. `show_config raw` is downgraded to `masked` unless
`reveal_secrets=true`.

## Configuration and backups

### `podkop_config_get(target=None, package=None, reveal_secrets=False, include_raw=False)`

Full UCI config as structured JSON. Anonymous UCI sections are shown as
`@subscription_url[0]` (the same names `uci show` uses); the raw `cfg...`
aliases are returned in `aliases`. Secrets are masked unless
`reveal_secrets=true`.

### `podkop_config_export(target=None, mode="masked", reveal_secrets=False)`

UCI export as text. `mode="raw"` requires `reveal_secrets=true`.

### `podkop_option_get(path, target=None, reveal_secrets=False)`

Read one option, e.g. `forkop.main.domain`, `forkop.settings.log_level`,
`forkop.@subscription_url[0].url`. Returns value, `is_list` and `exists`.

### `podkop_option_set(path, value, target=None, value_type="auto", apply=False, reason="option-set")`

Set one option or list. `value_type="auto"` uses a list when `value` is a list.
The path must belong to the backend package (`forkop` or `podkop`).

### `podkop_option_delete(path, target=None, apply=False, reason="option-delete")`

Delete one option with backup and reload on `apply=true`.

### `podkop_backup_create(target=None, reason="manual", apply=False)`

Create `/etc/podkop-mcp/backups/<UTC>-<reason>.uci` with a sha256 sidecar.
Dry-run by default.

### `podkop_backup_list(target=None, limit=50)`

List backups on the router.

### `podkop_backup_restore(backup_id, target=None, apply=False)`

Restore a backup, commit and reload. `backup_id` must be one returned by
`podkop_backup_list`.

## Sections

### `podkop_sections_list(target=None, include_children=False, reveal_secrets=False)`

Overview: name, label, action, enabled, domain count/summary, community lists,
child item counts. `include_children=true` returns subscription/URLTest/link/
tunnel items (URLs masked).

### `podkop_section_get(section, target=None, reveal_secrets=False, include_children=True)`

All options and child items of one section plus normalized domains.

### `podkop_section_create(name, action="connection", options=None, target=None, apply=False)`

Create a top-level routing section with minimal backend-specific defaults.
Forkop 2.0 accepts `action="connection"` or `"dns"`; legacy `zapret2`/`byedpi`
actions are rejected. For `action="zapret2"` on Forkop 1.x, pass `nfqws2_opt`
in `options`, otherwise the core will reject the generated config. A connection
section also needs a subscription/link/interface source; the tool warns about this.

### `podkop_tunnel_create(section, name, kind, label=None, options=None, target=None, apply=False)`

Create a Forkop 2.x `tunnel` child (`warp`, `wireguard`, `amneziawg`,
`tailscale`, `openvpn-client`, `openconnect`, `json`). Extra backend options
(`warp_profile_*`, `detour_section`, …) are passed through; secrets are masked
in the preview. Dry-run by default.

### `podkop_child_create(section, child_type, name, label=None, options=None, target=None, apply=False)`

Low-level creation of `urltest`, `fallback_group`, `link`, `interface`, `json`
or `tunnel_peer` children. Use the dedicated tools for subscriptions and
tunnels. Dry-run by default.

### `podkop_section_update(section, options, target=None, apply=False)`

Partial update of section options. For domains use the domain tools, which
preserve the backend storage type.

### `podkop_section_delete(section, target=None, cascade=True, allow_main=False, apply=False)`

Delete a section and (with `cascade=true`) its child items. Deleting `main`
requires `allow_main=true`.

### `podkop_section_enable(section, enabled, target=None, apply=False)`

Toggle `enabled` without deleting the section.

## Domains

### `podkop_domains_list(section="main", target=None)`

Normalized entries, raw stored value, storage type (`option_text`,
`option_multiline`, `list`), invalid entries and a summary by kind. The field is
chosen from the actual section (`destinations` wins over legacy `domain`), not
from a cached schema.

### `podkop_domains_add(section, domains, target=None, apply=False, verify=True)`

Adds entries, de-duplicates, rejects invalid ones. Forkop 1.x keeps
`section.domain` as one space-separated string; Forkop 2.x writes multiline
`destinations`; Podkop uses the `user_domains` list. On `apply=true` the first
added domain is resolved via the router DNS to confirm FakeIP mapping
(`verify=false` disables this).

### `podkop_domains_remove(section, domains, target=None, apply=False)`

Removes entries; missing entries are reported in `not_found` and are not an
error.

### `podkop_domains_replace(section, domains, target=None, apply=False)`

Replaces the whole set. Empty input is refused: use remove for clearing.
Forkop 2.x comments/blank lines are preserved where possible.

### `podkop_domains_find(domain, target=None)`

Where a domain is covered: matching section entries and kinds (`domain`, zone,
`full:`, `keyword:`, `regex:`, IP/CIDR). Selected community lists are returned
as `possible_lists` because their contents are not available to the server.

### `podkop_domains_suggest(target=None, limit=20, only_direct=True)`

Suggests hosts seen in live Clash connections that are not covered by any
section. With `only_direct=true` only connections whose chain contains
`direct` are considered. Heuristic: verify before adding.

### `podkop_domain_check(domain, target=None, probe_http=True)`

Validates the domain, resolves it through the router (`nslookup`), detects
FakeIP, reports section coverage and optionally probes `https://<domain>/`
with curl (HTTP status, errors do not raise). HTTP probing of private,
loopback and link-local addresses is refused (SSRF guard).

## Community lists

### `podkop_community_lists_get(section=None, target=None)`

Selected lists for one section (or all sections) and the static catalog of
known list names. `unknown` contains selected names outside the catalog.

### `podkop_community_lists_update(section, add=None, remove=None, target=None, apply=False)`

Adds/removes names in `community_lists` as a real UCI list; no network access.

## Connections and proxy groups

### `podkop_connections_config(target=None, section=None, reveal_secrets=False)`

Subscriptions, URLTest groups, links and tunnels per section. URLs and proxy
strings are masked unless `reveal_secrets=true`.

### `podkop_subscription_add(section, url, options=None, target=None, apply=False)`

Forkop: creates a `subscription_url` child (`<section>_sub_N`) with update
defaults; Podkop: sets inline `subscription_url` options. URL must be
`http(s)://…` or an absolute path.

### `podkop_subscription_delete(identifier, section=None, target=None, apply=False)`

Delete by child name, 1-based index, tag or raw URL.

### `podkop_subscription_update(section=None, target=None, apply=False, wait=True, timeout=180)`

Refresh subscriptions (cache write; `apply=true` required). Forkop 1.x uses
`subscription_update` / async status polling; Forkop 2.x refreshes Clash
`proxy-providers` through the Clash API. Without `apply` returns a structured
`dry_run` response with the selected mode. Bounded by ``timeout`` seconds.

### `podkop_proxies_list(target=None, group=None)`

Clash groups and nodes with type, selected proxy and last delay. `group`
returns one group's members.

### `podkop_proxy_select(group, proxy, target=None, apply=False)`

Runtime selection of a proxy in a Clash group (not a config change). Supported
on Forkop 2.x; on 1.x returns a clear error.

### `podkop_proxy_test(target=None)`

Read-only `check_proxy` plus selected-group delays from history.

## Monitoring

### `podkop_monitor_snapshot(target=None, include_connections=True, top=10)`

One-call snapshot: service, system, proxy summary, connections (top N, count,
totals), selected groups and a quick sing-box check. Parts fail independently
and are reported in `check_errors`.

### `podkop_connections_live(target=None, host=None, source_ip=None, chain=None, limit=50)`

Live connections with filters. `host`/`chain` are case-insensitive substrings;
`source_ip` matches exactly or as a network prefix. Returns normalized fields
(host, source, chain, rule, bytes, start).

### `podkop_traffic(target=None, sample_seconds=2.0, top=10)`

Samples Clash connections twice, returns total download/upload rates (bytes/s),
per-connection deltas and top consumers. Blocks for the sampling interval;
`sample_seconds` is clamped to 0.5..10.

## Service and updates

### `podkop_service_action(action, target=None, apply=False, wait=True, timeout=45)`

`start`, `stop`, `restart`, `reload`, `enable`, `disable`. Dry-run by default;
`stop` is blocked in read-only mode.

### `podkop_lists_update(kind="lists", target=None, apply=False, wait=True, timeout=180)`

Update built-in lists, subscriptions or both. Changes the router cache, so
`apply=true` is required.

## Error model

- `ConfigError` — bad path, missing section, invalid value.
- `BackendError` — unsupported command for the installed backend or a reload
  failure.
- `SafetyError` — read-only mode, forbidden package, invalid backup id.
- `ToolError` — MCP-facing message; no stack traces or secrets.
