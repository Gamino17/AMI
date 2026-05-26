"""Rate limits + spending por MobileIdentity.

Cada MID lleva un dict `limits` con tres tipos de tope:

  - Ventana horaria/diaria de SMS y llamadas (anti-spam, anti-runaway).
  - Presupuesto mensual en EUR (anti-bill-shock).
  - Allowlist de prefijos de país (anti-fraude por roaming exótico).

Y un dict `usage` que se actualiza on-the-fly:

  - sms_count_today, sms_count_this_hour, last_reset_at_day/hour
  - calls_count_today, calls_count_this_hour, calls_minutes_this_month
  - spend_this_month_eur, last_reset_at_month

Antes de cada SMS saliente o `place_call`, el backend llama a
`check_and_charge_sms(mid)` / `check_and_charge_call(mid)`. Si el MID está
sobre el tope, devuelven `(False, reason)` y el endpoint responde 429.

Cuando una llamada termina (transition_call → completed), el backend llama
`account_call_duration(mid, duration_sec, country_prefix)` que añade los
minutos al usage y descuenta del budget.

Tarifas (mock, parametrizables por env): 0.05 EUR/SMS, 0.04 EUR/min de
llamada. En live el partner cobra a AMI por tráfico real; estos números
representan lo que AMI factura al cliente.
"""
from __future__ import annotations
import os
from datetime import datetime, timezone


# Tarifas (sobreescribibles por env)
PRICE_SMS_EUR = float(os.environ.get("AMI_PRICE_SMS_EUR", "0.05"))
PRICE_CALL_PER_MIN_EUR = float(os.environ.get("AMI_PRICE_CALL_MIN_EUR", "0.04"))

# Límites por defecto al activar un MID. Razonables para v1, ajustables por
# el customer con POST /v1/mobile-identities/{mid}/limits.
DEFAULT_LIMITS = {
    "sms_per_hour": 60,
    "sms_per_day": 500,
    "calls_per_hour": 20,
    "calls_per_day": 200,
    "monthly_budget_eur": 100.0,
    "allowed_country_prefixes": ["+34"],   # España por defecto; ampliar al alta
    # Protección anti-spam por destinatario: un cliente con bug no puede
    # saturar el mismo número y hacer que el SBC del partner nos capen el
    # trunk. Si excede esto, devolvemos sms_destination_limit_exceeded.
    "sms_per_destination_per_hour": 5,
    "sms_per_destination_per_day": 20,
    "calls_per_destination_per_hour": 3,
    "calls_per_destination_per_day": 10,
}


def new_limits() -> dict:
    """Devuelve un set de límites por defecto (copia mutable)."""
    return {**DEFAULT_LIMITS, "allowed_country_prefixes": list(DEFAULT_LIMITS["allowed_country_prefixes"])}


def new_usage() -> dict:
    n = _now()
    return {
        "sms_count_this_hour": 0,
        "sms_count_today": 0,
        "calls_count_this_hour": 0,
        "calls_count_today": 0,
        "calls_minutes_this_month": 0.0,
        "spend_this_month_eur": 0.0,
        # Por destinatario: {<msisdn>: {"sms_h": N, "sms_d": N, "calls_h": N, "calls_d": N}}.
        # Se limpian junto con los counters globales en _maybe_reset.
        "per_destination": {},
        "last_reset_at_hour": n.isoformat(),
        "last_reset_at_day": n.isoformat(),
        "last_reset_at_month": n.isoformat(),
    }


def _now():
    return datetime.now(timezone.utc)


def _maybe_reset(usage: dict) -> None:
    """Cero los contadores si pasamos a una hora/día/mes nuevo. Idempotente."""
    n = _now()
    try:
        hour_marker = datetime.fromisoformat(usage["last_reset_at_hour"])
        day_marker = datetime.fromisoformat(usage["last_reset_at_day"])
        month_marker = datetime.fromisoformat(usage["last_reset_at_month"])
    except Exception:
        # Si los markers están corruptos, reset completo
        usage.update(new_usage())
        return

    if hour_marker.replace(minute=0, second=0, microsecond=0) != n.replace(minute=0, second=0, microsecond=0):
        usage["sms_count_this_hour"] = 0
        usage["calls_count_this_hour"] = 0
        # Reset hourly counters por destino (mantenemos los daily)
        for dest in usage.setdefault("per_destination", {}).values():
            dest["sms_h"] = 0
            dest["calls_h"] = 0
        usage["last_reset_at_hour"] = n.isoformat()
    if day_marker.date() != n.date():
        usage["sms_count_today"] = 0
        usage["calls_count_today"] = 0
        # Reset diario por destino — limpiamos el dict entero (era de ayer)
        usage["per_destination"] = {}
        usage["last_reset_at_day"] = n.isoformat()
    if (month_marker.year, month_marker.month) != (n.year, n.month):
        usage["calls_minutes_this_month"] = 0.0
        usage["spend_this_month_eur"] = 0.0
        usage["last_reset_at_month"] = n.isoformat()


def _country_allowed(limits: dict, e164: str) -> bool:
    allowed = limits.get("allowed_country_prefixes") or []
    if not allowed:    # lista vacía = bloquear todo (defensa por defecto)
        return False
    return any(e164.startswith(p) for p in allowed)


def _dest_bucket(usage: dict, to_e164: str) -> dict:
    """Bucket por destino dentro de usage['per_destination']."""
    pd = usage.setdefault("per_destination", {})
    return pd.setdefault(to_e164, {"sms_h": 0, "sms_d": 0, "calls_h": 0, "calls_d": 0})


