#!/usr/bin/env bash
# Ramp + monitor: mide la RODILLA de concurrencia del ami_voice_bridge.
#
# Sube la carga en escalones con SIPp y, en cada nivel, muestrea CPU/mem de los
# contenedores + canales activos de Asterisk + stats por-llamada del bridge.
# La "rodilla" = el nivel donde underruns/drops o CPU cruzan tu umbral.
#
# ⚠️  EJECUTAR CONTRA UNA CAJA CLONADA, NUNCA contra la demo viva.
#     (un flood a 100 llamadas puede tumbar el trunk de producción).
#
# Prerrequisitos: sipp instalado local; ssh sin password al clon; el mock_agent
# corriendo y un MID de test con voice_url -> mock (ver README).
set -euo pipefail

# ------------------------- CONFIG (edítame) -------------------------
CLONE_SSH="${CLONE_SSH:-root@CLONE_IP}"     # ssh al VPS CLONADO
ASTERISK_IP="${ASTERISK_IP:-CLONE_IP}"      # IP SIP del clon (puerto 5060/udp)
NUMBER="${NUMBER:-TEST_MID_NUMBER}"         # número del MID de test (voice_url->mock)
LOCAL_MEDIA_IP="${LOCAL_MEDIA_IP:-$(ipconfig getifaddr en0 2>/dev/null || hostname -I | awk '{print $1}')}"
LEVELS="${LEVELS:-5 10 25 50 75 100}"       # escalones de concurrencia
HOLD="${HOLD:-120}"                          # segundos por escalón
CALL_MS="${CALL_MS:-600000}"                 # duración de cada llamada (ms) > HOLD
RATE="${RATE:-5}"                            # nuevas llamadas/segundo (rampa suave)
OUT="${OUT:-loadtest_report_$(date +%Y%m%d_%H%M%S).txt}"
# --------------------------------------------------------------------

command -v sipp >/dev/null || { echo "Falta sipp (brew install sipp / apt install sip-tester)"; exit 1; }
[ -f sipp/pcmu.pcap ] || { echo "Falta sipp/pcmu.pcap (copia el g711u.pcap de los ejemplos de SIPp)"; exit 1; }

log(){ echo "$@" | tee -a "$OUT"; }

snap(){ # snapshot de recursos del clon en el nivel actual
  ssh -o ConnectTimeout=8 "$CLONE_SSH" '
    docker stats --no-stream --format "  {{.Name}}: cpu={{.CPUPerc}} mem={{.MemUsage}}" ami_voice_bridge asterisk 2>/dev/null;
    echo -n "  asterisk_channels: "; docker exec asterisk asterisk -rx "core show channels count" 2>/dev/null | head -1;
    echo -n "  host_load: "; uptime | sed "s/.*load average/load/";
  ' 2>/dev/null | tee -a "$OUT"
}

log "=== AMI voice bridge — load test $(date) ==="
log "clon=$CLONE_SSH asterisk=$ASTERISK_IP numero=$NUMBER media_ip=$LOCAL_MEDIA_IP"
log "escalones=[$LEVELS] hold=${HOLD}s rate=${RATE}/s call=${CALL_MS}ms"
log ""

for L in $LEVELS; do
  log "----- NIVEL: $L llamadas concurrentes -----"
  # -l = límite de llamadas simultáneas; -m = total (con margen); -d duración
  sipp "$ASTERISK_IP:5060" -sf sipp/uac_pcmu.xml -s "$NUMBER" \
       -l "$L" -r "$RATE" -d "$CALL_MS" -mi "$LOCAL_MEDIA_IP" \
       -m $((L * 3)) -trace_stat -fd 5 -bg -pid /tmp/sipp_$L.pid >/dev/null 2>&1 || true
  # deja subir la rampa hasta el nivel y estabilizar
  sleep 20
  log "[t+20s]"; snap
  sleep $((HOLD - 40))
  log "[t+${HOLD}s estable]"; snap
  # stats por-llamada del bridge en la ventana
  log "[bridge stats: (underruns/drops)]"
  ssh -o ConnectTimeout=8 "$CLONE_SSH" "docker logs --since=${HOLD}s ami_voice_bridge 2>&1 | grep -i stats: | tail -5" 2>/dev/null | tee -a "$OUT" || true
  # parar SIPp de este nivel
  [ -f /tmp/sipp_$L.pid ] && kill "$(cat /tmp/sipp_$L.pid)" 2>/dev/null || true
  pkill -f "uac_pcmu.xml" 2>/dev/null || true
  log ""
  sleep 10   # drenaje entre escalones
done

log "=== FIN. Rodilla = primer nivel con underruns% alto o CPU bridge >85% o setup fails. ==="
log "Informe: $OUT"
