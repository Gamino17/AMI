# Despliegue de AMI en Render

Guía paso a paso para publicar el mock AMI (API + MCP HTTP remoto) en Render
usando el blueprint `render.yaml` del repo.

## 1. Push del repo a GitHub

```bash
git add .
git commit -m "Configurar despliegue en Render"
git push origin main
```

Render necesita acceso al repo (público o vía la integración de GitHub de tu
cuenta).

## 2. Crear el blueprint en Render

1. Entra al dashboard de Render → **New** → **Blueprint**.
2. Conecta el repo de GitHub que acabas de pushear.
3. Render detecta `render.yaml` automáticamente y propone dos servicios:
   `ami-mock-api` y `ami-mcp-http`. Acepta y arranca el primer deploy.
4. El primer build del MCP tarda un poco más porque hace `pip install`
   (`mcp` + `httpx`). El API arranca casi instantáneo (stdlib pura).

Ambos servicios fallarán o quedarán en modo dev hasta que añadas las variables
secretas en el siguiente paso.

## 3. Setear variables marcadas `sync: false`

Las variables sensibles (claves, URLs cruzadas) no se versionan en
`render.yaml`. Hay que pegarlas manualmente en el dashboard.

### `ami-mock-api`

En **Environment** del servicio `ami-mock-api`:

- `AMI_API_KEY`: genera una clave segura y pégala. Ejemplo:
  ```bash
  openssl rand -hex 32
  ```
- `AMI_PUBLIC_URL`: la URL pública que Render asignó al servicio. La verás
  arriba a la derecha del propio dashboard del servicio. Suele ser
  `https://ami-mock-api.onrender.com` (sin barra final).

Tras guardar, Render hace redeploy automático.

### `ami-mcp-http`

En **Environment** del servicio `ami-mcp-http`:

- `AMI_API_URL`: la URL pública del primer servicio
  (la misma de `AMI_PUBLIC_URL` de arriba, p.ej.
  `https://ami-mock-api.onrender.com`).
- `AMI_API_KEY`: la **misma** clave que pegaste en `ami-mock-api`. El MCP la
  envía como `Authorization: Bearer ...` al API.

Guardar y dejar que redespliegue.

## 4. Verificación post-deploy

Sustituye `<api-url>` por la URL real de `ami-mock-api`.

```bash
# Healthcheck público (sin auth) → 200
curl https://<api-url>/v1/health

# Endpoint protegido con clave → 200
curl -H "Authorization: Bearer $AMI_API_KEY" https://<api-url>/v1/events

# Endpoint protegido sin clave → 401
curl https://<api-url>/v1/events
```

Si los tres responden como se indica, la API está sana y la auth funciona.

## 5. Probar el flujo end-to-end

Desde local, apuntando al despliegue remoto:

```bash
AMI_API_URL=https://<api-url> \
AMI_API_KEY=<la-clave> \
python3 demo_flow.py
```

Debe imprimir el camino completo `requested → offer_created → offer_accepted →
customer_data_submitted → signature_pending → signed → provisioning → active`.

## 6. Probar la página HTML de firma

El paso `create_contract` devuelve un campo `signature_url` apuntando a
`https://<api-url>/v1/sign/<contract_id>`. Cópialo y ábrelo en el navegador:
verás los datos del cliente, oferta y contrato, más un botón "Firmar contrato"
que hace POST al callback público y deja el contrato en estado `signed`.

## 7. Conectar Claude Desktop / Code al MCP

- **Stdio local apuntando al API remoto**: en la config MCP de Claude Desktop
  o Claude Code, declara un server `ami` que ejecute `python3 ami_mcp.py` con
  las variables `AMI_API_URL` y `AMI_API_KEY` apuntando al despliegue. Ver
  README para el snippet exacto.
- **HTTP remoto**: cualquier cliente MCP que soporte transporte
  `streamable-http` puede conectarse directamente a
  `https://ami-mcp-http.onrender.com/mcp` (sin instalar nada localmente).
  Recuerda que el MCP HTTP necesita la misma `AMI_API_KEY` configurada en su
  entorno para hablar con el API.

## Coste estimado

- Sólo `ami-mock-api` en plan Starter: ~$7/mes.
- Los dos servicios (API + MCP HTTP) en plan Starter: ~$14/mes.

El plan Starter se eligió para evitar cold starts durante demos en vivo. Si en
algún momento el demo no va a estar en uso continuo, puedes bajar cualquiera de
los dos servicios a plan Free desde el dashboard (Settings → Instance Type).
Implica cold starts de ~30 s tras inactividad, pero el coste pasa a $0.

## Apagado / pausa

Para suspender sin perder la config, usa **Settings → Suspend Service** en cada
servicio. El blueprint queda intacto y se puede reanudar en un click.
