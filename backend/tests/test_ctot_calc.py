"""Tests contra valores reales cacheados de JUNIO 3 CONSOLIDADO(1).xlsx.

Todos los valores esperados vienen de leer el workbook con
openpyxl(data_only=True) -- es decir, son los resultados que Excel ya
tenía calculados, no una reinterpretación mía de la fórmula.
"""
from datetime import date, time

from app.services.ctot_calc import (
    compute_dla,
    compute_eta_aircon_calculado,
    compute_revision,
    nearest_delta_minutes,
    parse_hhmm,
    resolve_day_offset,
)

TODAY = date(2026, 6, 4)  # fecha usada en las fórmulas TODAY() cacheadas


def test_parse_hhmm_basic():
    assert parse_hhmm("2215") == time(22, 15)
    assert parse_hhmm("0013") == time(0, 13)
    assert parse_hhmm("") is None
    assert parse_hhmm(None) is None
    assert parse_hhmm(0) is None


def test_revision_adelanto_calculo_ctot2_row2():
    # Cálculo CTOT 2 fila 2: A=2215 B=0013 D=2210 -> S=00:08
    result = compute_revision(
        eta_aircon="0013", ctot_prev="2215", ctot_actual="2210", today=TODAY
    )
    assert result.eta_ctot_calculado == time(0, 8)


def test_revision_atraso_calculo_ctot2_row6():
    # Cálculo CTOT 2 fila 6: A=2348 B=0048 D=2351 -> S=00:51
    result = compute_revision(
        eta_aircon="0048", ctot_prev="2348", ctot_actual="2351", today=TODAY
    )
    assert result.eta_ctot_calculado == time(0, 51)


def test_revision_matches_formatofmp_cascade_row7():
    # FormatoFMP SUR fila 7: H (ETA AIRCON CALCULADO) = 00:51, igual al
    # resultado de Cálculo CTOT 2 (no hay nivel 3/4 activo para ese vuelo).
    result = compute_revision(
        eta_aircon="0048", ctot_prev="2348", ctot_actual="2351", today=TODAY
    )
    assert result.eta_ctot_calculado == time(0, 51)


def test_dla_formatofmp_row6():
    # FormatoFMP SUR fila 6: K(ETD1)=2311 L(CTOT1)=2319 -> AD(minutos) ~= 8
    dla_days, dla_minutes = compute_dla(etd_solicitado="2311", ctot_confirmado="2319")
    assert dla_minutes == 8
    assert round(dla_days * 24 * 60) == 8


def test_dla_sin_demora_es_cero():
    dla_days, dla_minutes = compute_dla(etd_solicitado="2215", ctot_confirmado="2215")
    assert dla_minutes == 0
    assert dla_days == 0


def test_dla_cruce_medianoche():
    # ETD tarde en la noche, CTOT confirmado ya en la madrugada siguiente.
    dla_days, dla_minutes = compute_dla(etd_solicitado="2350", ctot_confirmado="0010")
    assert dla_minutes == 20


def test_dla_cruce_medianoche_mas_de_una_hora():
    # Regresión: antes solo se detectaba el cruce si el CTOT caía justo en
    # la hora 00, así que un CTOT en la madrugada (ej. 01:15) después de un
    # ETD tarde en la noche (23:50) reportaba 0 minutos de demora en vez de
    # los 85 reales.
    dla_days, dla_minutes = compute_dla(etd_solicitado="2350", ctot_confirmado="0115")
    assert dla_minutes == 85


def test_resolve_day_offset_no_rollover():
    assert resolve_day_offset(time(22, 15), time(22, 10)) == 0
    assert resolve_day_offset(time(23, 48), time(23, 51)) == 0


def test_resolve_day_offset_rollover_hacia_adelante():
    # CTOT anterior de madrugada (04), CTOT nuevo cae en el bucket "24" (00h)
    # -> única rama de la fórmula original que devuelve +1.
    assert resolve_day_offset(time(4, 0), time(0, 0)) == 1


def test_resolve_day_offset_rollover_hacia_atras():
    # CTOT anterior recién pasada la medianoche (01-03), CTOT nuevo la noche anterior (23/24)
    assert resolve_day_offset(time(1, 0), time(23, 0)) == -1


def test_eta_calculado_sin_ningun_ctot_es_el_propio_eta_aircon():
    result = compute_eta_aircon_calculado("1030", "0900", None, None, None, None)
    assert result == time(10, 30)


def test_eta_calculado_ejemplo_1_solo_ctot1():
    # ETA AIRCON=10:30 ETD1=09:00 CTOT1=09:20 -> ETA CALC = 10:50
    result = compute_eta_aircon_calculado("1030", "0900", "0920", None, None, None)
    assert result == time(10, 50)


def test_eta_calculado_ejemplo_2_usa_el_ultimo_ctot_no_el_acumulado():
    # ETA AIRCON=10:30 ETD1=09:00 CTOT1=09:20 CTOT2=09:35
    # diferencia = CTOT2 - ETD1 = 35 min (no CTOT2 - CTOT1) -> ETA CALC = 11:05
    result = compute_eta_aircon_calculado("1030", "0900", "0920", "0935", None, None)
    assert result == time(11, 5)


def test_eta_calculado_ejemplo_3_cruce_de_medianoche():
    # ETA AIRCON=23:55 ETD1=23:40 CTOT2=00:20(d+1) -> diferencia +40 -> 00:35(d+1)
    result = compute_eta_aircon_calculado("2355", "2340", None, "0020", None, None)
    assert result == time(0, 35)


def test_eta_calculado_prioriza_ctot4_sobre_niveles_previos():
    result = compute_eta_aircon_calculado("1030", "0900", "0920", "0935", "0940", "1000")
    # diferencia = CTOT4(10:00) - ETD1(09:00) = 60 min
    assert result == time(11, 30)


def test_eta_calculado_sin_eta_aircon_es_none():
    assert compute_eta_aircon_calculado(None, "0900", "0920", None, None, None) is None


def test_eta_calculado_sin_etd1_mantiene_eta_aircon():
    result = compute_eta_aircon_calculado("1030", None, "0920", None, None, None)
    assert result == time(10, 30)


def test_nearest_delta_minutes_cruces_de_medianoche():
    assert nearest_delta_minutes(time(23, 50), time(0, 10)) == 20
    assert nearest_delta_minutes(time(23, 30), time(1, 0)) == 90
    assert nearest_delta_minutes(time(0, 15), time(23, 55)) == -20
