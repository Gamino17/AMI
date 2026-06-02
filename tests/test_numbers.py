"""Tests del inventario de números (pool) de AMI.

AMI debe poder asignar AUTOMÁTICAMENTE un número real del partner en el alta
(activate) cuando exista uno disponible que matchee el país y las capabilities
de la oferta, con fallback NO-breaking al número mock ('+34 600 ' + random)
cuando el pool está vacío o sin match. Esto cubre:

  Capa HTTP (end-to-end, fixtures client/anon_client/server_url):
    - POST /v1/admin/numbers  (alta de números al pool; auth admin)
    - GET  /v1/admin/numbers?status=&country=  (listado + counts; auth admin)
    - POST /v1/admin/numbers/{number}/disable   (auth admin)
    - alta (activate) asigna número real del pool + matchea country
    - pool vacío -> fallback a mock (NO rompe los tests existentes)
    - no doble-asignación + agotamiento del pool
    - número disabled NO se asigna
    - auth: customer key NO accede a /v1/admin/numbers; sin ADMIN_KEY -> 401
    - country no soportado -> 400

  Capa pura (módulo ami_numbers, sin HTTP):
    - add_numbers: dedup, inválidos, normalización, idempotencia
    - allocate_number: match country + capabilities, idempotencia por-mid, None
    - release_number: devuelve el número al pool, no-op si el mid no tenía pool
    - list_numbers: counts y filtros
    - disable_number: bloquea allocate

REGLA NO-BREAKING (dura): con el pool vacío el alta SIEMPRE devuelve un
phone_number (fallback mock). Los ~585 tests existentes siguen verdes porque
nunca cargan el pool. Los tests de este módulo cargan el pool explícitamente.

Patrón de admin-client tomado de tests/test_multitenancy.py (Bearer ADMIN_KEY
con monkeypatch de ami_api_module.ADMIN_KEY). _provision tomado de
tests/test_calls.py (flujo SIMRequest->oferta->datos->contrato->firma->activate).
"""
from __future__ import annotations

import httpx
import pytest


ADMIN_KEY = "admin_secret_xyz"

# Capabilities que cubren la oferta por defecto. El alta usa offer['capabilities']
# que, para una sim-request sin capabilities explícitas, es ['sms', 'voice']
# (fallback en ami_api.py:7862). Cargamos los números del pool con AL MENOS
# esas caps para que allocate_number los matchee en el alta. Usamos el set
# completo del país (COUNTRIES[...]['capabilities']) para ser superset seguro.
FULL_CAPS = ["sms", "voice", "data", "whatsapp_ready"]


# ---------------- helpers ----------------

@pytest.fixture
def admin_key(ami_api_module, monkeypatch):
    """Activa el ADMIN_KEY para los endpoints /v1/admin/numbers."""
    monkeypatch.setattr(ami_api_module, "ADMIN_KEY", ADMIN_KEY)
    return ADMIN_KEY


@pytest.fixture
def clean_pool(ami_api_module):
    """Defensa de aislamiento: garantiza el pool vacío al entrar al test,
    aunque el conftest aún no limpie STATE['numbers']. Idempotente."""
    ami_api_module.STATE.setdefault("numbers", {}).clear()
    yield
    ami_api_module.STATE.setdefault("numbers", {}).clear()


def _admin_client(server_url):
    """httpx.Client autenticado como admin (Bearer ADMIN_KEY). Igual patrón
    que test_multitenancy.py:12-13."""
    return httpx.Client(
        base_url=server_url,
        timeout=5.0,
        headers={"Authorization": f"Bearer {ADMIN_KEY}"},
    )


def _add_numbers(server_url, numbers, country, capabilities=None, source=None):
    """POST /v1/admin/numbers como admin. Devuelve la respuesta httpx."""
    body = {"numbers": numbers, "country": country}
    if capabilities is not None:
        body["capabilities"] = capabilities
    if source is not None:
        body["source"] = source
    with _admin_client(server_url) as c:
        return c.post("/v1/admin/numbers", json=body)


