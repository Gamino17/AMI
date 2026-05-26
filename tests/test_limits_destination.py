"""Tests del rate limit por destino MSISDN.

Protege al trunk del partner: un cliente con bug no puede saturar el mismo
número de destino. Por defecto: 5 SMS/h y 20 SMS/día al mismo destino;
3 llamadas/h y 10/día.
"""
from __future__ import annotations
import ami_limits


def _identity(**limits_override):
    return {
        "limits": {**ami_limits.DEFAULT_LIMITS, **limits_override},
        "usage": ami_limits.new_usage(),
    }


def test_sms_dest_hourly_cap_blocks_sixth():
    ident = _identity(sms_per_destination_per_hour=5, sms_per_destination_per_day=999)
    for i in range(5):
        ok, reason = ami_limits.check_and_charge_sms(ident, "+34600111222")
        assert ok, f"#{i} debería pasar: {reason}"
    ok, reason = ami_limits.check_and_charge_sms(ident, "+34600111222")
    assert not ok
    assert reason == "sms_destination_hourly_limit_exceeded"


def test_sms_dest_cap_independent_per_msisdn():
    """Saturar +34600... no afecta a +34700..."""
    ident = _identity(sms_per_destination_per_hour=2)
    ami_limits.check_and_charge_sms(ident, "+34600111222")
    ami_limits.check_and_charge_sms(ident, "+34600111222")
    blocked, _ = ami_limits.check_and_charge_sms(ident, "+34600111222")
    assert not blocked
    # Otro destino debe seguir pasando
    ok, _ = ami_limits.check_and_charge_sms(ident, "+34700111222")
    assert ok


def test_sms_dest_daily_cap_persists_across_hour_reset():
    """Reset horario NO debe reabrir el cap diario por destino."""
    ident = _identity(sms_per_destination_per_hour=99, sms_per_destination_per_day=3)
    for i in range(3):
        ok, _ = ami_limits.check_and_charge_sms(ident, "+34600111222")
        assert ok
    # Forzar reset horario (cambiar el marker a una hora vieja)
    from datetime import datetime, timezone, timedelta
    ident["usage"]["last_reset_at_hour"] = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    ami_limits._maybe_reset(ident["usage"])
    # El bucket horario se ha reseteado pero el diario sigue contando
    blocked, reason = ami_limits.check_and_charge_sms(ident, "+34600111222")
    assert not blocked
    assert reason == "sms_destination_daily_limit_exceeded"


def test_calls_dest_cap_blocks():
    ident = _identity(calls_per_destination_per_hour=3)
    for _ in range(3):
        ok, _ = ami_limits.check_and_reserve_call(ident, "+34600999000")
        assert ok
    blocked, reason = ami_limits.check_and_reserve_call(ident, "+34600999000")
    assert not blocked
    assert reason == "calls_destination_hourly_limit_exceeded"


def test_per_destination_bucket_resets_on_day_change():
    ident = _identity(sms_per_destination_per_day=2)
    ami_limits.check_and_charge_sms(ident, "+34600111222")
    ami_limits.check_and_charge_sms(ident, "+34600111222")
    # Reset diario
    from datetime import datetime, timezone, timedelta
    ident["usage"]["last_reset_at_day"] = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    ami_limits._maybe_reset(ident["usage"])
    # Debe poder mandar de nuevo
    ok, _ = ami_limits.check_and_charge_sms(ident, "+34600111222")
    assert ok
