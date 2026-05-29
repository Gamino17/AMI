# Asterisk PBX desde apt de Ubuntu 24.04 (noble).
#
# Razón: no hay imagen oficial de Asterisk en Docker Hub. Las comunitarias
# van y vienen. Debian bookworm removió Asterisk de main en una actualización
# reciente (problemas de mantenedor) — Ubuntu noble sigue empaquetando
# Asterisk 20.x (LTS) en main y es la base más estable hoy.
#
# Necesitamos curl + bash en el container porque el dialplan
# (extensions.conf) lanza `curl` para notificar a AMI los cambios de estado
# de llamada vía /v1/_telco/calls/*.

FROM ubuntu:24.04

RUN apt-get update \
 && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
      asterisk \
      asterisk-modules \
      curl \
      ca-certificates \
      bash \
      gettext-base \
      procps \
 && apt-get clean \
 && rm -rf /var/lib/apt/lists/*

# Asterisk corre como user `asterisk`. Aseguramos los dirs runtime con
# permisos correctos (los volumes de config se montan read-only).
RUN mkdir -p /var/run/asterisk /var/lib/asterisk /var/spool/asterisk /var/log/asterisk \
 && chown -R asterisk:asterisk /var/run/asterisk /var/lib/asterisk /var/spool/asterisk /var/log/asterisk

# Puertos:
#  5060 UDP/TCP : SIP
#  5061 TCP     : SIP TLS (preparado, no obligado)
#  10000-10100 UDP : rango RTP (ver rtp.conf)
#  8088 TCP     : ARI / HTTP de Asterisk
EXPOSE 5060/udp 5060/tcp 5061/tcp 8088/tcp 10000-10100/udp

# Wrapper que aplica envsubst a los configs con placeholders ${VAR}.
# Asterisk no soporta env vars nativamente: el dialplan extension.conf usa
# ${AMI_API} y ${TELCO_KEY}, lo resolvemos expandiendo a /etc/asterisk/.
COPY --chmod=0755 <<'EOF' /usr/local/bin/start-asterisk.sh
#!/bin/bash
set -e

# Render: copia los templates de /etc/asterisk-templates/ a /etc/asterisk/,
# expandiendo placeholders ${VAR} con envsubst. Los templates están RO
# (bind-mounted desde el host); /etc/asterisk/ es RW dentro del container.
TEMPLATES_DIR=/etc/asterisk-templates
TARGET_DIR=/etc/asterisk

if [ -d "$TEMPLATES_DIR" ]; then
  for src in "$TEMPLATES_DIR"/*.conf; do
    [ -f "$src" ] || continue
    name="$(basename "$src")"
    dst="$TARGET_DIR/$name"
    if grep -q '\${' "$src" 2>/dev/null; then
      envsubst < "$src" > "$dst"
    else
      cp "$src" "$dst"
    fi
    chown asterisk:asterisk "$dst" 2>/dev/null || true
    chmod 0644 "$dst" 2>/dev/null || true
  done
fi

# Permisos del directorio de scripts del dialplan (curl + parser inbound).
if [ -d /var/lib/asterisk/scripts ]; then
  chmod -R +x /var/lib/asterisk/scripts || true
fi

# Foreground, verbose nivel 3, drop privs a user asterisk. Sin -p (priority
# scheduler) porque requiere CAP_SYS_NICE que el container no tiene.
exec asterisk -f -vvv -U asterisk -G asterisk
EOF

CMD ["/usr/local/bin/start-asterisk.sh"]
