#!/bin/sh
# Запускает минимальный OpenWrt-стек в контейнере:
# ubusd + rpcd (ubus call uci get) + dropbear на :22.
set -e

mkdir -p /var/run/ubus /var/lib/rpcd /etc/dropbear
rm -f /var/run/ubus/ubus.sock

/sbin/ubusd >/tmp/ubusd.log 2>&1 &
/sbin/rpcd >/tmp/rpcd.log 2>&1 &

# Ключ хоста для dropbear. Клиентский ключ монтируется снаружи
# в /etc/dropbear/authorized_keys.
if [ ! -f /etc/dropbear/dropbear_ed25519_host_key ]; then
    /usr/bin/dropbearkey -t ed25519 -f /etc/dropbear/dropbear_ed25519_host_key >/tmp/dropbear-key.log 2>&1
fi

exec /usr/sbin/dropbear -R -p 22 -F