def _list_numbers(server_url, status=None, country=None):
    """GET /v1/admin/numbers como admin. Devuelve el JSON parseado."""
    params = {}
    if status is not None:
        params["status"] = status
    if country is not None:
        params["country"] = country
    with _admin_client(server_url) as c:
        r = c.get("/v1/admin/numbers", params=params)
    assert r.status_code == 200, r.text
    return r.json()


def _provision(client, sample_customer_payload, country="ES", activate_extra=None):
    """Flujo completo SIMRequest->oferta->datos->contrato->firma->activate.
    `country` se propaga a la sim-request (la oferta lo copia a offer['country'],
    que es lo que lee allocate_number en el alta). Devuelve la respuesta httpx
    de activate."""
    r = client.post("/v1/sim-requests", json={"country": country, "sim_type": "eSIM"})
    assert r.status_code == 201, r.text
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


# =====================================================================
# Capa HTTP · admin add / list
# =====================================================================

def test_add_numbers_201_and_counts(server_url, admin_key, clean_pool):
    r = _add_numbers(server_url,
                     ["+34600111222", "+34600111333"],
                     country="ES", capabilities=FULL_CAPS, source="partner_es")
    assert r.status_code == 201, r.text
    body = r.json()
    assert len(body["added"]) == 2
    assert body["skipped"] == []
    assert body["invalid"] == []
    assert body["count"] == 2

    listing = _list_numbers(server_url)
    counts = listing["counts"]
    assert counts["available"] == 2
    assert counts["assigned"] == 0
    assert counts["disabled"] == 0
    assert counts["total"] == 2
    assert listing["count"] == 2


def test_add_numbers_singular_number_key(server_url, admin_key, clean_pool):
    """El endpoint también acepta {number:'...'} singular (no solo numbers:[...])."""
    with _admin_client(server_url) as c:
        r = c.post("/v1/admin/numbers",
                   json={"number": "+34600111999", "country": "ES",
                         "capabilities": FULL_CAPS})
    assert r.status_code == 201, r.text
    assert len(r.json()["added"]) == 1
    assert _list_numbers(server_url)["counts"]["available"] == 1


def test_add_numbers_dedup_and_invalid(server_url, admin_key, clean_pool):
    """Un número repetido en el mismo batch -> skipped; un no-E.164 -> invalid."""
    r = _add_numbers(server_url,
                     ["+34600111222", "+34600111222", "no-e164"],
                     country="ES", capabilities=FULL_CAPS)
    assert r.status_code == 201, r.text
    body = r.json()
    assert len(body["added"]) == 1
    assert len(body["skipped"]) == 1
    assert len(body["invalid"]) == 1

    # Recargar los mismos números: idempotente -> nada nuevo, ya existente skipped.
    r2 = _add_numbers(server_url, ["+34600111222"], country="ES",
                      capabilities=FULL_CAPS)
    assert r2.status_code == 201, r2.text
    body2 = r2.json()
    assert body2["added"] == []
    assert len(body2["skipped"]) == 1

    # El total del pool sigue siendo 1 (no se duplicó).
    assert _list_numbers(server_url)["counts"]["total"] == 1


def test_list_numbers_filters_by_status_and_country(server_url, admin_key, clean_pool):
    _add_numbers(server_url, ["+34600000001"], country="ES", capabilities=FULL_CAPS)
    _add_numbers(server_url, ["+573330000001"], country="CO", capabilities=FULL_CAPS)

    es = _list_numbers(server_url, country="ES")
    assert es["count"] == 1
    assert all(n["country"] == "ES" for n in es["numbers"])

    avail = _list_numbers(server_url, status="available")
    assert avail["count"] == 2
    # counts SIEMPRE refleja el inventario completo, no el filtrado.
    assert avail["counts"]["total"] == 2


