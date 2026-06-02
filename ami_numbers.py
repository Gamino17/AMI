"""Inventario de números E.164 del partner telco — lógica pura del pool.

Este módulo es el equivalente a `ami_limits.py` para el inventario de números:
funciones puras que reciben y mutan `state` (el STATE de ami_api) o un record
dict, y devuelven datos. NO toca HTTP, NO importa `ami_api` (para evitar el
ciclo), y reimplementa la validación E.164 con la MISMA regex que el repo
(`^\\+[1-9]\\d{5,14}$` + limpieza `re.sub(r"[\\s\\-]", "", raw)`), igual que
`ami_limits` define su propio `_now()`.

POLÍTICA pool-first / mock-fallback
-----------------------------------
El alta (POST /v1/mobile-identities/activate) intenta asignar AUTOMÁTICAMENTE
un número real del inventario con `allocate_number(...)`. Si hay un número
'available' que matchee el país (y capabilities) pedido, ese número real se
asigna al MID. Si NO hay match (pool vacío, sin números del país, o agotado),
`allocate_number` devuelve None y el caller hace FALLBACK al número mock
histórico (`'+34 600 ' + random`). Así, con el pool vacío (el estado de dev y
de los tests existentes) el alta SIEMPRE devuelve un phone_number como hasta
ahora — los tests no se rompen. Cuando el partner entregue números reales y se
carguen en el pool, el alta empieza a repartirlos sin tocar el flujo.

SCHEMA del record  (state['numbers'][<e164_normalizado>])
---------------------------------------------------------
    {
        "number":       "+573336033869",          # E.164 normalizado (== la clave)
        "country":      "CO",                       # código ISO de COUNTRIES (validado por el caller)
        "capabilities": ["sms", "voice", "data"],   # capacidades que soporta el número
        "status":       "available" | "assigned" | "disabled",
        "mid":          "<mid asignado>" | None,    # cross-link al MobileIdentity
        "source":       "partner_co" | "admin_manual" | "admin",
        "created_at":   "<iso>",                    # cuándo entró al inventario
        "assigned_at":  "<iso>" | None,             # cuándo se asignó (None si disponible)
    }

La clave del dict es el número NORMALIZADO E.164: garantiza dedup natural y
lookup O(1) por número, igual que `_find_mid_by_phone` normaliza para comparar.

ATOMICIDAD
----------
El servidor serializa cada request bajo el STATE_LOCK global (RLock) y persiste
en el finally del request, así que dos altas concurrentes no pueden tomar el
mismo número: las mutaciones de `allocate_number` ocurren en sitio antes de
retornar. Como defensa adicional contra reintentos del MISMO request,
`allocate_number` es idempotente por-mid: si el `mid` ya tiene un número
asignado, devuelve ESE en vez de asignar otro (evita fugas del pool).
"""
from __future__ import annotations
import re
from datetime import datetime, timezone


# Misma regex y limpieza que ami_api._normalize_msisdn (deliberadamente
# duplicada aquí para no acoplar el módulo puro al import de ami_api).
_E164_RE = re.compile(r"^\+[1-9]\d{5,14}$")


def _now():
    """Timestamp por defecto (ISO 8601, UTC). Inyectable vía `now_fn`."""
    return datetime.now(timezone.utc).isoformat()


def _normalize(raw):
    """Acepta '+34 600 549 832', '+34-600-549-832', etc.; devuelve
    '+34600549832'. Devuelve None si el formato es inválido tras limpieza.
    Misma semántica que ami_api._normalize_msisdn."""
    if not isinstance(raw, str):
        return None
    s = re.sub(r"[\s\-]", "", raw.strip())
    if not _E164_RE.match(s):
        return None
    return s


def _pool(state):
    """Devuelve state['numbers'] creándolo vacío si no existiera (defensa).
    En producción ya se inicializa en el literal de STATE."""
    pool = state.get("numbers")
    if pool is None:
        pool = {}
        state["numbers"] = pool
    return pool


