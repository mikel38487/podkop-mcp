# Security

## What counts as a secret

- the private SSH key and its passphrase;
- subscription URLs (the access token is in the path);
- `proxy_string` (VLESS/Trojan/Hysteria2/Shadowsocks, etc.);
- `private_key`, `pre_shared_key`, `auth_key`, `password`, `yacd_secret_key`;
- the full `/etc/config/forkop` or `/etc/config/podkop` dump.

Tools mask these values by default. The only exceptions are explicit
`reveal_secrets=true` and `podkop_config_export(mode="raw")`.

## Threat model

The MCP server runs on the administrator's machine and has SSH access with root
rights. A compromised MCP client or prompt injection through log content must not
give an arbitrary shell on the router:

- writes are restricted to the `forkop`/`podkop` UCI package and validated paths;
- arbitrary CLI is denied by an allowlist; mutating commands are reserved for
  tools with dry-run, backup and rollback;
- file reads are restricted to `/etc`, `/var`, `/tmp`, `/proc`, `/sys`, `/rom`,
  `/usr/share`, with no `..`;
- `PODKOP_MCP_READ_ONLY=1` blocks all writes, service actions and list updates.

Residual risk: the SSH key has root rights. Use a dedicated key and, where
possible, `authorized_keys` restrictions (`from=`, `command=`), plus
`PODKOP_MCP_READ_ONLY=1` for monitoring-only sessions.

## Host key policy

The default is `reject`: an unknown host key is rejected. Add the router key to
`known_hosts` first. `accept-new` is an explicit one-time TOFU option: it adds an
unknown key but still rejects a mismatch with an already known key. `ignore` is
for isolated lab benches only.

## Backups and rollback

Before every write the server copies `/etc/config/<pkg>` to
`/etc/podkop-mcp/backups/<UTC>-<reason>.uci` with a sha256 sidecar. If reload
fails or the service does not come up in time, the backup is restored
automatically, committed and the service is reloaded.

Backups are ordinary UCI files; list them with `podkop_backup_list` and restore
with `podkop_backup_restore`.

## Secrets in logs

`podkop_logs` passes output through redaction: subscription URLs, proxy links,
UUIDs and token-like query parameters are masked. Do not publish full logs in an
issue without reviewing them.

## Reporting a vulnerability

Do not open a public issue with secrets. Use GitHub Security Advisory or contact
the repository owner privately and include a minimal reproduction without real
keys or subscription URLs.