# =====================================================================
# Capa HTTP · alta (activate) asigna del pool + matchea country
# =====================================================================

def test_activate_assigns_real_number_from_pool_matching_country(
        server_url, client, sample_customer_payload, admin_key, clean_pool):
    """Con un número CO en el pool y un alta country='CO', el MID recibe ese
    número real (no el mock '+34 600 ...') y queda el cross-link en el pool."""
    pool_num = "+573336033869"
    r = _add_numbers(server_url, [pool_num], country="CO",
                     capabilities=FULL_CAPS, source="partner_co")
    assert r.status_code == 201, r.text

    r = _provision(client, sample_customer_payload, country="CO")
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["phone_number"] == pool_num
    assert not body["phone_number"].startswith("+34 600 ")
    mid = body["id"]

    # El número del pool quedó assigned y cruzado con el mid (cross-link).
    listing = _list_numbers(server_url, country="CO")
    rec = next(n for n in listing["numbers"] if n["number"] == pool_num)
    assert rec["status"] == "assigned"
    assert rec["mid"] == mid
    assert rec["assigned_at"] is not None
    assert listing["counts"]["assigned"] == 1
    assert listing["counts"]["available"] == 0


def test_activate_country_match_picks_right_pool(
        server_url, client, sample_customer_payload, admin_key, clean_pool):
    """Con pool ES y CO, un alta ES debe tomar un +34 del pool ES (no un +57)."""
    _add_numbers(server_url, ["+34600222001"], country="ES", capabilities=FULL_CAPS)
    _add_numbers(server_url, ["+573330000002"], country="CO", capabilities=FULL_CAPS)

    r = _provision(client, sample_customer_payload, country="ES")
    assert r.status_code == 201, r.text
    assert r.json()["phone_number"] == "+34600222001"

    # El número ES quedó assigned; el CO sigue available.
    es_assigned = _list_numbers(server_url, country="ES", status="assigned")
    co_avail = _list_numbers(server_url, country="CO", status="available")
    assert es_assigned["count"] == 1
    assert co_avail["count"] == 1


def test_empty_pool_falls_back_to_mock_non_breaking(
        client, sample_customer_payload, clean_pool):
    """SIN pool cargado: el alta debe seguir devolviendo un phone_number mock
    ('+34 600 ...'). Este es el caso de los ~585 tests existentes — NO rompe."""
    r = _provision(client, sample_customer_payload, country="ES")
    assert r.status_code == 201, r.text
    phone = r.json()["phone_number"]
    assert phone.startswith("+34 600 "), phone


def test_empty_pool_fallback_works_for_other_country_too(
        client, sample_customer_payload, clean_pool):
    """Pool vacío con country='CO': sigue cayendo a mock (no hay número CO),
    el alta no se rompe."""
    r = _provision(client, sample_customer_payload, country="CO")
    assert r.status_code == 201, r.text
    # El fallback mock es siempre '+34 600 ...' (no depende del país pedido).
    assert r.json()["phone_number"].startswith("+34 600 ")


# =====================================================================
# Capa HTTP · no doble-asignación + agotamiento
# =====================================================================

def test_no_double_assignment(
        server_url, client, sample_customer_payload, admin_key, clean_pool):
    """Con UN solo número en el pool, dos altas distintas: la 1ª toma el número
    real, la 2ª cae a mock (pool agotado). El número del pool tiene UN único mid."""
    pool_num = "+34600333001"
    _add_numbers(server_url, [pool_num], country="ES", capabilities=FULL_CAPS)

    r1 = _provision(client, sample_customer_payload, country="ES")
    r2 = _provision(client, sample_customer_payload, country="ES")
    assert r1.status_code == 201 and r2.status_code == 201

    phone1 = r1.json()["phone_number"]
    phone2 = r2.json()["phone_number"]
    assert phone1 == pool_num
    assert phone2 != phone1
    assert phone2.startswith("+34 600 ")  # 2ª cayó a mock

    listing = _list_numbers(server_url, country="ES")
    rec = next(n for n in listing["numbers"] if n["number"] == pool_num)
    assert rec["status"] == "assigned"
    assert rec["mid"] == r1.json()["id"]   # un solo dueño, el primero
    assert listing["counts"]["assigned"] == 1
    assert listing["counts"]["available"] == 0