def _summary(rec):
    """Vista compacta de un record (no expone de más; no hay secretos)."""
    return {
        "number": rec["number"],
        "country": rec.get("country"),
        "capabilities": rec.get("capabilities", []),
        "status": rec.get("status"),
        "mid": rec.get("mid"),
        "source": rec.get("source"),
        "created_at": rec.get("created_at"),
        "assigned_at": rec.get("assigned_at"),
    }


def add_numbers(state, numbers, country, capabilities=None, source=None,
                now_fn=_now, prefix=None):
    """Carga una lista de números E.164 al inventario.

    `numbers` es una lista de strings (el endpoint admin envuelve el singular
    `number` en una lista). Para cada raw:
      - normaliza con `_normalize`; si es inválido -> `invalid`.
      - si la clave ya existe en el pool -> `skipped_dupes` (NO pisa, ni
        siquiera si está asignado: dedup duro, idempotente).
      - si no existe -> crea entrada status='available', mid=None,
        assigned_at=None, created_at=now_fn().

    `country` lo valida el caller contra COUNTRIES; aquí se guarda tal cual.
    `capabilities` default [], `source` default 'admin'.

    Devuelve {'added': [...], 'skipped_dupes': [...], 'invalid': [...]} con
    listas de strings (números, no records) para una respuesta compacta.
    """
    pool = _pool(state)
    caps = list(capabilities) if capabilities else []
    src = source or "admin"
    added, skipped, invalid = [], [], []
    for raw in (numbers or []):
        e164 = _normalize(raw)
        if e164 is None:
            invalid.append(raw)
            continue
        # Validación de coherencia número<->país: si el caller pasa el prefijo
        # E.164 del país (COUNTRIES[country]['msisdn_prefix']), rechazamos los
        # números que no empiecen por él (p.ej. un +57 cargado como country=ES),
        # que luego romperían el matcheo por país en allocate_number.
        if prefix and not e164.startswith(prefix):
            invalid.append(raw)
            continue
        if e164 in pool:
            skipped.append(e164)
            continue
        pool[e164] = {
            "number": e164,
            "country": country,
            "capabilities": list(caps),
            "status": "available",
            "mid": None,
            "source": src,
            "created_at": now_fn(),
            "assigned_at": None,
        }
        added.append(e164)
    return {"added": added, "skipped_dupes": skipped, "invalid": invalid}


def allocate_number(state, mid, country=None, capabilities=None, now_fn=_now):
    """Asigna el PRIMER número 'available' que matchee al `mid`.

    Matcheo:
      - country: si se pasa, record['country'] == country (case-sensitive,
        viene de COUNTRIES). Si None, cualquier país.
      - capabilities: si se pide una lista no vacía, el número debe soportar
        TODAS (set(requested) <= set(record['capabilities'])). Si None/[],
        no filtra.

    Idempotencia por-mid: si el `mid` YA tiene un número asignado, devuelve
    ESE record sin asignar otro (cubre el reintento del mismo request y evita
    fugas del pool).

    Muta el record en sitio (status='assigned', mid, assigned_at=now_fn()) y
    devuelve la MISMA referencia viva dentro de state. Devuelve None si no hay
    'available' que matchee -> el caller hace fallback a mock.
    """
    pool = _pool(state)

    # Idempotencia defensiva: ¿este mid ya tiene número del pool?
    for rec in pool.values():
        if rec.get("mid") == mid and rec.get("status") == "assigned":
            return rec

    requested_caps = set(capabilities) if capabilities else set()
    for rec in pool.values():
        if rec.get("status") != "available":
            continue
        if country is not None and rec.get("country") != country:
            continue
        if requested_caps and not requested_caps <= set(rec.get("capabilities", [])):
            continue
        rec["status"] = "assigned"
        rec["mid"] = mid
        rec["assigned_at"] = now_fn()
        return rec
    return None


