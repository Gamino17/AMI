# Imagen mínima para ami_api. Python stdlib pura — solo necesitamos el
# intérprete; nada de pip si no hay requirements.txt con entries activas.
FROM python:3.11-slim

WORKDIR /app

# Copiar TODOS los módulos ami_*.py (son ~30: webhooks, limits, panel,
# storage, metrics, kyc, notify, backup, log, etc.) más el script install.
# Antes solo se copiaban ami_api.py y ami_mcp.py, y los imports dentro
# del backend fallaban con ModuleNotFoundError.
COPY ami_*.py install.sh ./
COPY ami_telco/ ./ami_telco/
COPY docs/ ./docs/
COPY requirements.txt ./

# Si requirements.txt tiene deps (uvicorn/mcp para el server HTTP), instalarlas.
# El backend REST por sí solo no las necesita; el MCP HTTP sí.
RUN pip install --no-cache-dir -r requirements.txt || true

EXPOSE 8000
ENV PYTHONUNBUFFERED=1

CMD ["python", "ami_api.py"]
