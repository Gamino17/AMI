# Kannel SMSC HTTP gateway desde apt de Debian.
#
# Razón: no existe imagen oficial mantenida de `kannel/kannel` en Docker Hub
# (la que estaba referenciada no resolvía). Compilamos desde el paquete
# Debian, que es estable, mantenido y soporta SMPP/HTTP de serie.
#
# El paquete `kannel` instala `bearerbox` (el SMSC) y `smsbox` (la pasarela
# HTTP). Ambos arrancan desde `/etc/kannel/kannel.conf` (montado como volume
# en docker-compose.yml).
#
# Puertos:
#   - 13000/tcp : admin / status
#   - 13013/tcp : sendsms HTTP

FROM debian:bookworm-slim

RUN apt-get update \
 && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
      kannel \
      ca-certificates \
      gettext-base \
 && apt-get clean \
 && rm -rf /var/lib/apt/lists/* \
 && (getent group kannel >/dev/null || groupadd -r kannel) \
 && (id -u kannel >/dev/null 2>&1 || useradd -r -g kannel -d /var/spool/kannel -s /usr/sbin/nologin kannel) \
 && mkdir -p /var/spool/kannel /var/log/kannel \
 && chown -R kannel:kannel /var/spool/kannel /var/log/kannel

EXPOSE 13000 13013

# Entrypoint: si la conf tiene ${VAR}, hacer envsubst antes de arrancar.
# Bearerbox primero, smsbox después (mismo proceso vía supervisor mínimo).
COPY --chmod=0755 <<'EOF' /usr/local/bin/start-kannel.sh
#!/bin/bash
set -e

# Si kannel.conf tiene placeholders ${VAR}, expandirlos a /tmp para no
# modificar el bind-mount read-only.
CONF_SRC=/etc/kannel/kannel.conf
CONF_RUN=/tmp/kannel.conf
if grep -q '\${' "$CONF_SRC"; then
  envsubst < "$CONF_SRC" > "$CONF_RUN"
else
  cp "$CONF_SRC" "$CONF_RUN"
fi

# Arranca bearerbox en background y smsbox en foreground (PID 1).
# Si bearerbox muere, el container reinicia (restart: unless-stopped).
bearerbox -v 1 "$CONF_RUN" &
BEAR_PID=$!
sleep 2
exec smsbox -v 1 "$CONF_RUN"
EOF

CMD ["/usr/local/bin/start-kannel.sh"]
