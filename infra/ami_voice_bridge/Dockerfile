# Imagen del container ami_voice_bridge (capa SIGNALING del puente de voz).
#
# El bridge es un proceso Python async INDEPENDIENTE de ami_api:
#   - Mantiene un WebSocket persistente contra ARI /ari/events (Stasis app
#     'ami_voice') para recibir StasisStart en tiempo real.
#   - Despacha un webhook estilo Twilio al voice_url del cliente y ejecuta el
#     TwiML resultante contra ARI REST.
#
# Python 3.12: el bridge corre aislado del backend (ami_api usa 3.11). Usar la
# 3.12-slim como pide el Paso 2; no hay acoplamiento de versión entre procesos.
FROM python:3.12-slim

WORKDIR /app

# Dependencias del bridge. A diferencia de ami_api.Dockerfile (que usa
# 'pip install ... || true' porque sus deps son opcionales), aquí websockets
# es CRÍTICO: la stdlib no trae cliente WebSocket y sin él el bridge no puede
# conectarse a ARI /ari/events. Un fallo de instalación DEBE romper el build,
# nunca arrancar silenciosamente sin el cliente WS (riesgo documentado en el
# mapa de despliegue). Por eso NO se añade '|| true'.
COPY infra/ami_voice_bridge/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Código del bridge + módulo de signaling reutilizado del Paso 1.
# El contexto de build es la raíz del repo (context '..' en compose), igual que
# ami_api, así que ami_voice_streams.py se copia desde la raíz y queda junto a
# bridge.py para el import directo. NO se reimplementa: el bridge lo importa.
COPY infra/ami_voice_bridge/bridge.py ./bridge.py
COPY ami_voice_streams.py ./ami_voice_streams.py

# Puerto del servidor /health para el healthcheck de Docker.
# NO se exponen puertos RTP en Paso 2: se añadirán en Paso 3 (externalMedia).
EXPOSE 8090

ENV PYTHONUNBUFFERED=1

# curl no está garantizado en la imagen slim: el healthcheck usa python stdlib
# (urllib) para golpear el endpoint /health del propio bridge.
HEALTHCHECK --interval=15s --timeout=3s --retries=5 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8090/health',timeout=2).status==200 else 1)"

# -u: logs sin buffer para que aparezcan en 'docker logs' en tiempo real.
CMD ["python", "-u", "bridge.py"]
