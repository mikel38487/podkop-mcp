#!/usr/bin/env bash
# Поднимает OpenWrt в Docker и готовит SSH-доступ для тестов (Linux/CI).
# Это контейнер (общее ядро с хостом), а не VM; внутри — реальные uci,
# ubusd, rpcd, dropbear. forkop CLI/init — заглушки.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PORT="${PODKOP_EMU_PORT:-2222}"
NAME="${PODKOP_EMU_NAME:-podkop-mcp-openwrt}"
IMAGE="podkop-mcp-openwrt:test"
TMP="$ROOT/.tmp"
KEYDIR="$TMP/openwrt-ssh"
KEY="$KEYDIR/id_ed25519"
PUB="$KEY.pub"

docker info >/dev/null 2>&1 || { echo "Docker daemon недоступен" >&2; exit 1; }
mkdir -p "$KEYDIR"
[ -f "$KEY" ] || ssh-keygen -t ed25519 -f "$KEY" -N "" -q

if ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
    docker build -t "$IMAGE" "$ROOT/tests/docker"
fi

docker rm -f "$NAME" >/dev/null 2>&1 || true
docker run -d --name "$NAME" -p "${PORT}:22" \
    -v "$PUB:/etc/dropbear/authorized_keys:ro" \
    "$IMAGE" >/dev/null

KNOWN="$TMP/openwrt-known_hosts"
rm -f "$KNOWN"
for _ in $(seq 1 60); do
    sleep 1
    if ssh -i "$KEY" -p "$PORT" -o BatchMode=yes -o ConnectTimeout=3 \
        -o StrictHostKeyChecking=accept-new -o UserKnownHostsFile="$KNOWN" \
        root@127.0.0.1 "uci get forkop.main.domain" >/dev/null 2>&1; then
        break
    fi
done

if ! ssh -i "$KEY" -p "$PORT" -o BatchMode=yes -o ConnectTimeout=3 \
    -o StrictHostKeyChecking=accept-new -o UserKnownHostsFile="$KNOWN" \
    root@127.0.0.1 "uci get forkop.main.domain" >/dev/null 2>&1; then
    docker logs "$NAME" 2>&1 | tail -30 || true
    echo "OpenWrt SSH не поднялся" >&2
    exit 1
fi

ENVFILE="$TMP/openwrt-env.sh"
cat > "$ENVFILE" <<EOF
export PODKOP_MCP_DOCKER_SSH=1
export PODKOP_MCP_SSH_HOST=127.0.0.1
export PODKOP_MCP_SSH_PORT=$PORT
export PODKOP_MCP_SSH_USER=root
export PODKOP_MCP_SSH_KEY_PATH=$KEY
export PODKOP_MCP_KNOWN_HOSTS=$KNOWN
export PODKOP_MCP_HOST_KEY_POLICY=reject
export PODKOP_MCP_READ_ONLY=0
EOF

echo "OpenWrt container ready: 127.0.0.1:$PORT"
echo "  source $ENVFILE"
echo "  python -m pytest -q -m docker_ssh"
echo "  docker rm -f $NAME"