def test_pool_exhaustion_then_mock(
        server_url, client, sample_customer_payload, admin_key, clean_pool):
    """Cargar 2 números ES; 3 altas -> 2 reales + 1 mock. counts: available 0,
    assigned 2."""
    _add_numbers(server_url, ["+34600444001", "+34600444002"],
                 country="ES", capabilities=FULL_CAPS)

    phones = []
    for _ in range(3):
        r = _provision(client, sample_customer_payload, country="ES")
        assert r.status_code == 201, r.text
        phones.append(r.json()["phone_number"])

    real = [p for p in phones if not p.startswith("+34 600 ")]
    mock = [p for p in phones if p.startswith("+34 600 ")]
    assert sorted(real) == ["+34600444001", "+34600444002"]
    assert len(mock) == 1

    counts = _list_numbers(server_url)["counts"]
    assert counts["available"] == 0
    assert counts["assigned"] == 2


def test_disable_number_not_allocated(
        server_url, client, sample_customer_payload, admin_key, clean_pool):
    """Un número disabled NO se asigna: el alta del mismo país cae a mock."""
    pool_num = "+34600555001"
    _add_numbers(server_url, [pool_num], country="ES", capabilities=FULL_CAPS)

    with _admin_client(server_url) as c:
        r = c.post(f"/v1/admin/numbers/{pool_num}/disable")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "disabled"

    r = _provision(client, sample_customer_payload, country="ES")
    assert r.status_code == 201, r.text
    # No se asignó el disabled -> mock.
    assert r.json()["phone_number"].startswith("+34 600 ")

    listing = _list_numbers(server_url)
    rec = next(n for n in listing["numbers"] if n["number"] == pool_num)
    assert rec["status"] == "disabled"
    assert rec["mid"] is None


def test_disable_unknown_number_is_404(server_url, admin_key, clean_pool):
    with _admin_client(server_url) as c:
        r = c.post("/v1/admin/numbers/+34600999999/disable")
    assert r.status_code == 404
    assert r.json()["error"] == "number_not_found"


def test_disable_number_url_encoded_plus(server_url, admin_key, clean_pool):
    """El '+' del E.164 en la URL puede viajar como %2B; el handler debe
    unquote el segmento antes de normalizar."""
    pool_num = "+34600556001"
    _add_numbers(server_url, [pool_num], country="ES", capabilities=FULL_CAPS)
    with _admin_client(server_url) as c:
        r = c.post("/v1/admin/numbers/%2B34600556001/disable")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "disabled"


# =====================================================================
# Capa HTTP · auth admin
# =====================================================================

def test_admin_numbers_customer_key_denied_post(client, admin_key, clean_pool):
    """La fixture `client` usa la customer key (TEST_API_KEY). NO debe poder
    crear números: check_admin_auth la rechaza con 401 unauthorized_admin."""
    r = client.post("/v1/admin/numbers",
                    json={"numbers": ["+34600777001"], "country": "ES"})
    assert r.status_code == 401
    assert r.json()["error"] == "unauthorized_admin"


def test_admin_numbers_customer_key_denied_get(client, admin_key, clean_pool):
    r = client.get("/v1/admin/numbers")
    assert r.status_code == 401
    assert r.json()["error"] == "unauthorized_admin"


def test_admin_numbers_disable_customer_key_denied(client, admin_key, clean_pool):
    r = client.post("/v1/admin/numbers/+34600777002/disable")
    assert r.status_code == 401
    assert r.json()["error"] == "unauthorized_admin"


