FROM openwrt/rootfs:x86_64-25.12.6

# Реальный OpenWrt userland: uci + ubus + rpcd + dropbear.
# Ядро общее с хостом (это контейнер, не VM), но UCI/ubus/SSH —
# настоящие. forkop CLI и init-скрипт заменены заглушками.
RUN apk update \
    && apk add --no-cache ubus rpcd dropbear \
    && mkdir -p /var/run/ubus /var/lib/rpcd /etc/dropbear /tmp/podkop-mcp/backups

COPY forkop /usr/bin/forkop
COPY forkop-init /etc/init.d/forkop
COPY forkop-config /etc/config/forkop
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /usr/bin/forkop /etc/init.d/forkop /entrypoint.sh \
    && echo "1.0.5" > /etc/forkop-version

EXPOSE 22
CMD ["/entrypoint.sh"]
