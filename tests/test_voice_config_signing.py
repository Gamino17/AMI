"""Tests de integración HTTP de la firma X-AMI-Signature como feature
first-class del aprovisionamiento AMI.

Cubre el ciclo de vida del `signing_secret` a través de la superficie HTTP real
(ThreadingHTTPServer de conftest), siguiendo el patrón de tests/test_calls.py:

  - POST /v1/mobile-identities/activate    (auto-issue + explícito + redacción)
  - POST /v1/mobile-identities/{mid}/voice-config  (auto-issue + explícito + 400)
  - GET  /v1/mobile-identities/{mid}/voice-config  (SIEMPRE redactado)
  - POST /v1/mobile-identities/{mid}/rotate-signing-secret

Contrato autoritativo de la firma (ya entregado a openclaw, NO cambia):
  - secret = 'amiwhsec_' + 64 hex.
  - El secreto se muestra EN CLARO UNA SOLA VEZ (como agent_token): en la
    respuesta de activate (si hubo voice_url) y en la de voice-config-set, más
    en la rotación. NUNCA en GET, NUNCA en events/audit, NUNCA en logs.

NOTA: estos tests validan el contrato de producto. Si la implementación de
ami_api.py aún no está mergeada, fallarán hasta que lo esté — son el criterio
de aceptación de esa feature, deterministas y sin red real (loopback de
conftest).
"""
from __future__ import annotations

import json

import pytest


# El secreto exacto de la demo (mid_0a7e2f9640) que openclaw verificará. Debe
# poder fijarse explícitamente para el bootstrap.
DEMO_SECRET = "amiwhsec_d0b569ae40f2d9096c8a5e8f0bfc9d2c2eb7d89dd7c302c3af9470ba70f1ec4f"
SECRET_PREFIX = "amiwhsec_"

VOICE_URL = "https://agent.example.com/voice/webhook"


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _provision(client, sample_customer_payload, activate_extra=None):
    """Flujo completo SIMRequest->oferta->contrato->firma->activate. Devuelve
    la respuesta httpx de activate (con activate_extra fusionado en el body).

    Mismo patrón que tests/test_calls.py::_provision para no acoplarnos al
    fixture active_identity cuando necesitamos controlar el body de activate
    (p.ej. inyectar voice_url + signing_secret).
    """
    r = client.post("/v1/sim-requests", json={"country": "ES", "sim_type": "eSIM"})
    sim = r.json()["sim_request"]
    offer = r.json()["offer"]
    client.post(f"/v1/offers/{offer['id']}/accept")
    cust = client.post(f"/v1/sim-requests/{sim['id']}/customer-data",
                       json={"customer": sample_customer_payload})
    customer = cust.json()["customer"]
    contract = client.post("/v1/contracts",
                           json={"offer_id": offer["id"], "customer_id": customer["id"]})
    contract_id = contract.json()["id"]
    client.post(f"/v1/contracts/{contract_id}/mock-sign")
    payload = {"contract_id": contract_id}
    payload.update(activate_extra or {})
    return client.post("/v1/mobile-identities/activate", json=payload)


def _is_amiwhsec(value) -> bool:
    """True si `value` es un signing_secret EN CLARO bien formado."""
    return (isinstance(value, str)
            and value.startswith(SECRET_PREFIX)
            and len(value) == len(SECRET_PREFIX) + 64)


def _is_redacted(value) -> bool:
    """True si `value` está redactado (prefijo+sufijo, NO el secreto entero).

    El helper _redact_secret del backend produce algo como
    'amiwhsec_d0b5...ec4f': empieza por el prefijo, contiene '...' y es mucho
    más corto que el secreto en claro. Aceptamos también un flag/marcador
    genérico como 'set' por si la implementación usa esa forma — lo único
    inadmisible es que aparezca el secreto entero.
    """
    if value is None:
        return True
    if not isinstance(value, str):
        return False
    if _is_amiwhsec(value):
        # El valor en claro NUNCA cuenta como redactado.
        return False
    return "..." in value or value == "set"


def _stored_secret(ami_api_module, mid):
    """Lee el signing_secret persistido en STATE (en claro, como el resto de
    secretos del repo: coherente con el modelo, no se cifra en reposo)."""
    identity = ami_api_module.STATE["mobile_identities"].get(mid)
    assert identity is not None, f"MID {mid} no existe en STATE"
    vc = identity.get("voice_config")
    assert vc is not None, f"MID {mid} no tiene voice_config en STATE"
    return vc.get("signing_secret")