def check_and_charge_sms(identity: dict, to_e164: str) -> tuple[bool, str | None]:
    """Verifica límites + descuenta SMS. Devuelve (ok, reason_if_blocked)."""
    limits = identity.setdefault("limits", new_limits())
    usage = identity.setdefault("usage", new_usage())
    _maybe_reset(usage)

    if not _country_allowed(limits, to_e164):
        return False, "country_not_allowed"
    if usage["sms_count_this_hour"] >= limits["sms_per_hour"]:
        return False, "sms_hourly_limit_exceeded"
    if usage["sms_count_today"] >= limits["sms_per_day"]:
        return False, "sms_daily_limit_exceeded"
    if usage["spend_this_month_eur"] + PRICE_SMS_EUR > limits["monthly_budget_eur"]:
        return False, "monthly_budget_exceeded"

    # Cap por destinatario (protección anti-spam → trunk del partner)
    dest = _dest_bucket(usage, to_e164)
    if dest["sms_h"] >= limits.get("sms_per_destination_per_hour", 5):
        return False, "sms_destination_hourly_limit_exceeded"
    if dest["sms_d"] >= limits.get("sms_per_destination_per_day", 20):
        return False, "sms_destination_daily_limit_exceeded"

    usage["sms_count_this_hour"] += 1
    usage["sms_count_today"] += 1
    usage["spend_this_month_eur"] = round(usage["spend_this_month_eur"] + PRICE_SMS_EUR, 4)
    dest["sms_h"] += 1
    dest["sms_d"] += 1
    return True, None


def check_and_reserve_call(identity: dict, to_e164: str) -> tuple[bool, str | None]:
    """Verifica límites antes de originar una llamada. Reserva slot horario/diario.
    El gasto en EUR se calcula al final (con la duración real) vía
    `account_call_duration`."""
    limits = identity.setdefault("limits", new_limits())
    usage = identity.setdefault("usage", new_usage())
    _maybe_reset(usage)

    if not _country_allowed(limits, to_e164):
        return False, "country_not_allowed"
    if usage["calls_count_this_hour"] >= limits["calls_per_hour"]:
        return False, "calls_hourly_limit_exceeded"
    if usage["calls_count_today"] >= limits["calls_per_day"]:
        return False, "calls_daily_limit_exceeded"
    if usage["spend_this_month_eur"] >= limits["monthly_budget_eur"]:
        return False, "monthly_budget_exceeded"

    # Cap por destinatario
    dest = _dest_bucket(usage, to_e164)
    if dest["calls_h"] >= limits.get("calls_per_destination_per_hour", 3):
        return False, "calls_destination_hourly_limit_exceeded"
    if dest["calls_d"] >= limits.get("calls_per_destination_per_day", 10):
        return False, "calls_destination_daily_limit_exceeded"

    usage["calls_count_this_hour"] += 1
    usage["calls_count_today"] += 1
    dest["calls_h"] += 1
    dest["calls_d"] += 1
    return True, None


def account_call_duration(identity: dict, duration_sec: int | float | None) -> None:
    """Suma minutos y EUR al usage. Llamado al transicionar a `completed`."""
    if not duration_sec or duration_sec <= 0:
        return
    minutes = float(duration_sec) / 60.0
    cost = round(minutes * PRICE_CALL_PER_MIN_EUR, 4)
    usage = identity.setdefault("usage", new_usage())
    _maybe_reset(usage)
    usage["calls_minutes_this_month"] = round(usage["calls_minutes_this_month"] + minutes, 4)
    usage["spend_this_month_eur"] = round(usage["spend_this_month_eur"] + cost, 4)


def update_limits(identity: dict, patch: dict) -> tuple[bool, str | None, dict]:
    """Aplica un patch parcial sobre los limits del MID. Validación estricta."""
    cur = identity.setdefault("limits", new_limits())
    new = dict(cur)
    for k in ("sms_per_hour", "sms_per_day", "calls_per_hour", "calls_per_day"):
        if k in patch:
            v = patch[k]
            if not isinstance(v, int) or v < 0:
                return False, f"invalid_{k}", cur
            new[k] = v
    if "monthly_budget_eur" in patch:
        v = patch["monthly_budget_eur"]
        if not isinstance(v, (int, float)) or v < 0:
            return False, "invalid_monthly_budget_eur", cur
        new["monthly_budget_eur"] = float(v)
    if "allowed_country_prefixes" in patch:
        v = patch["allowed_country_prefixes"]
        if not isinstance(v, list) or not all(isinstance(p, str) and p.startswith("+") for p in v):
            return False, "invalid_allowed_country_prefixes", cur
        new["allowed_country_prefixes"] = v
    identity["limits"] = new
    return True, None, new


def usage_snapshot(identity: dict) -> dict:
    """Devuelve el usage actual + tarifas + porcentajes consumidos. Útil para
    paneles y para que el agente sepa cuánto le queda antes de chocar."""
    limits = identity.setdefault("limits", new_limits())
    usage = identity.setdefault("usage", new_usage())
    _maybe_reset(usage)

    def pct(num, den):
        if not den: return None
        return round(min(100.0, 100.0 * num / den), 2)

    return {
        "usage": dict(usage),
        "limits": dict(limits),
        "pricing": {
            "sms_eur": PRICE_SMS_EUR,
            "call_per_min_eur": PRICE_CALL_PER_MIN_EUR,
        },
        "consumed_pct": {
            "sms_hour": pct(usage["sms_count_this_hour"], limits["sms_per_hour"]),
            "sms_day": pct(usage["sms_count_today"], limits["sms_per_day"]),
            "calls_hour": pct(usage["calls_count_this_hour"], limits["calls_per_hour"]),
            "calls_day": pct(usage["calls_count_today"], limits["calls_per_day"]),
            "monthly_budget": pct(usage["spend_this_month_eur"], limits["monthly_budget_eur"]),
        },
    }