def release_number(state, mid, now_fn=_now):
    """Devuelve al pool el número asignado al `mid` (baja/suspensión del MID).

    Busca el record con status=='assigned' y mid==mid; si existe lo vuelve a
    'available' (mid=None, assigned_at=None) y lo devuelve. None si el mid no
    tenía número del pool (p.ej. estaba en mock) -> no-op seguro.
    """
    pool = _pool(state)
    for rec in pool.values():
        if rec.get("mid") == mid and rec.get("status") == "assigned":
            rec["status"] = "available"
            rec["mid"] = None
            rec["assigned_at"] = None
            return rec
    return None


def list_numbers(state, status=None, country=None):
    """Lista el inventario (filtrable por status y/o country) + counts.

    `counts` se calcula SIEMPRE sobre el inventario completo (no sobre el
    filtrado) para dar visibilidad operativa. `numbers` es la vista filtrada.
    """
    pool = _pool(state)
    counts = {"available": 0, "assigned": 0, "disabled": 0, "total": 0}
    out = []
    for rec in pool.values():
        st = rec.get("status")
        counts["total"] += 1
        if st in counts:
            counts[st] += 1
        if status is not None and st != status:
            continue
        if country is not None and rec.get("country") != country:
            continue
        out.append(_summary(rec))
    return {"numbers": out, "count": len(out), "counts": counts}


def disable_number(state, number_raw):
    """Marca un número como 'disabled' (no se vuelve a asignar).

    Normaliza `number_raw`, lo busca en el pool y pone status='disabled'
    independientemente de si estaba available o assigned (un número disabled
    queda fuera de `allocate_number`). Devuelve el record o None si no existe.
    """
    pool = _pool(state)
    e164 = _normalize(number_raw)
    if e164 is None:
        return None
    rec = pool.get(e164)
    if rec is None:
        return None
    rec["status"] = "disabled"
    return rec


def enable_number(state, number_raw):
    """Simétrico de disable: re-habilita un número a 'available'.

    Solo vuelve a 'available' si no está asignado a ningún MID (mid is None);
    si está asignado, no toca el status (no robaría el número a su MID).
    Devuelve el record o None si no existe. Opcional (no requerido por el
    contrato), incluido para el endpoint /enable de follow-up.
    """
    pool = _pool(state)
    e164 = _normalize(number_raw)
    if e164 is None:
        return None
    rec = pool.get(e164)
    if rec is None:
        return None
    if rec.get("mid") is None:
        rec["status"] = "available"
    return rec


def upsert_assigned(state, number_raw, mid, country=None, capabilities=None,
                    source=None, now_fn=_now):
    """Registra/actualiza un número como 'assigned' a `mid` (idempotente).

    Pensado para el camino del endpoint admin de número EXPLÍCITO (el del PoC
    +573336033869): el número se inyecta a mano en el MID, y este helper lo
    refleja en el inventario para mantener consistencia (number.mid == mid).

    - Si el número no existe en el pool: lo crea status='assigned', mid=mid.
    - Si existe y NO choca (mid coincide o el record no tenía mid): lo actualiza
      a 'assigned', mid=mid (idempotente; no rompe el reuse del mismo MID).
    - Si existe y está asignado a OTRO mid distinto: NO lo pisa (devuelve el
      record existente tal cual, conflicto silencioso para no romper el alta).

    Devuelve el record resultante, o None si `number_raw` es inválido.
    """
    pool = _pool(state)
    e164 = _normalize(number_raw)
    if e164 is None:
        return None
    rec = pool.get(e164)
    if rec is None:
        rec = {
            "number": e164,
            "country": country,
            "capabilities": list(capabilities) if capabilities else [],
            "status": "assigned",
            "mid": mid,
            "source": source or "admin_manual",
            "created_at": now_fn(),
            "assigned_at": now_fn(),
        }
        pool[e164] = rec
        return rec
    # Existe: solo actualizamos si no choca con otro mid vivo.
    if rec.get("mid") in (None, mid):
        rec["status"] = "assigned"
        rec["mid"] = mid
        if rec.get("assigned_at") is None:
            rec["assigned_at"] = now_fn()
    return rec