def _all_event_blobs(ami_api_module) -> str:
    """Serializa TODOS los events de STATE a un único string para asertar que
    el secreto en claro no aparece en ningún payload de auditoría."""
    return json.dumps(ami_api_module.STATE["events"], ensure_ascii=False)


# --------------------------------------------------------------------------- #
# activate: auto-issue + explícito + redacción
# --------------------------------------------------------------------------- #

def test_activate_auto_issues_secret_once(client, sample_customer_payload, ami_api_module):
    """Activar con voice_url y SIN signing_secret -> AMI auto-genera el secreto,
    lo devuelve EN CLARO una sola vez + hint, y el voice_config de la respuesta
    va REDACTADO (no el secreto entero)."""
    r = _provision(client, sample_customer_payload, {"voice_url": VOICE_URL})
    assert r.status_code == 201, r.text
    body = r.json()

    # El secreto en claro aparece UNA VEZ a top level, bien formado.
    assert _is_amiwhsec(body.get("signing_secret")), body.get("signing_secret")
    assert body.get("signing_secret_hint"), "falta el hint de 'solo se muestra una vez'"

    # El voice_config embebido en la respuesta NO debe llevar el secreto en claro.
    vc = body.get("voice_config")
    assert vc is not None, "activate con voice_url debe traer voice_config"
    assert vc["voice_url"] == VOICE_URL
    assert _is_redacted(vc.get("signing_secret")), vc.get("signing_secret")

    # Persistido en STATE en claro y coincide con el devuelto.
    assert _stored_secret(ami_api_module, body["id"]) == body["signing_secret"]


def test_activate_without_voice_url_has_no_signing_secret(client, sample_customer_payload):
    """Sin voice_url no hay webhook que firmar -> ni voice_config ni
    signing_secret. No-breaking respecto al comportamiento previo."""
    r = _provision(client, sample_customer_payload)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body.get("voice_config") is None
    assert body.get("signing_secret") is None


def test_activate_explicit_secret_bootstrap(client, sample_customer_payload, ami_api_module):
    """Bootstrap de la demo: activar con voice_url + signing_secret EXPLÍCITO
    (el secreto exacto de la demo) -> AMI lo respeta tal cual y lo persiste."""
    r = _provision(client, sample_customer_payload,
                   {"voice_url": VOICE_URL, "signing_secret": DEMO_SECRET})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body.get("signing_secret") == DEMO_SECRET
    assert _stored_secret(ami_api_module, body["id"]) == DEMO_SECRET
    # En la copia del voice_config sigue redactado.
    assert _is_redacted(body["voice_config"].get("signing_secret"))


def test_activate_explicit_secret_invalid_prefix_is_400(client, sample_customer_payload):
    """Un signing_secret explícito sin el prefijo 'amiwhsec_' se rechaza con
    400 antes de provisionar (no se firma silenciosamente con basura)."""
    r = _provision(client, sample_customer_payload,
                   {"voice_url": VOICE_URL, "signing_secret": "deadbeef_not_a_secret"})
    assert r.status_code == 400, r.text
    assert r.json()["error"] == "invalid_signing_secret"


# --------------------------------------------------------------------------- #
# POST voice-config: auto-issue + explícito + validación de prefijo
# --------------------------------------------------------------------------- #

def test_post_voice_config_auto_issues_secret_once(client, active_identity, ami_api_module):
    """POST voice-config sin signing_secret -> auto-genera, lo devuelve EN CLARO
    una vez + hint, voice_config redactado en la respuesta."""
    mid = active_identity["mid"]
    r = client.post(f"/v1/mobile-identities/{mid}/voice-config",
                    json={"voice_url": VOICE_URL})
    assert r.status_code == 200, r.text
    body = r.json()

    assert _is_amiwhsec(body.get("signing_secret")), body.get("signing_secret")
    assert body.get("signing_secret_hint")

    vc = body["voice_config"]
    assert vc["voice_url"] == VOICE_URL
    assert vc["id"].startswith("vcfg_")
    assert _is_redacted(vc.get("signing_secret")), vc.get("signing_secret")

    assert _stored_secret(ami_api_module, mid) == body["signing_secret"]


def test_post_voice_config_explicit_secret(client, active_identity, ami_api_module):
    """POST voice-config con signing_secret explícito -> lo respeta y persiste."""
    mid = active_identity["mid"]
    r = client.post(f"/v1/mobile-identities/{mid}/voice-config",
                    json={"voice_url": VOICE_URL, "signing_secret": DEMO_SECRET})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["signing_secret"] == DEMO_SECRET
    assert _stored_secret(ami_api_module, mid) == DEMO_SECRET
    assert _is_redacted(body["voice_config"].get("signing_secret"))


