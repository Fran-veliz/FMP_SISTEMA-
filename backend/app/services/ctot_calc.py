"""Funciones de dominio para los cálculos de FormatoFMP (ETA AIRCON
CALCULADO, DLA) a partir de los strings HHMM que tipean los operadores.

`compute_revision` / `resolve_day_offset` son un port puro de las hojas
'Cálculo CTOT 2/3/4' (+ NOR) del Excel original, validado fila por fila
contra los valores cacheados de 'JUNIO 3 CONSOLIDADO(1).xlsx' (hoja
'Cálculo CTOT 2', filas 2 y 6) -- ver DECISIONES.md §2.9, no se tocan para
mantener paridad con lo que los operadores FMP ya conocen. Ya no alimentan
ETA AIRCON CALCULADO (ver `compute_eta_aircon_calculado` más abajo), se
conservan como referencia y por sus tests de paridad.

Referencia de origen (columnas de 'Cálculo CTOT 2', mismo patrón en 3 y 4):
  A=CTOT(n-1)  B=ETA AIRCON  C=ETD(n)  D=CTOT(n)
  E=Fecha Hora DEP (hoy + A)      J=Fecha Hora CTOT(n) (hoy[+offset] + D)
  M=adelanto (E-J si >=0)         N=atraso (J-E si >=0)
  S=ETA-CTOT calculado = ETA_original - (E-J), normalizado a 24h.

`compute_eta_aircon_calculado` es la fórmula vigente de ETA AIRCON
CALCULADO (decisión de negocio 2026-07-06, ver DECISIONES.md §2.12): ETA
AIRCON desplazado por la diferencia entre el último CTOT confirmado
(CTOT4 > CTOT3 > CTOT2 > CTOT1, el más profundo cargado) y ETD1,
recalculada siempre desde esos dos valores -- sin acumular los
desplazamientos de niveles intermedios.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

SEC_REASONS = [
    "SxSEC", "SxTFC", "SxSEC&TFC", "SxLIMCAP", "SxTMI", "SxACC",
    "SxNOTAM", "MEDEVAC", "HMDEP", "OMC", "USA EMBASSY", "HEAD", "QRF",
]

REV_REASONS = [
    "RxSEC", "RxCIADEP", "RxCIAADH", "RxCIAyTFC", "RxTMI", "RxLIMCAP",
    "RxTWR", "RxACC", "RxTFC", "RxCIAROD", "RxCIADOC", "RxCIAPDR",
    "RxWIND", "RxBIRDS", "RxMET", "RxNOTAM",
]


def es_madrugada(hora: str | None) -> bool:
    """True si una hora HHMM cae entre 0000 y 0559 UTC. Único lugar donde
    vive este umbral -- lo usan tanto la búsqueda en vivo del itinerario
    (routers/itinerary.py, sobre la hora actual del servidor) como la
    resolución del slot al guardar el vuelo (routers/flights.py, sobre
    flight.hora), para que no queden dos copias de la misma regla que
    puedan divergir con el tiempo."""
    if not hora or len(hora) != 4 or not hora.isdigit():
        return False
    return int(hora[:2]) < 6


def parse_hhmm(value: str | None) -> time | None:
    """Convierte 'HHMM' (como lo tipea el operador) a time. '' / None -> None.

    Replica IFERROR(TIME(MID(x,1,2),RIGHT(x,2),0),"") de FormatoFMP!AH..AQ.
    """
    if value in (None, 0):
        return None
    s = str(value).strip()
    if s == "":
        return None
    s = s.zfill(4)
    try:
        hh = int(s[:2]) % 24
        mm = int(s[2:4])
        return time(hh, mm)
    except ValueError:
        return None


def _day_bucket(t: time) -> str:
    """'24' para medianoche, 'HH' en cualquier otro caso — así es como el
    Excel compara F/K (columna I) para detectar cruces de medianoche."""
    return "24" if t.hour == 0 else f"{t.hour:02d}"


def resolve_day_offset(hora_ref: time, hora_nueva: time) -> int:
    """Replica la columna I: offset de día (-1, 0, +1) entre la hora del
    CTOT anterior (hora_ref) y la hora del CTOT de este nivel (hora_nueva),
    para vuelos que cruzan medianoche (horas '01'..'03' vs '23'/'24')."""
    g, h = _day_bucket(hora_ref), _day_bucket(hora_nueva)
    if g < h and h == "23" and g in ("01", "02", "03"):
        return -1
    if g < h and h == "24" and g in ("01", "02", "03"):
        return -1
    if g > h and g == "24" and h in ("01", "02", "03"):
        return 0
    if g > h and g == "24":
        return -1
    if g != "24" and g > h:
        return 0
    if g < h and h == "24":
        return 1
    return 0


@dataclass
class RevisionResult:
    eta_ctot_calculado: time | None  # columna S
    delta_minutes: int  # minutos que se desplazó el ETA (+ atraso, - adelanto)


def compute_revision(
    eta_aircon: str | None,
    ctot_prev: str | None,
    ctot_actual: str | None,
    today: date | None = None,
) -> RevisionResult:
    """Replica columnas E..S de 'Cálculo CTOT n'.

    eta_aircon:  ETA AIRCON original (columna B)
    ctot_prev:   CTOT confirmado del nivel anterior (columna A)
    ctot_actual: CTOT confirmado de este nivel (columna D)
    """
    eta = parse_hhmm(eta_aircon)
    prev_t = parse_hhmm(ctot_prev)
    actual_t = parse_hhmm(ctot_actual)
    if eta is None or prev_t is None or actual_t is None:
        return RevisionResult(None, 0)

    today = today or date.today()
    offset = resolve_day_offset(prev_t, actual_t)

    e_dt = datetime.combine(today, prev_t)
    j_dt = datetime.combine(today + timedelta(days=offset), actual_t)
    delta_minutes = round((e_dt - j_dt).total_seconds() / 60)

    eta_dt = datetime.combine(today, eta) - timedelta(minutes=delta_minutes)
    return RevisionResult(eta_dt.time(), -delta_minutes)


def nearest_delta_minutes(reference: time, target: time) -> int:
    """Minutos para ir de `reference` a `target`, asumiendo siempre la
    ocurrencia más cercana en el tiempo (nunca más de 12h en cualquier
    sentido). Aritmética real de reloj de 24h, sin heurísticas de bucket:
    cubre cualquier cruce de medianoche en cualquier dirección."""
    ref_minutes = reference.hour * 60 + reference.minute
    target_minutes = target.hour * 60 + target.minute
    diff = target_minutes - ref_minutes
    return ((diff + 720) % 1440) - 720


def compute_eta_aircon_calculado(
    eta_aircon: str | None,
    etd1: str | None,
    ctot1: str | None,
    ctot2: str | None,
    ctot3: str | None,
    ctot4: str | None,
    ctot5: str | None = None,
    today: date | None = None,
) -> time | None:
    """ETA AIRCON CALCULADO = ETA AIRCON + (último CTOT confirmado - ETD1).

    "Último CTOT confirmado" es el más profundo entre CTOT5..CTOT1 (el que
    esté cargado). El resultado se recalcula siempre desde ETA AIRCON y
    ETD1 -- no se acumulan los desplazamientos de niveles intermedios. Si
    no hay ningún CTOT cargado, el resultado es el propio ETA AIRCON. Si
    falta ETA AIRCON o ETD1 (con algún CTOT cargado), no se puede calcular
    el desplazamiento y se devuelve ETA AIRCON sin modificar.

    `ctot5` va con default para no romper las llamadas de los tests de
    paridad, escritos cuando la grilla llegaba hasta el Nivel 4."""
    eta = parse_hhmm(eta_aircon)
    if eta is None:
        return None

    ultimo_ctot = (
        parse_hhmm(ctot5)
        or parse_hhmm(ctot4)
        or parse_hhmm(ctot3)
        or parse_hhmm(ctot2)
        or parse_hhmm(ctot1)
    )
    if ultimo_ctot is None:
        return eta

    etd1_t = parse_hhmm(etd1)
    if etd1_t is None:
        return eta

    today = today or date.today()
    delta = nearest_delta_minutes(etd1_t, ultimo_ctot)
    return (datetime.combine(today, eta) + timedelta(minutes=delta)).time()


def compute_dla(
    etd_solicitado: str | None,
    ctot_confirmado: str | None,
) -> tuple[float, float]:
    """Replica columnas AC (DLA, tiempo) / AD (DLA en minutos) de FormatoFMP
    para el nivel activo: diferencia entre el CTOT confirmado y el ETD
    solicitado de ese mismo nivel, asumiendo siempre el cruce de medianoche
    más cercano (vía `nearest_delta_minutes`, no solo cuando el CTOT cae
    justo en la hora 00). Devuelve (dla_dias, dla_minutos); dla_minutos
    siempre >= 0 (si el CTOT es anterior al ETD, no hay demora)."""
    etd = parse_hhmm(etd_solicitado)
    ctot = parse_hhmm(ctot_confirmado)
    if etd is None or ctot is None:
        return (0.0, 0.0)

    minutes = max(nearest_delta_minutes(etd, ctot), 0.0)
    return (minutes / (24 * 60), minutes)
