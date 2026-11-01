# Compatibility

## Backends

| | Forkop 1.x | Forkop 2.x | Podkop |
|---|---|---|---|
| Detection | `forkop show_version`, `settings.config_version` | same, major ≥ 2 | `podkop show_version` |
| Config package | `forkop` | `forkop` | `podkop` |
| Init script | `/etc/init.d/forkop` | same | `/etc/init.d/podkop` |
| Domains | `section.domain`, one space-separated string | `section.destinations`, one entry per line with `#`/`//` comments | `section.user_domains` UCI list; legacy `domain_list` |
| Community lists | `section.community_lists` list | `community_lists` + `rule_set` | `community_lists` list |
| Subscriptions | `@subscription_url` child sections | same | inline section options |
| Clash transport | action CLI (`clash_api get_connections`) | HTTP CLI (`clash_api GET /connections`) | action CLI |
| Runtime proxy select | not supported by the server | `PUT /proxies/<group>` | not supported |
| Verified | **live** (1.0.5, OpenWrt 25.12.6) | fixtures + real 2.0 in Docker (subscription, providers, apply) | real 0.7.24 in Docker (detect/config/domains/service, VLESS) |

## What is covered

- **All UCI settings** through `podkop_config_get`, `podkop_option_get/set/delete`.
  This is generic: a new option added by an upstream release can be read or
  written without updating the server, as long as the package is `forkop` or
  `podkop`.
- **Sections**: list, inspect, create, update, delete, enable/disable.
- **Domains**: list/add/remove/replace/find/suggest/check with backend-specific
  storage.
- **Community lists**: selected and catalog; add/remove as a real UCI list.
- **Connections**: subscriptions, URLTest groups, links, tunnels (read);
  subscription add/delete/update.
- **Monitoring**: service/system status, health checks, logs, Clash proxies,
  live connections, traffic rates.
- **Safety**: dry-run, backup, reload check, automatic rollback, read-only mode,
  secret redaction.

## Known limitations

- Section **reordering** is not exposed: UCI order is file order. Use
  `podkop_config_export` and edit/import if you need to reorder.
- **Podkop subscriptions** are handled best-effort: the server writes the
  `subscription_url` option and update flags, but Podkop versions differ in how
  they store subscription sources. The live target is Forkop.
- **Podkop domains** use `user_domains` only when `user_domain_list_type=dynamic`;
  the server sets this automatically when adding/removing domains.
- **Podkop 0.7.24** was tested on a real OpenWrt container; other Podkop releases
  may differ in CLI/options. Check `podkop_doctor` and start with read-only
  tools after an upgrade.
- **Forkop 1.x** has no active per-proxy latency command; `podkop_proxies_list`
  returns the URLTest history instead. `podkop_proxy_select` needs Forkop 2.x.
- **FakeIP ranges** are recognized as `198.18.0.0/15` and `fc00::/18`; custom
  ranges are not read from sing-box config yet.
- **Section reorder** and simultaneous edits by another process are not
  transactional; the server reads the config, builds a plan and applies it
  immediately. Avoid editing the router from LuCI while an `apply=true` call is
  in flight.
- **Podkop 0.7.24** was tested on a disposable OpenWrt container, not on the
  production router. Treat other Podkop releases as unverified until
  `podkop_doctor` and read-only tools pass.

## Verifying a new backend

```text
podkop_doctor
podkop_status
podkop_config_get(include_raw=False)
podkop_sections_list
podkop_domains_list(section="main")
podkop_monitor_snapshot(include_connections=False)
```

If detection is wrong and both Forkop and Podkop are installed, set
`PODKOP_MCP_BACKEND=forkop` or `podkop` explicitly.
