"""Tests del endpoint INTERNO `GET /v1/_telco/voice-config/{mid}` (X-Telco-Key).

Este endpoint es maquina-a-maquina: el bridge de voz (ami_voice_bridge) lo
consulta — con la clave compartida del partner telco — para obtener el
`signing_secret` con el que firmar (X-AMI-Signature) el webhook que dispara
hacia el `voice_url` del agente. Es el UNICO sitio donde el secreto de firma
viaja en claro a otra maquina, y SOLO bajo X-Telco-Key; jamas al customer ni
en logs/dialplan.

Contrato verificado aqui:
  - 200 con X-Telco-Key correcta -> {mid, voice_url, signing_secret}.
  - 401 con X-Telco-Key invalida (compare_digest).
  - 503 si AMI_TELCO_INBOUND_KEY no esta configurada (telco_inbound_disabled).
  - 404 si el MID no existe o no tiene voice_config con voice_url.
  - El customer-auth NO aplica: una API key de customer SIN X-Telco-Key no se
    cuela (debe fallar 401/503 igual que cualquier cliente sin la clave telco).

Patrones reutilizados:
  - fixture `telco_key` (monkeypatch de ami_api_module.TELCO_INBOUND_KEY),
    identico al de tests/test_calls.py:371-374.
  - fixtures client / anon_client / active_identity de tests/conftest.py.
  - el patron X-Telco-Key de los otros endpoints /v1/_telco/* (test_calls.py).
"""
from __future__ import annotations

import pytest


# voice_url de prueba (mismo valor canonico que tests/test_calls.py:242).
VOICE_URL = "https://agent.example.com/voice/webhook"

# Secreto de firma de la demo (el que openclaw verificara). Sirve como vector
# determinista para fijar uno EXPLICITO en el MID.
DEMO_SECRET = (
    "amiwhsec_d0b569ae40f2d9096c8a5e8f0bfc9d2c2eb7d89dd7c302c3af9470ba70f1ec4f"
)

INTERNAL_PATH = "/v1/_telco/voice-config/{mid}"


# ───────────────────────── fixtures ─────────────────────────

@pytest.fixture
def telco_key(ami_api_module, monkeypatch):
    """Configura una AMI_TELCO_INBOUND_KEY de prueba para los endpoints _telco.

    Identico al fixture de tests/test_calls.py:371-374. Sin este fixture el
    modulo arranca con TELCO_INBOUND_KEY=None y los _telco devuelven 503.
    """
    monkeypatch.setattr(ami_api_module, "TELCO_INBOUND_KEY", "test_telco_key_xyz")
    yield "test_telco_key_xyz"


@pytest.fixture
def voice_mid(client, active_identity):
    """Activa la voice-config (con signing_secret explicito de la demo) sobre el
    MID ya provisionado y devuelve {mid, secret}.

    Usamos el secreto explicito para tener un valor determinista que asertar en
    el 200 del endpoint interno.
    """
    mid = active_identity["mid"]
    r = client.post(
        f"/v1/mobile-identities/{mid}/voice-config",
        json={"voice_url": VOICE_URL, "signing_secret": DEMO_SECRET},
    )
    assert r.status_code == 200, r.text
    return {"mid": mid, "secret": DEMO_SECRET}


# ──────────────────── 200: clave correcta ────────────────────

