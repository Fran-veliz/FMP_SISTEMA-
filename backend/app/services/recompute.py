"""Aplica ctot_calc.py sobre un Flight: recalcula ETA AIRCON CALCULADO y DLA
cada vez que se crea o edita un vuelo, tal como el Excel recalculaba las
hojas 'Cálculo CTOT n' automáticamente al tipear en 'FormatoFMP'."""
from __future__ import annotations

from app import clock
from app.services.ctot_calc import compute_dla, compute_eta_aircon_calculado
from app.models import Flight


def _fmt(t) -> str | None:
    if t is None:
        return None
    return f"{t.hour:02d}{t.minute:02d}"


def recompute_flight(flight: Flight) -> None:
    now = clock.now_utc()
    today = now.date()
    flight.updated_at = now
    eta_calc = compute_eta_aircon_calculado(
        flight.eta_aircon,
        flight.etd1,
        flight.ctot1,
        flight.ctot2,
        flight.ctot3,
        flight.ctot4,
        flight.ctot5,
        today=today,
    )
    flight.eta_aircon_calculado = _fmt(eta_calc) or flight.eta_aircon

    etd, ctot = _active_level(flight)
    if etd and ctot:
        _, dla_minutos = compute_dla(etd, ctot)
        flight.dla_minutos = dla_minutos
    else:
        flight.dla_minutos = None


def _active_level(flight: Flight) -> tuple[str | None, str | None]:
    """El nivel de revisión más profundo con ETD y CTOT confirmados, igual
    a como el Excel elegía qué par ETD/CTOT usar para calcular DLA."""
    for etd, ctot in (
        (flight.etd5, flight.ctot5),
        (flight.etd4, flight.ctot4),
        (flight.etd3, flight.ctot3),
        (flight.etd2, flight.ctot2),
        (flight.etd1, flight.ctot1),
    ):
        if etd and ctot:
            return etd, ctot
    return None, None
