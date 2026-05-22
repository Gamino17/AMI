# Imagen mínima para ami_api. Python stdlib pura — solo necesitamos el
# intérprete; nada de pip si no hay requirements.txt con entries activas.
FROM python:3.11-slim

WORKDIR /app

# Copiar el código fuente (no incluimos tests/, docs/, .venv/, etc.).
COPY ami_api.py ami_mcp.py install.sh ./
COPY ami_telco/ ./ami_telco/
COPY docs/ ./docs/
COPY requirements.txt ./

# Si requirements.txt tiene deps (uvicorn/mcp para el server HTTP), instalarlas.
# El backend REST por sí solo no las necesita; el MCP HTTP sí.
RUN pip install --no-cache-dir -r requirements.txt || true

EXPOSE 8000
ENV PYTHONUNBUFFERED=1

CMD ["python", "ami_api.py"]
