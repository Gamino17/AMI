#!/usr/bin/env python3
"""AMI MCP server (SDK oficial). Soporta stdio y HTTP streamable.

Uso:
    python ami_mcp.py                # stdio (Claude Desktop / Code / cliente local)
    python ami_mcp.py http           # streamable-http en :8001 (servidor remoto)

Variables de entorno:
    AMI_API_URL    URL base del backend AMI (default: http://localhost:8000)
    AMI_API_KEY    Bearer token enviado a la API (opcional en modo dev)
    AMI_MCP_HOST   Bind para HTTP (default: 0.0.0.0)
    AMI_MCP_PORT   Puerto para HTTP (default: 8001)
"""
from __future__ import annotations
import json, os, sys
from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP

API_URL = os.environ.get("AMI_API_URL", "http://localhost:8000").rstrip("/")
API_KEY = os.environ.get("AMI_API_KEY") or None


def _headers() -> dict[str, str]:
    h = {"Content-Type": "application/json"}
    if API_KEY:
        h["Authorization"] = f"Bearer {API_KEY}"
    return h


async def _get(path: str) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.get(API_URL + path, headers=_headers())
        return {"http_status": r.status_code, "body": _safe_json(r)}


async def _post(path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.post(API_URL + path, json=body or {}, headers=_headers())
        return {"http_status": r.status_code, "body": _safe_json(r)}


def _safe_json(r: httpx.Response) -> Any:
    try: return r.json()
    except Exception: return {"raw": r.text}


mcp = FastMCP(
    "ami",
    instructions=(
        "AMI v1 — Agent Mobile Identity Protocol. Permite a un agente solicitar, "
        "contratar y activar una identidad móvil (SIM/eSIM/número). Flujo: "
        "request_sim_offer → accept_offer → submit_customer_data → create_contract "
        "→ (firmar en signature_url) → activate_sim_identity. La SIM física es lo "
        "único simulado; el resto del flujo es real."
    ),
)


@mcp.tool(name="ami.search_sim_options",
          description="Lista países, tipos de SIM/eSIM y capacidades disponibles.")
async def search_sim_options() -> dict:
    return await _get("/v1/sim-options")


@mcp.tool(name="ami.request_sim_offer",
          description="Crea una SIMRequest y devuelve la oferta inmediata del partner telco.")
async def request_sim_offer(
    agent_name: str,
    country: str = "ES",
    sim_type: str = "eSIM",
    capabilities: list[str] | None = None,
    purpose: str = "agent_identity",
    max_monthly_price: float | None = None,
    currency: str = "EUR",
) -> dict:
    return await _post("/v1/sim-requests", {
        "country": country,
        "sim_type": sim_type,
        "capabilities": capabilities or ["sms", "voice"],
        "agent": {"name": agent_name, "purpose": purpose},
        "commercial_constraints": {
            "max_monthly_price": max_monthly_price,
            "currency": currency,
        },
    })


@mcp.tool(name="ami.accept_offer", description="Acepta una oferta antes de generar contrato.")
async def accept_offer(offer_id: str) -> dict:
    return await _post(f"/v1/offers/{offer_id}/accept")


@mcp.tool(name="ami.submit_customer_data",
          description="Envía los datos legales/fiscales del cliente y los vincula a la SIMRequest.")
async def submit_customer_data(
    sim_request_id: str,
    legal_name: str,
    tax_id: str,
    billing_email: str,
    address: str,
    representative_name: str,
) -> dict:
    return await _post(f"/v1/sim-requests/{sim_request_id}/customer-data", {
        "customer": {
            "legal_name": legal_name,
            "tax_id": tax_id,
            "billing_email": billing_email,
            "address": address,
            "representative_name": representative_name,
        }
    })


@mcp.tool(name="ami.create_contract",
          description="Genera el contrato vinculado a una oferta y un cliente. "
                      "Devuelve signature_url donde el firmante debe aceptar.")
async def create_contract(offer_id: str, customer_id: str) -> dict:
    return await _post("/v1/contracts", {"offer_id": offer_id, "customer_id": customer_id})


@mcp.tool(name="ami.get_contract_status", description="Consulta el estado actual de un contrato.")
async def get_contract_status(contract_id: str) -> dict:
    return await _get(f"/v1/contracts/{contract_id}")


@mcp.tool(name="ami.confirm_signature_status",
          description="Comprueba si el contrato ya está firmado (alias semántico de get_contract_status).")
async def confirm_signature_status(contract_id: str) -> dict:
    return await _get(f"/v1/contracts/{contract_id}")


@mcp.tool(name="ami.activate_sim_identity",
          description="Tras la firma, inicia el provisioning con el partner telco. "
                      "Devuelve la MobileIdentity activa con phone_number y QR de eSIM.")
async def activate_sim_identity(contract_id: str) -> dict:
    return await _post("/v1/mobile-identities/activate", {"contract_id": contract_id})


@mcp.tool(name="ami.get_identity_status",
          description="Consulta el estado de una MobileIdentity activa.")
async def get_identity_status(mobile_identity_id: str) -> dict:
    return await _get(f"/v1/mobile-identities/{mobile_identity_id}")


@mcp.tool(name="ami.cancel_request",
          description="Cancela una SIMRequest antes de la activación.")
async def cancel_request(sim_request_id: str, reason: str = "cancelled_by_agent") -> dict:
    return await _post(f"/v1/sim-requests/{sim_request_id}/cancel", {"reason": reason})


@mcp.tool(name="ami.list_events",
          description="Devuelve los últimos AuditEvents del backend (debug/inspección).")
async def list_events() -> dict:
    return await _get("/v1/events")


def main() -> None:
    transport = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("AMI_MCP_TRANSPORT", "stdio")
    if transport == "stdio":
        mcp.run(transport="stdio")
    elif transport == "http":
        # FastMCP activa DNS-rebinding protection por defecto y solo permite
        # hosts/origenes localhost. En producción detrás de un proxy hay que
        # añadir el hostname público a allowed_hosts (Render lo expone como
        # RENDER_EXTERNAL_HOSTNAME) o pasarlo explícito vía AMI_MCP_PUBLIC_HOST.
        # Sin esto, el server responde 421 "Invalid Host header" a Cloudflare.
        public_host = (
            os.environ.get("AMI_MCP_PUBLIC_HOST")
            or os.environ.get("RENDER_EXTERNAL_HOSTNAME")
        )
        if public_host:
            ts = mcp.settings.transport_security
            ts.allowed_hosts = list(ts.allowed_hosts) + [public_host, f"{public_host}:*"]
            ts.allowed_origins = list(ts.allowed_origins) + [
                f"https://{public_host}", f"http://{public_host}",
            ]

        # Stateless: cada llamada es independiente. Imprescindible cuando el
        # server puede correr en múltiples instancias o reciclar workers
        # (Render free/starter), porque la sesión por defecto se guarda en
        # memoria del proceso y se perdería entre requests dando "Session
        # terminated".
        mcp.settings.stateless_http = True
        mcp.settings.json_response = True

        import uvicorn
        from starlette.responses import HTMLResponse
        from starlette.routing import Route

        host = os.environ.get("AMI_MCP_HOST", "0.0.0.0")
        port = int(os.environ.get("AMI_MCP_PORT", "8001"))
        landing_url = os.environ.get("AMI_API_URL", "https://protocolami.com")

        # Página HTML amable para humanos que llegan al MCP server por curiosidad.
        # Servida en GET / (raíz) y como fallback para GET /mcp con Accept: text/html.
        page = (
            "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
            "<title>AMI MCP Server</title>"
            "<style>"
            "body{font-family:-apple-system,BlinkMacSystemFont,'Inter','Segoe UI',sans-serif;"
            "background:#08080c;color:#ededf2;display:flex;align-items:center;justify-content:center;"
            "min-height:100vh;margin:0;padding:1.5rem;line-height:1.6}"
            ".box{max-width:560px;text-align:center}"
            "h1{font-weight:700;font-size:2rem;margin:0 0 1rem;letter-spacing:-0.02em}"
            "h1 .dot{color:#8b6cff}"
            "p{color:#8888a0;margin:0 0 1.5rem}"
            "code{font-family:'JetBrains Mono','SF Mono',Menlo,monospace;background:#14141d;"
            "border:1px solid #1f1f2c;padding:0.15em 0.5em;border-radius:4px;color:#5dd1ff;font-size:0.9em}"
            ".pill{display:inline-block;font-family:'JetBrains Mono','SF Mono',Menlo,monospace;"
            "font-size:0.72rem;background:#14141d;border:1px solid #1f1f2c;color:#8888a0;"
            "padding:0.3rem 0.7rem;border-radius:999px;margin-bottom:1.5rem;letter-spacing:0.05em}"
            ".btn{display:inline-block;background:linear-gradient(180deg,#9d80ff,#7a5cff);"
            "color:#fff;text-decoration:none;padding:0.7rem 1.4rem;border-radius:8px;"
            "font-weight:500;margin-top:1rem;box-shadow:0 8px 24px -8px rgba(123,92,255,0.5)}"
            "</style></head><body><div class=\"box\">"
            "<div class=\"pill\">MCP HTTP server</div>"
            "<h1>AMI<span class=\"dot\">.</span></h1>"
            "<p>This URL is the <strong>streamable-http MCP endpoint</strong> for AI agents, "
            "not a website. Point any MCP-compatible client (Claude Desktop, OpenClaw, "
            "custom SDK, etc.) at <code>/mcp</code> and it will see the 11 <code>ami.*</code> tools.</p>"
            "<p>If you arrived here from a browser, you probably want the AMI landing page:</p>"
            f"<a class=\"btn\" href=\"{landing_url}\">Go to ami-mock-api &rarr;</a>"
            "</div></body></html>"
        )

        async def root(request):
            return HTMLResponse(page)

        async def mcp_browser(request):
            # GET /mcp con Accept: text/html → landing amable; otros casos → 405
            # (los POSTs reales del MCP los maneja el handler de FastMCP montado abajo).
            if "text/html" in request.headers.get("accept", ""):
                return HTMLResponse(page)
            return HTMLResponse(
                "Method Not Allowed (use POST with Accept: application/json, text/event-stream)",
                status_code=405,
            )

        app = mcp.streamable_http_app()
        # Inserta las rutas amables al principio para que tengan prioridad sobre
        # cualquier 404 catch-all del app de FastMCP.
        app.routes.insert(0, Route("/", root, methods=["GET"]))
        app.routes.insert(1, Route("/mcp", mcp_browser, methods=["GET"]))

        uvicorn.run(
            app, host=host, port=port,
            proxy_headers=True, forwarded_allow_ips="*",
            log_level="info",
        )
    else:
        print(f"Unknown transport: {transport!r}. Use 'stdio' or 'http'.", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