def test_internal_endpoint_returns_secret_with_telco_key(
        anon_client, voice_mid, telco_key):
    """Con X-Telco-Key correcta, el bridge recibe voice_url + signing_secret."""
    r = anon_client.get(
        INTERNAL_PATH.format(mid=voice_mid["mid"]),
        headers={"X-Telco-Key": telco_key},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["mid"] == voice_mid["mid"]
    assert body["voice_url"] == VOICE_URL
    # El endpoint interno SI devuelve el secreto en claro (es el unico canal
    # maquina-a-maquina autorizado, gated por X-Telco-Key).
    assert body["signing_secret"] == voice_mid["secret"]


def test_internal_endpoint_secret_is_full_value_not_redacted(
        anon_client, voice_mid, telco_key):
    """Aqui (y SOLO aqui) el secreto viaja entero, no redactado.

    A diferencia del GET voice-config del customer (que lo redacta), el bridge
    necesita el valor completo para poder firmar. Verificamos que no esta
    truncado/redactado (no contiene '...').
    """
    r = anon_client.get(
        INTERNAL_PATH.format(mid=voice_mid["mid"]),
        headers={"X-Telco-Key": telco_key},
    )
    assert r.status_code == 200, r.text
    secret = r.json()["signing_secret"]
    assert secret.startswith("amiwhsec_")
    assert "..." not in secret
    assert len(secret) == len(DEMO_SECRET)


# ──────────────────── 401: clave invalida ────────────────────

def test_internal_endpoint_401_wrong_key(anon_client, voice_mid, telco_key):
    """Una X-Telco-Key que no coincide -> 401 invalid_telco_key (compare_digest)."""
    r = anon_client.get(
        INTERNAL_PATH.format(mid=voice_mid["mid"]),
        headers={"X-Telco-Key": "obviously-wrong-key"},
    )
    assert r.status_code == 401
    assert r.json()["error"] == "invalid_telco_key"


def test_internal_endpoint_401_empty_key(anon_client, voice_mid, telco_key):
    """X-Telco-Key vacia (presente pero "") tampoco se cuela -> 401."""
    r = anon_client.get(
        INTERNAL_PATH.format(mid=voice_mid["mid"]),
        headers={"X-Telco-Key": ""},
    )
    assert r.status_code == 401
    assert r.json()["error"] == "invalid_telco_key"


def test_internal_endpoint_401_missing_key_header(anon_client, voice_mid, telco_key):
    """Sin el header X-Telco-Key (pero con la clave configurada) -> 401."""
    r = anon_client.get(INTERNAL_PATH.format(mid=voice_mid["mid"]))
    assert r.status_code == 401
    assert r.json()["error"] == "invalid_telco_key"


# ──────────────────── 503: sin clave configurada ────────────────────

def test_internal_endpoint_503_no_key_configured(anon_client, voice_mid):
    """Sin AMI_TELCO_INBOUND_KEY en el servidor (no usamos el fixture telco_key)
    el endpoint _telco esta deshabilitado -> 503 telco_inbound_disabled.

    NOTA: no inyectamos `telco_key`, asi que TELCO_INBOUND_KEY queda en su valor
    por defecto (None) en el modulo bajo test.
    """
    r = anon_client.get(
        INTERNAL_PATH.format(mid=voice_mid["mid"]),
        headers={"X-Telco-Key": "whatever"},
    )
    assert r.status_code == 503
    assert r.json()["error"] == "telco_inbound_disabled"


# ──────────────────── 404: sin voice_config ────────────────────

def test_internal_endpoint_404_no_voice_config(
        anon_client, active_identity, telco_key):
    """MID valido pero SIN voice-config (no se llamo a voice-config) -> 404.

    Usamos active_identity directamente (no el fixture voice_mid) para que el
    MID no tenga voice_config.
    """
    mid = active_identity["mid"]
    r = anon_client.get(
        INTERNAL_PATH.format(mid=mid),
        headers={"X-Telco-Key": telco_key},
    )
    assert r.status_code == 404
    assert r.json()["error"] == "voice_config_not_found"


def test_internal_endpoint_404_unknown_mid(anon_client, telco_key):
    """MID inexistente -> 404 (no filtra si el MID existe o solo le falta config:
    mismo error voice_config_not_found para no enumerar MIDs)."""
    r = anon_client.get(
        INTERNAL_PATH.format(mid="mid_doesnotexist000"),
        headers={"X-Telco-Key": telco_key},
    )
    assert r.status_code == 404
    assert r.json()["error"] == "voice_config_not_found"


# ──────────────────── el customer-auth NO aplica ────────────────────

def test_internal_endpoint_not_reachable_by_customer_key(
        client, voice_mid):
    """`client` lleva la API key del customer (Bearer) PERO no X-Telco-Key, y
    NO inyectamos `telco_key` -> el gate _telco salta primero.

    El endpoint interno NO pasa por check_auth del customer: una credencial de
    customer no debe colarse. Con TELCO_INBOUND_KEY sin configurar -> 503.
    """
    r = client.get(INTERNAL_PATH.format(mid=voice_mid["mid"]))
    assert r.status_code == 503
    assert r.json()["error"] == "telco_inbound_disabled"


def test_internal_endpoint_customer_key_without_telco_key_is_rejected(
        client, voice_mid, telco_key):
    """Con la clave telco CONFIGURADA pero el cliente presentando solo su Bearer
    de customer (sin X-Telco-Key) -> 401.

    Confirma que la autorizacion del customer NO concede acceso a este endpoint:
    el unico factor que abre la puerta es X-Telco-Key, no el Bearer del customer.
    """
    r = client.get(INTERNAL_PATH.format(mid=voice_mid["mid"]))
    assert r.status_code == 401
    assert r.json()["error"] == "invalid_telco_key"


def test_internal_endpoint_customer_key_does_not_substitute_telco_key(
        anon_client, client, voice_mid, telco_key):
    """La API key de customer puesta en X-Telco-Key tampoco vale: son secretos
    distintos. Usamos el Bearer del customer como si fuera la X-Telco-Key.
    """
    # La API key del customer es la TEST_API_KEY del conftest; la metemos en el
    # header equivocado a proposito.
    bearer = client.headers["Authorization"].split(" ", 1)[1]
    r = anon_client.get(
        INTERNAL_PATH.format(mid=voice_mid["mid"]),
        headers={"X-Telco-Key": bearer},
    )
    assert r.status_code == 401
    assert r.json()["error"] == "invalid_telco_key"