def test_admin_numbers_no_key_is_401(server_url, ami_api_module, monkeypatch, clean_pool):
    """Sin ADMIN_KEY seteada, /v1/admin/numbers devuelve 401."""
    monkeypatch.setattr(ami_api_module, "ADMIN_KEY", None)
    with httpx.Client(base_url=server_url, timeout=5.0,
                      headers={"Authorization": f"Bearer {ADMIN_KEY}"}) as c:
        r = c.post("/v1/admin/numbers",
                   json={"numbers": ["+34600777003"], "country": "ES"})
    assert r.status_code == 401


def test_admin_numbers_wrong_key_is_401(server_url, admin_key, clean_pool):
    with httpx.Client(base_url=server_url, timeout=5.0,
                      headers={"Authorization": "Bearer totally_wrong"}) as c:
        r = c.post("/v1/admin/numbers",
                   json={"numbers": ["+34600777004"], "country": "ES"})
    assert r.status_code == 401


def test_admin_numbers_unsupported_country_400(server_url, admin_key, clean_pool):
    r = _add_numbers(server_url, ["+9999999999"], country="XX")
    assert r.status_code == 400
    assert r.json()["error"] == "unsupported_country"
    assert r.json()["country"] == "XX"


# =====================================================================
# Capa pura · módulo ami_numbers (sin HTTP)
# =====================================================================

@pytest.fixture
def numbers_mod():
    import ami_numbers
    return ami_numbers


@pytest.fixture
def fresh_state():
    """Un STATE mínimo aislado para los unit tests puros del módulo."""
    return {"numbers": {}}


# -------- add_numbers --------

def test_unit_add_numbers_normalizes_and_dedups(numbers_mod, fresh_state):
    res = numbers_mod.add_numbers(
        fresh_state,
        ["+34 600 111 222", "+34-600-111-222", "+34600111333", "garbage", ""],
        country="ES", capabilities=["sms", "voice"], source="partner_es")
    # '+34 600 111 222' y '+34-600-111-222' normalizan al MISMO E.164 -> 1 add + 1 dup.
    assert len(res["added"]) == 2
    assert len(res["skipped_dupes"]) == 1
    assert len(res["invalid"]) == 2  # 'garbage' y ''
    # La clave del dict es el E.164 normalizado.
    assert "+34600111222" in fresh_state["numbers"]
    rec = fresh_state["numbers"]["+34600111222"]
    assert rec["status"] == "available"
    assert rec["mid"] is None
    assert rec["country"] == "ES"
    assert rec["capabilities"] == ["sms", "voice"]
    assert rec["source"] == "partner_es"
    assert rec["created_at"] is not None
    assert rec["assigned_at"] is None


def test_unit_add_numbers_idempotent(numbers_mod, fresh_state):
    numbers_mod.add_numbers(fresh_state, ["+34600111222"], country="ES")
    res = numbers_mod.add_numbers(fresh_state, ["+34600111222"], country="ES")
    assert res["added"] == []
    assert len(res["skipped_dupes"]) == 1
    assert len(fresh_state["numbers"]) == 1


def test_unit_add_numbers_does_not_overwrite_assigned(numbers_mod, fresh_state):
    """Re-cargar un número ya asignado NO lo pisa (dedup duro)."""
    numbers_mod.add_numbers(fresh_state, ["+34600111222"], country="ES",
                            capabilities=["sms", "voice"])
    numbers_mod.allocate_number(fresh_state, "mid_a", country="ES")
    # Re-add del mismo número: skipped, sigue asignado al mid_a.
    res = numbers_mod.add_numbers(fresh_state, ["+34600111222"], country="ES")
    assert res["added"] == []
    rec = fresh_state["numbers"]["+34600111222"]
    assert rec["status"] == "assigned"
    assert rec["mid"] == "mid_a"