def test_post_voice_config_explicit_invalid_prefix_is_400(client, active_identity):
    """signing_secret explícito malformado -> 400 invalid_signing_secret."""
    mid = active_identity["mid"]
    r = client.post(f"/v1/mobile-identities/{mid}/voice-config",
                    json={"voice_url": VOICE_URL, "signing_secret": "nope_123"})
    assert r.status_code == 400, r.text
    assert r.json()["error"] == "invalid_signing_secret"


def test_clear_voice_config_drops_secret(client, active_identity, ami_api_module):
    """Limpiar el voice_config (voice_url=null) elimina también el secreto:
    no debe quedar un signing_secret huérfano firmando nada."""
    mid = active_identity["mid"]
    client.post(f"/v1/mobile-identities/{mid}/voice-config",
                json={"voice_url": VOICE_URL})
    r = client.post(f"/v1/mobile-identities/{mid}/voice-config",
                    json={"voice_url": None})
    assert r.status_code == 200, r.text
    assert r.json()["voice_config"] is None
    assert ami_api_module.STATE["mobile_identities"][mid].get("voice_config") is None


# --------------------------------------------------------------------------- #
# GET voice-config: NUNCA devuelve el secreto en claro
# --------------------------------------------------------------------------- #

def test_get_voice_config_never_returns_secret_in_clear(client, active_identity, ami_api_module):
    """Tras fijar el secreto de la demo, GET voice-config lo devuelve REDACTADO
    (prefijo+sufijo / flag), jamás el valor entero — aunque en STATE esté en
    claro."""
    mid = active_identity["mid"]
    set_resp = client.post(f"/v1/mobile-identities/{mid}/voice-config",
                           json={"voice_url": VOICE_URL, "signing_secret": DEMO_SECRET})
    assert set_resp.status_code == 200

    r = client.get(f"/v1/mobile-identities/{mid}/voice-config")
    assert r.status_code == 200, r.text
    vc = r.json()["voice_config"]
    assert vc["voice_url"] == VOICE_URL

    redacted = vc.get("signing_secret")
    assert _is_redacted(redacted), redacted
    # Doble verificación explícita: el secreto entero NO está en el GET.
    assert DEMO_SECRET not in r.text
    # Pero el secreto SÍ sigue intacto en STATE (no lo destruimos al redactar).
    assert _stored_secret(ami_api_module, mid) == DEMO_SECRET


def test_get_voice_config_no_secret_when_unset(client, active_identity):
    """GET de un MID sin voice_config no expone nada raro."""
    mid = active_identity["mid"]
    r = client.get(f"/v1/mobile-identities/{mid}/voice-config")
    assert r.status_code == 200, r.text
    assert r.json()["voice_config"] is None


# --------------------------------------------------------------------------- #
# rotate-signing-secret
# --------------------------------------------------------------------------- #

def test_rotate_signing_secret_changes_and_returns_once(client, active_identity, ami_api_module):
    """POST rotate-signing-secret rota el secreto: lo devuelve EN CLARO una vez,
    invalida el anterior, y persiste el nuevo en STATE."""
    mid = active_identity["mid"]
    set_resp = client.post(f"/v1/mobile-identities/{mid}/voice-config",
                           json={"voice_url": VOICE_URL})
    old_secret = set_resp.json()["signing_secret"]
    assert _is_amiwhsec(old_secret)

    r = client.post(f"/v1/mobile-identities/{mid}/rotate-signing-secret")
    assert r.status_code == 200, r.text
    body = r.json()
    new_secret = body.get("signing_secret")
    assert _is_amiwhsec(new_secret), new_secret
    assert new_secret != old_secret, "la rotación debe cambiar el secreto"
    assert body.get("rotated_at")
    assert body.get("signing_secret_hint")

    # El nuevo es el que queda persistido; el viejo ya no firma.
    assert _stored_secret(ami_api_module, mid) == new_secret


def test_rotate_signing_secret_explicit(client, active_identity, ami_api_module):
    """Se puede rotar a un secreto explícito (re-bootstrap), validando prefijo."""
    mid = active_identity["mid"]
    client.post(f"/v1/mobile-identities/{mid}/voice-config",
                json={"voice_url": VOICE_URL})

    r = client.post(f"/v1/mobile-identities/{mid}/rotate-signing-secret",
                    json={"signing_secret": DEMO_SECRET})
    assert r.status_code == 200, r.text
    assert r.json()["signing_secret"] == DEMO_SECRET
    assert _stored_secret(ami_api_module, mid) == DEMO_SECRET


