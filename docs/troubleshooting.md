# Troubleshooting

Start with `podkop_doctor` — it reports config/SSH/backend problems without
changing anything.

## `podkop_doctor` returns `ok: false`

### SSH key not found

The path in `PODKOP_MCP_SSH_KEY_PATH` is resolved on the machine running the MCP
server, not on the router. Use an absolute path (`C:/Users/<user>/.ssh/router_key`
on Windows, `/home/<user>/.ssh/router_key` on Linux) or an `~/.ssh/config` alias:

```json
"PODKOP_MCP_SSH_CONFIG_HOST": "router-home",
"PODKOP_MCP_SSH_KEY_PATH": "C:/Users/<user>/.ssh/router_key"
```

### Authentication failed

- The key must be the private key, not `.pub`.
- Check the user (`root` on OpenWrt by default).
- Test outside MCP: `ssh -o BatchMode=yes router-home "echo ok"`.

### Host key mismatch

`reject` is the default. If the router was reinstalled, remove the old entry:

```bash
ssh-keygen -R 192.168.1.1
```

Then connect once with `PODKOP_MCP_HOST_KEY_POLICY=accept-new`, or add the new
key to `known_hosts` manually. Do not use `ignore` outside a lab.

### Backend not found

The server looks for the `forkop` or `podkop` CLI and the UCI package. Check:

```sh
command -v forkop; uci -q show forkop | head
```

If only a legacy `/etc/config/podkop` exists but no `podkop` binary, it is not a
backend. Set `PODKOP_MCP_BACKEND=forkop|podkop` only when both are installed and
you want a specific one.

## Write fails or is rolled back

Every `apply=true` writes a backup first. If reload fails, the server restores
the backup and returns an error. Things to check:

1. `podkop_backup_list` — confirm a backup was created.
2. `podkop_logs(source="forkop", lines=100)` — look for the first `[error]`.
3. `podkop_health(full=true)` — sing-box/nft/DNS state.
4. `podkop_config_export(mode="masked")` — verify the option type/value.

Common causes:

- a `connection` section without a subscription/link/interface;
- `action="zapret2"` without `nfqws2_opt` on Forkop 1.x;
- a domain written as a UCI list where Forkop 1.x expects one string
  (use `podkop_domains_add`, never manual `uci add_list`);
- invalid JSON in a custom outbound.

If the service does not come back at all, restore a known-good backup:

```text
podkop_backup_restore(backup_id="<id>", apply=true)
```

## Domain is added but does not use the proxy

Check the chain in order:

1. `podkop_domains_list(section="main")` — the entry is present and the storage
   is `option_text` for Forkop 1.x (not `list`).
2. `podkop_domain_check(domain="example.com")` — `covered=true`,
   `fakeip=true`.
3. `podkop_connections_live(host="example.com")` — connections use the expected
   proxy chain, not `direct`.
4. Client DNS: Android Private DNS, iOS iCloud Private Relay, browser DoH or a
   manually set DNS bypass the router. Disable them for the test.
5. App caches failures: force-close and reopen the app.

If `fakeip=false` but coverage is true, the generated sing-box config may not
contain the rule yet: reload was rejected or the domain is in a disabled
section. Check `podkop_sections_list` (`enabled`) and logs.

## `podkop_status` works, but Clash/monitoring tools fail

Forkop 1.x uses the action CLI (`forkop clash_api get_connections`), Forkop 2.x
uses the HTTP transport (`forkop clash_api GET /connections`). The server
detects the mode from the version. If both fail:

- `podkop_cli(command="get_status")` works?
- `podkop_logs(source="sing-box")` — is the core running?
- YACD/Clash API may be disabled or bound to another interface; the CLI path
  handles authentication internally.

## Read-only mode

`PODKOP_MCP_READ_ONLY=1` blocks every write, service action and list update.
`podkop_doctor` reports `read_only: true`. Dry-run calls still work.

## Empty or redacted output

- `***` means the value is a secret (subscription URL, proxy string, key).
  Use `reveal_secrets=true` explicitly if you really need it.
- `podkop_config_get` shows anonymous sections as `@subscription_url[0]`;
  their old `cfg...` names are in `aliases`.

## Windows/PowerShell notes

- Values are shell-quoted inside a script sent to `sh` via stdin, so `|`, `$`,
  quotes and newlines do not need local escaping. Do not “fix” commands with
  extra quoting; no `base64` applet is required on the router.
- Set `PYTHONIOENCODING=utf-8` if the console mangles Russian output.
- The MCP server writes logs to stderr; stdout is reserved for the MCP protocol.

## Collecting a report

```text
podkop_doctor
podkop_status
podkop_health(full=false)
podkop_logs(source="forkop", lines=100)
podkop_config_export(mode="masked")
```

Masked output is safe to share; still review it before posting publicly.
