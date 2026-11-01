# Security policy

## What counts as a secret

- the private SSH key and its passphrase;
- subscription URLs (they contain the access token);
- `proxy_string` (VLESS/Trojan/Hysteria2/Shadowsocks, etc.);
- `private_key`, `pre_shared_key`, `auth_key`, `password`, `yacd_secret_key`;
- the contents of `/etc/config/forkop` and `/etc/config/podkop` in full.

The server masks these values by default. `reveal_secrets=true` and
`podkop_config_export(mode="raw")` are a deliberate exception.

## Threat model

The MCP server runs on the administrator's machine and has SSH access to the
router. A compromised MCP client (or prompt injection through log content) must
not give an arbitrary shell on the router:

- writes go only to the `forkop`/`podkop` UCI package, and the path is
  validated;
- an arbitrary CLI is forbidden by an allowlist; mutating commands go only
  through tools with dry-run/backup/rollback;
- file reads are limited to `/etc`, `/var`, `/tmp`, `/proc`, `/sys`, `/rom`,
  `/usr/share`, with no `..`;
- the read-only mode `PODKOP_MCP_READ_ONLY=1` blocks all changes.

Residual risk: an SSH key with root rights. Use a dedicated key and
`from=`/`command=` restrictions in `authorized_keys` where possible.

## Host key policy

The default is `reject`: an unknown host key is not accepted. For the first
connection to a new router, add the key to `known_hosts` manually or explicitly
set `PODKOP_MCP_HOST_KEY_POLICY=accept-new` (the key is added, but a mismatch
with an already known key is still an error).

## Reporting a vulnerability

Do not open a public issue with secrets. Write to the repository owner privately
(GitHub Security Advisory) and include a minimal reproduction scenario without
real keys or subscription URLs.
