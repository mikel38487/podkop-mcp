# Testing in an OpenWrt Docker container

This is the local/CI test environment for the SSH + UCI + apply/rollback path.
It runs **OpenWrt userland in Docker**, not a virtual machine: the container
shares the host kernel. For a full router with kernel modules, nftables and
sing-box, use QEMU/Proxmox/another VM (see the end of this page).

## What is real inside

| Component | In container | Notes |
|---|---|---|
| OpenWrt rootfs | **real** `openwrt/rootfs:x86_64-25.12.6` | same release as the test router |
| `uci` / `ubus` / `ubusd` / `rpcd` | **real** | `ubus call uci get` path is exercised |
| `dropbear` + SSH | **real** | tests connect over `127.0.0.1:2222` |
| backup (`cp`, `sha256sum`) | **real** | apply/rollback path is exercised |
| `forkop` CLI / init script | **stub** | deterministic JSON/reload; no sing-box or nft |
| kernel / nftables / sing-box | host kernel | not a full router |

Why stubs: Forkop needs a matching sing-box build, nftables and procd; in a
container this is heavy and architecture-specific. The stub keeps tests fast and
lets us verify everything that podkop-mcp itself owns: SSH, UCI JSON, typed
option/list writes, backups, reload checks, rollback and redaction.

## Run on Windows

Docker Desktop must be running.

```powershell
cd path\to\podkop-mcp
.\scripts\openwrt-docker.ps1
. .\.tmp\openwrt-env.ps1
.\.venv\Scripts\python.exe -m pytest -q -m docker_ssh
```

The helper generates an ephemeral SSH key, builds `podkop-mcp-openwrt:test`,
starts the container on port 2222 and writes `.tmp/openwrt-env.ps1`.

Stop it:

```powershell
.\scripts\openwrt-docker.ps1 -Stop
```

## Run on Linux / CI

```bash
cd /path/to/podkop-mcp
bash scripts/openwrt-docker.sh
source .tmp/openwrt-env.sh
python -m pytest -q -m docker_ssh
docker rm -f podkop-mcp-openwrt
```

The GitHub Actions job `docker-ssh` does exactly this.

## Direct UCI tests without SSH/Docker daemon

`tests/docker/test_apply_openwrt.py` (marker `docker`) exercises `Router` with
`docker exec` or, when a Docker socket is unavailable, with a local
`chroot` of the same rootfs:

```bash
PODKOP_MCP_EMU_ROOT=/opt/openwrt-emu PODKOP_MCP_EMU_LOCAL_CHROOT=1 \
  python -m pytest -q -m docker
```

That path is useful for sandboxed CI or for a quick `uci` check without SSH.

## What the tests cover

- `podkop_doctor` detects Forkop 1.0.5 over real SSH and real `ubus`/`uci`.
- `podkop_domains_add(apply=true)` keeps `section.domain` a **string**, writes a
  backup and reloads.
- `podkop_domains_remove(apply=true)` restores the original list.
- `podkop_option_set` updates a setting and `podkop_option_get` reads it back.
- Direct `Router.apply_plan` tests cover reload failure -> automatic rollback
  and backup restore.

## Limitations

- This is **not** a full OpenWrt VM. Kernel modules, `nft` in a real router
  pipeline, sing-box, procd service supervision and hardware networking are not
  tested here.
- The container's forkop CLI/init are stubs; real Forkop 1.0.5 read-only
  behavior is covered by the live tests (`-m live`) against the test router.
- Docker Desktop on Windows shares the host kernel; `iptables`/`nftables`
  behavior can differ from OpenWrt.
- For a full-system test, boot an x86_64 OpenWrt image under QEMU (with KVM) or
  Proxmox, install `forkop` + `sing-box-forkop` there, and point podkop-mcp at
  that VM over SSH. This is intentionally not part of the default CI because it
  needs hardware virtualization and a larger image.

## Security

The test key is generated locally under `.tmp/` (ignored by git), the container
is disposable, and the tests never use the real router key. Do not commit
`.tmp/` or the generated key.

## Real Forkop 2.0 smoke test

The default image uses a stub forkop CLI. For a real end-to-end check, boot the
same OpenWrt rootfs with `/sbin/init` (procd), install Forkop from the upstream
installer, and point the MCP server at its SSH port:

```bash
# inside a disposable privileged container with internet
sh <(wget -O - https://raw.githubusercontent.com/ushan0v/forkop/main/install.sh) --variant base --lang en --quiet
```

Then configure `PODKOP_MCP_SSH_HOST/PORT/USER/KEY_PATH` to that container and use
`podkop_section_create` + `podkop_subscription_add` + `podkop_service_action`.
This was used to verify: real Forkop 2.0 detection, subscription import (50
proxies), `subscription_update` via proxy-providers, domain apply, FakeIP
resolution, backup/restore and a different egress IP through the proxy.

Never put a real subscription URL in this repository or in tracked fixtures; use
an environment variable in a disposable container.

## Real Podkop 0.7.24 smoke test

The same disposable OpenWrt container was used to install Podkop from its
upstream installer (`sh <(wget -O - https://raw.githubusercontent.com/itdoginfo/podkop/refs/heads/main/install.sh)`).
Podkop 0.7.24 was detected automatically (`schema=podkop`, `user_domains` list).

Verified against the real service:

- `podkop_doctor`, `podkop_status`, `podkop_config_get`, `podkop_sections_list`;
- `podkop_option_set` for `proxy_config_type=url` and a VLESS `proxy_string`;
- `podkop_domains_add(apply=true)` — writes `user_domains` **and** sets
  `user_domain_list_type=dynamic` (without it Podkop silently ignores the list);
- service start (`sing-box` process running), `podkop_monitor_snapshot`,
  `podkop_proxies_list` (one VLESS node);
- `podkop_domain_check` returns FakeIP `198.18.0.2` for the added domain.

Podkop does **not** start from `reload` when it is stopped and has no outbound;
the server therefore commits config changes with a warning and leaves the start
to `podkop_service_action`.
