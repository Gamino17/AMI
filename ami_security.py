"""Helpers de seguridad transversales: SSRF guard, header injection, etc.

Mantenemos la lógica en un módulo dedicado para que sea testeable y
reutilizable desde varios sitios (webhooks salientes, adapters telco si
hace falta, etc.).

Decisiones de diseño:

- **SSRF**: webhook URLs son user-controlled. Antes de hacer cualquier
  request saliente, resolvemos DNS y rechazamos cualquier IP en rangos
  loopback / link-local (169.254.0.0/16) / private / multicast / reserved.
  La validación se hace JUSTO antes del connect (no solo al registrar
  el webhook) para mitigar DNS rebinding.
- **No follow redirects**: instalamos un opener que rechaza cualquier
  3xx — un atacante podría registrar https://attacker.com/jump y desde
  ahí redirigir a http://169.254.169.254 (metadata cloud) o a un endpoint
  interno. Cada redirect tendría que re-validarse, lo que es complejo;
  más simple: prohibir redirects en webhooks salientes.
- **HTTPS-only opcional**: si AMI_REQUIRE_HTTPS_WEBHOOKS=1 (default en
  producción), rechazamos `http://` en URLs de webhook. En dev / test se
  desactiva para poder usar HTTP local.
"""
from __future__ import annotations
import ipaddress
import os
import socket
import urllib.parse
import urllib.request


# Hosts a los que se permite enviar webhooks aunque resuelvan a loopback/private.
# Configurable vía AMI_WEBHOOK_ALLOWED_HOSTS (CSV). Útil para tests donde el
# sink HTTP corre en 127.0.0.1.
_ALLOWLIST_HOSTS = set(
    h.strip().lower()
    for h in (os.environ.get("AMI_WEBHOOK_ALLOWED_HOSTS") or "").split(",")
    if h.strip()
)

# En producción debería ser "1". En dev/tests, "0" para poder usar http://.
REQUIRE_HTTPS_WEBHOOKS = (os.environ.get("AMI_REQUIRE_HTTPS_WEBHOOKS") or "0") == "1"


def _is_blocked_ip(ip_str: str) -> bool:
    """Devuelve True si la IP cae en cualquier rango que NO permitimos para
    webhooks salientes:
      - loopback (127.0.0.0/8, ::1)
      - link-local (169.254.0.0/16, fe80::/10) — incluye el endpoint de
        metadata de cloud providers (AWS/GCP/Azure usan 169.254.169.254)
      - private (10/8, 172.16/12, 192.168/16, fc00::/7)
      - multicast / reserved / unspecified
    """
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return True   # si no se puede parsear, mejor bloquear
    return any([
        ip.is_loopback,
        ip.is_link_local,
        ip.is_private,
        ip.is_multicast,
        ip.is_reserved,
        ip.is_unspecified,
    ])


def is_safe_webhook_url(url: str) -> tuple[bool, str | None]:
    """Valida una URL de webhook. Devuelve (ok, reason_if_blocked).

    Chequeos:
      1. Scheme: solo http o https. Si REQUIRE_HTTPS_WEBHOOKS, solo https.
      2. Hostname presente y no es una IP literal bloqueada.
      3. Resolución DNS: todas las IPs resueltas deben estar fuera de los
         rangos prohibidos (loopback, private, link-local, etc.). Si una de
         las IPs es interna, fallamos — defensa contra hosts que tienen
         records mixtos para hacer rebinding.

    Casos especiales: hostnames en AMI_WEBHOOK_ALLOWED_HOSTS se aprueban
    sin chequear (útil para sinks HTTP locales en tests)."""
    if not isinstance(url, str) or len(url) > 2048:
        return False, "invalid_url"
    try:
        parsed = urllib.parse.urlparse(url.strip())
    except ValueError:
        return False, "invalid_url"

    scheme = (parsed.scheme or "").lower()
    if scheme not in ("http", "https"):
        return False, "scheme_not_allowed"
    if REQUIRE_HTTPS_WEBHOOKS and scheme != "https":
        return False, "https_required"

    host = (parsed.hostname or "").lower()
    if not host:
        return False, "missing_host"

    # Allowlist para testing (sink en 127.0.0.1, etc.)
    if host in _ALLOWLIST_HOSTS:
        return True, None

    # Si el host es ya una IP literal, validarla directamente
    try:
        ipaddress.ip_address(host)
        if _is_blocked_ip(host):
            return False, "blocked_ip_range"
        return True, None
    except ValueError:
        pass   # no es IP literal, hay que resolverlo

    # Resolución DNS: si CUALQUIER IP resuelta es interna, rechazar
    try:
        addrinfo = socket.getaddrinfo(host, None)
    except (socket.gaierror, OSError):
        return False, "dns_resolution_failed"

    for entry in addrinfo:
        ip = entry[4][0]
        if _is_blocked_ip(ip):
            return False, "blocked_ip_range"

    return True, None


# Custom opener que rechaza cualquier redirect 3xx. urllib.request usa
# HTTPRedirectHandler por defecto que SIGUE redirects automáticamente — eso
# expone SSRF aunque la URL inicial sea segura. Este handler corta de raíz.
class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(
            req.full_url, code,
            f"redirects_disabled (would redirect to {newurl})",
            headers, fp,
        )


def build_safe_opener() -> urllib.request.OpenerDirector:
    """Devuelve un urllib opener que rechaza redirects 3xx. Usar SIEMPRE
    para requests salientes con URL user-controlled (webhooks)."""
    return urllib.request.build_opener(_NoRedirectHandler())