# -------- allocate_number --------

def test_unit_allocate_matches_country(numbers_mod, fresh_state):
    numbers_mod.add_numbers(fresh_state, ["+34600111222"], country="ES",
                            capabilities=["sms", "voice"])
    numbers_mod.add_numbers(fresh_state, ["+573330000001"], country="CO",
                            capabilities=["sms", "voice"])
    rec = numbers_mod.allocate_number(fresh_state, "mid_co", country="CO")
    assert rec is not None
    assert rec["country"] == "CO"
    assert rec["number"] == "+573330000001"
    assert rec["status"] == "assigned"
    assert rec["mid"] == "mid_co"
    assert rec["assigned_at"] is not None


def test_unit_allocate_country_none_takes_any(numbers_mod, fresh_state):
    numbers_mod.add_numbers(fresh_state, ["+34600111222"], country="ES",
                            capabilities=["sms"])
    rec = numbers_mod.allocate_number(fresh_state, "mid_x", country=None)
    assert rec is not None
    assert rec["number"] == "+34600111222"


def test_unit_allocate_matches_capabilities_superset(numbers_mod, fresh_state):
    """allocate exige que el número soporte TODAS las caps pedidas (subset)."""
    numbers_mod.add_numbers(fresh_state, ["+34600111222"], country="ES",
                            capabilities=["sms"])  # solo sms
    numbers_mod.add_numbers(fresh_state, ["+34600111333"], country="ES",
                            capabilities=["sms", "voice", "data"])
    # Pido sms+voice: el primero (solo sms) NO matchea, el segundo SÍ.
    rec = numbers_mod.allocate_number(fresh_state, "mid_v", country="ES",
                                      capabilities=["sms", "voice"])
    assert rec is not None
    assert rec["number"] == "+34600111333"


def test_unit_allocate_empty_caps_does_not_filter(numbers_mod, fresh_state):
    """Caps pedidas vacías/None -> no filtra por capabilities."""
    numbers_mod.add_numbers(fresh_state, ["+34600111222"], country="ES",
                            capabilities=[])
    rec = numbers_mod.allocate_number(fresh_state, "mid_e", country="ES",
                                      capabilities=[])
    assert rec is not None
    rec2 = numbers_mod.allocate_number(fresh_state, "mid_e2", country="ES",
                                       capabilities=None)
    # Solo había uno -> el segundo no encuentra.
    assert rec2 is None


def test_unit_allocate_none_when_no_match(numbers_mod, fresh_state):
    # Pool vacío.
    assert numbers_mod.allocate_number(fresh_state, "mid_z", country="ES") is None
    # Solo ES; pido CO -> None.
    numbers_mod.add_numbers(fresh_state, ["+34600111222"], country="ES",
                            capabilities=["sms", "voice"])
    assert numbers_mod.allocate_number(fresh_state, "mid_z", country="CO") is None


def test_unit_allocate_idempotent_per_mid(numbers_mod, fresh_state):
    """Reintentar allocate con el MISMO mid devuelve el MISMO número (no fuga)."""
    numbers_mod.add_numbers(fresh_state, ["+34600111222", "+34600111333"],
                            country="ES", capabilities=["sms", "voice"])
    first = numbers_mod.allocate_number(fresh_state, "mid_same", country="ES")
    again = numbers_mod.allocate_number(fresh_state, "mid_same", country="ES")
    assert first["number"] == again["number"]
    # Solo uno de los dos números quedó asignado.
    assigned = [n for n in fresh_state["numbers"].values() if n["status"] == "assigned"]
    assert len(assigned) == 1


def test_unit_allocate_first_available_in_order(numbers_mod, fresh_state):
    """allocate toma el PRIMER available en orden de inserción."""
    numbers_mod.add_numbers(fresh_state, ["+34600111111", "+34600222222"],
                            country="ES", capabilities=["sms", "voice"])
    rec = numbers_mod.allocate_number(fresh_state, "mid_first", country="ES")
    assert rec["number"] == "+34600111111"