def test_rotate_signing_secret_explicit_invalid_prefix_is_400(client, active_identity):
    mid = active_identity["mid"]
    client.post(f"/v1/mobile-identities/{mid}/voice-config",
                json={"voice_url": VOICE_URL})
    r = client.post(f"/v1/mobile-identities/{mid}/rotate-signing-secret",
                    json={"signing_secret": "bad"})
    assert r.status_code == 400, r.text
    assert r.json()["error"] == "invalid_signing_secret"


def test_rotate_signing_secret_without_voice_config_is_409(client, active_identity):
    """Rotar sin voice_config previo -> 409: no hay nada que firmar todavía."""
    mid = active_identity["mid"]
    r = client.post(f"/v1/mobile-identities/{mid}/rotate-signing-secret")
    assert r.status_code == 409, r.text
    assert r.json()["error"] == "voice_config_not_set"


def test_rotate_signing_secret_unknown_mid_is_404(client, active_identity):
    r = client.post("/v1/mobile-identities/mid_doesnotexist/rotate-signing-secret")
    assert r.status_code == 404, r.text


def test_rotate_signing_secret_scoped_other_account_is_404(
        client, anon_client, active_identity):
    """Un caller sin la API key del customer (anónimo) no puede rotar: el gate
    de auth del customer debe cortar antes (401)."""
    mid = active_identity["mid"]
    client.post(f"/v1/mobile-identities/{mid}/voice-config",
                json={"voice_url": VOICE_URL})
    r = anon_client.post(f"/v1/mobile-identities/{mid}/rotate-signing-secret")
    assert r.status_code == 401, r.text


# --------------------------------------------------------------------------- #
# El secreto NUNCA en events/audit
# --------------------------------------------------------------------------- #

def test_secret_not_in_events_after_activate(client, sample_customer_payload, ami_api_module):
    r = _provision(client, sample_customer_payload, {"voice_url": VOICE_URL})
    assert r.status_code == 201, r.text
    secret = r.json()["signing_secret"]
    assert _is_amiwhsec(secret)
    # Ni el secreto entero ni en STATE['events'] ni en el endpoint /v1/events.
    assert secret not in _all_event_blobs(ami_api_module)
    events_text = client.get("/v1/events").text
    assert secret not in events_text


def test_secret_not_in_events_after_voice_config_set(client, active_identity, ami_api_module):
    mid = active_identity["mid"]
    r = client.post(f"/v1/mobile-identities/{mid}/voice-config",
                    json={"voice_url": VOICE_URL, "signing_secret": DEMO_SECRET})
    assert r.status_code == 200, r.text
    assert DEMO_SECRET not in _all_event_blobs(ami_api_module)
    assert DEMO_SECRET not in client.get("/v1/events").text


def test_secret_not_in_events_after_rotation(client, active_identity, ami_api_module):
    mid = active_identity["mid"]
    client.post(f"/v1/mobile-identities/{mid}/voice-config",
                json={"voice_url": VOICE_URL})
    r = client.post(f"/v1/mobile-identities/{mid}/rotate-signing-secret")
    assert r.status_code == 200, r.text
    rotated = r.json()["signing_secret"]
    assert rotated not in _all_event_blobs(ami_api_module)
    assert rotated not in client.get("/v1/events").text


def test_secret_never_in_any_response_field_except_designated(
        client, active_identity, ami_api_module):
    """Defensa en profundidad: el secreto en claro solo puede vivir en el campo
    top-level `signing_secret` de las respuestas designadas (set/rotate/activate)
    y en STATE; en GET voice-config no aparece en NINGUNA parte del cuerpo."""
    mid = active_identity["mid"]
    set_resp = client.post(f"/v1/mobile-identities/{mid}/voice-config",
                           json={"voice_url": VOICE_URL, "signing_secret": DEMO_SECRET})
    assert set_resp.status_code == 200
    # En el SET el secreto SÍ aparece (una vez), pero solo en el campo dedicado:
    set_body = set_resp.json()
    assert set_body["signing_secret"] == DEMO_SECRET
    # ...y el voice_config embebido NO lo trae en claro.
    assert DEMO_SECRET not in json.dumps(set_body["voice_config"])

    # En GET no aparece en ninguna parte del cuerpo.
    get_resp = client.get(f"/v1/mobile-identities/{mid}/voice-config")
    assert DEMO_SECRET not in get_resp.text

    # En el GET genérico del MID tampoco (lectura del record completo).
    full = client.get(f"/v1/mobile-identities/{mid}")
    if full.status_code == 200:
        assert DEMO_SECRET not in full.text
