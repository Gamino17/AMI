#!/bin/bash
# ami_inbound.sh — wrapper para el dialplan de entrantes en Asterisk.
#
# Invocado desde extensions.conf como:
#   SHELL(/var/lib/asterisk/scripts/ami_inbound.sh "$CLI" "$EXTEN" "$CHAN" "$URL" "$KEY")
#
# Hace UNA SOLA petición POST a /v1/_telco/calls/inbound con el body
# {from, to, telco_ref}. AMI responde:
#
#   - 201 + {forward_sip_uri, call_id, mid}  → la imprimimos en una línea.
#   - 409 + {error: "no_endpoint"}           → imprime una línea con campos vacíos.
#   - otro                                    → idem (caller decide reject).
#
# Output (UNA línea, parseable con CUT en dialplan):
#   FORWARD=sip:user@host:port|CALL_ID=call_xxx|MID=mid_xxx
# o, en caso de error/409:
#   FORWARD=|CALL_ID=|MID=
#
# NO depende de jq — usamos sed/grep para extraer los campos del JSON simple
# que devuelve AMI. La estructura del JSON es plana (sin objetos anidados),
# así que un regex inline es suficiente y robusto.

set -eu

CALLER="$1"
DEST="$2"
CHAN="$3"
AMI_URL="$4"
TELCO_KEY="$5"

# Cuerpo JSON. Las llaves se escapan en Bash con dobles llaves.
BODY=$(printf '{"from":"%s","to":"%s","telco_ref":"%s"}' "$CALLER" "$DEST" "$CHAN")

# UNA llamada. -m 5 = max 5 s, -s silent, -f fail-on-http-error (-w no).
# Capturamos body + http_code para distinguir 201 vs 409 vs error.
RESP=$(curl -s -m 5 -X POST \
  -H "X-Telco-Key: $TELCO_KEY" \
  -H "Content-Type: application/json" \
  -w "\n%{http_code}" \
  -d "$BODY" \
  "$AMI_URL/v1/_telco/calls/inbound" 2>/dev/null || true)

# El body es todo menos la última línea; el code es la última.
HTTP_CODE=$(printf '%s' "$RESP" | tail -n1)
HTTP_BODY=$(printf '%s' "$RESP" | sed '$d')

if [ "$HTTP_CODE" != "201" ]; then
  echo "FORWARD=|CALL_ID=|MID="
  exit 0
fi

# Extracción tipo `"forward_sip_uri":"sip:foo@bar:5060"` → "sip:foo@bar:5060".
# Tolerante a orden de campos y espacios entre `:`.
extract() {
  printf '%s' "$1" | sed -n 's/.*"'"$2"'"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | head -n1
}

FORWARD=$(extract "$HTTP_BODY" "forward_sip_uri")
CALL_ID=$(extract "$HTTP_BODY" "call_id")
MID=$(extract "$HTTP_BODY" "mid")

echo "FORWARD=${FORWARD}|CALL_ID=${CALL_ID}|MID=${MID}"