# -------- release_number --------

def test_unit_release_returns_number_to_pool(numbers_mod, fresh_state):
    numbers_mod.add_numbers(fresh_state, ["+34600111222"], country="ES",
                            capabilities=["sms", "voice"])
    numbers_mod.allocate_number(fresh_state, "mid_r", country="ES")
    released = numbers_mod.release_number(fresh_state, "mid_r")
    assert released is not None
    assert released["number"] == "+34600111222"
    assert released["status"] == "available"
    assert released["mid"] is None
    assert released["assigned_at"] is None
    # Y se puede re-asignar.
    rec = numbers_mod.allocate_number(fresh_state, "mid_r2", country="ES")
    assert rec is not None
    assert rec["mid"] == "mid_r2"


def test_unit_release_noop_for_mid_without_pool_number(numbers_mod, fresh_state):
    """Un mid que nunca tomó número del pool (estaba en mock) -> release no-op."""
    numbers_mod.add_numbers(fresh_state, ["+34600111222"], country="ES")
    assert numbers_mod.release_number(fresh_state, "mid_never") is None
    # El número del pool sigue available, intacto.
    assert fresh_state["numbers"]["+34600111222"]["status"] == "available"


# -------- list_numbers --------

def test_unit_list_numbers_counts_and_filters(numbers_mod, fresh_state):
    numbers_mod.add_numbers(fresh_state, ["+34600111111"], country="ES",
                            capabilities=["sms", "voice"])
    numbers_mod.add_numbers(fresh_state, ["+573330000001"], country="CO",
                            capabilities=["sms", "voice"])
    numbers_mod.allocate_number(fresh_state, "mid_a", country="ES")

    full = numbers_mod.list_numbers(fresh_state)
    assert full["counts"]["total"] == 2
    assert full["counts"]["assigned"] == 1
    assert full["counts"]["available"] == 1
    assert full["counts"]["disabled"] == 0
    assert full["count"] == 2

    # Filtro por country: solo CO (1, available).
    co = numbers_mod.list_numbers(fresh_state, country="CO")
    assert co["count"] == 1
    assert co["numbers"][0]["country"] == "CO"
    # counts SIEMPRE sobre el inventario completo, no el filtrado.
    assert co["counts"]["total"] == 2

    # Filtro por status assigned.
    assigned = numbers_mod.list_numbers(fresh_state, status="assigned")
    assert assigned["count"] == 1
    assert assigned["numbers"][0]["status"] == "assigned"
    assert assigned["numbers"][0]["mid"] == "mid_a"


def test_unit_list_numbers_summary_shape(numbers_mod, fresh_state):
    """El summary por número expone exactamente los campos del schema."""
    numbers_mod.add_numbers(fresh_state, ["+34600111111"], country="ES",
                            capabilities=["sms", "voice"], source="partner_es")
    n = numbers_mod.list_numbers(fresh_state)["numbers"][0]
    assert set(n.keys()) == {
        "number", "country", "capabilities", "status",
        "mid", "source", "created_at", "assigned_at",
    }


# -------- disable_number --------

def test_unit_disable_blocks_allocate(numbers_mod, fresh_state):
    numbers_mod.add_numbers(fresh_state, ["+34600111222"], country="ES",
                            capabilities=["sms", "voice"])
    rec = numbers_mod.disable_number(fresh_state, "+34 600 111 222")  # normaliza
    assert rec is not None
    assert rec["status"] == "disabled"
    # disabled no se asigna.
    assert numbers_mod.allocate_number(fresh_state, "mid_d", country="ES") is None


def test_unit_disable_unknown_returns_none(numbers_mod, fresh_state):
    assert numbers_mod.disable_number(fresh_state, "+34600000000") is None
