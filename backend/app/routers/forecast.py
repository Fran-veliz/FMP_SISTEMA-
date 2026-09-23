from collections import Counter
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import require_shift, require_viewable_date
from app.database import get_db
from app.models import Direction, Flight, Sector, ShiftLog
from app.services import itinerario
from app.schemas import ForecastBucket, ForecastResponse

router = APIRouter(tags=["forecast"])

# El límite operacional (operaciones/hora, arribos + despegues) es un dato del
# aeródromo, no del sector: los 49 que estaban fijos acá son los de Lima. Vive
# en el catálogo `estaciones` -- ver itinerario.capacidad_declarada.


# Prefijo OACI del Perú: todo aeródromo nacional empieza con "SP" (SPJC Lima,
# SPZO Cusco, SPQU Arequipa, SPST Tarapoto...). La FMP gestiona el flujo de
# llegada solo de vuelos nacionales; los internacionales no entran en esa
# cuenta aunque sí sumen a la capacidad del aeródromo.
#
# Los vuelos militares, oficiales y navales operan desde aeródromos SP, así
# que este mismo criterio los incluye sin necesitar una regla aparte -- ver
# la nota sobre el itinerario en get_forecast.
PREFIJO_OACI_PERU = "SP"


def _es_nacional(aerodromo: str | None) -> bool:
    """En un arribo, `aerodromo` es el ORIGEN (el destino es SPJC, implícito).
    Sin origen no se puede afirmar que sea nacional, así que no cuenta."""
    if not aerodromo:
        return False
    return aerodromo.strip().upper().startswith(PREFIJO_OACI_PERU)


# Aeródromo de Cusco. Hay que aceptar las dos formas: la carga de temporada
# convierte IATA->OACI y guarda "SPZO", pero el formato antiguo copia la celda
# ORIGEN tal cual, y en la base conviven filas con "CUZ" sin convertir. Mirar
# solo una de las dos perdía vuelos en silencio.
CODIGOS_CUSCO = frozenset({"SPZO", "CUZ"})


def _es_cusco(aerodromo: str | None) -> bool:
    """En un arribo, `aerodromo` es el ORIGEN: True si el vuelo viene de Cusco."""
    if not aerodromo:
        return False
    return aerodromo.strip().upper() in CODIGOS_CUSCO


def _bucket_label(hour: int) -> str:
    """Rótulo de la franja horaria.

    La FMP cuenta la franja de HH:00 a HH:59 -- un vuelo a las 13:00 pertenece
    a la franja de las 13, no a la anterior. Es la misma convención que aplica
    `_hour_of` al quedarse con las dos primeras cifras del HHMM; el rótulo
    antes decía "13:01 - 14:00" y contradecía dónde caían los vuelos.
    """
    hour = hour % 24
    return f"{hour:02d}:00 - {hour:02d}:59"


def _hour_of(value: str | None) -> int | None:
    if not value or len(value) < 2:
        return None
    try:
        return int(value[:2]) % 24
    except ValueError:
        return None


@router.get("/forecast", response_model=ForecastResponse)
def get_forecast(
    flight_date: date,
    sector: Sector | None = Query(None, description="Si se omite, combina SUR + NOR"),
    estacion: str = Query(
        itinerario.ESTACION_POR_DEFECTO,
        description="Aeropuerto operativo de CTOT (SPJC); los demás se consultan en el portal",
    ),
    capacidad_maxima: int | None = Query(
        None, ge=1, description="Si se omite, se usa la capacidad declarada de la estación"
    ),
    db: Session = Depends(get_db),
    shift: ShiftLog = Depends(require_shift),
):
    # El límite de año de los perfiles de la DGAC se aplicaba SOLO en /flights
    # y en la exportación: por acá se podía pedir cualquier fecha y obtener la
    # carga del aeródromo de un año no autorizado. Comprobado con un perfil
    # DGAC2 pidiendo 2021.
    require_viewable_date(shift, flight_date)
    estacion = estacion.strip().upper()
    if estacion != itinerario.ESTACION_POR_DEFECTO:
        raise HTTPException(
            status_code=422,
            detail="El pronóstico CTOT corresponde a Lima (SPJC). "
            "Consulte los demás aeropuertos en el portal de itinerarios.",
        )
    # Pronosticado: todo el itinerario del día (arribos + despegues), es el
    # límite operacional del aeropuerto completo, no depende de sector.
    if capacidad_maxima is None:
        capacidad_maxima = itinerario.capacidad_declarada(db, estacion)
    if capacidad_maxima is None:
        raise HTTPException(status_code=422, detail="El aeropuerto no tiene capacidad declarada.")
    itinerary_entries = itinerario.entradas_del_dia(db, estacion, flight_date)
    pronosticado: Counter[int] = Counter()
    # Los arribos nacionales se cuentan aparte además de ir dentro del total:
    # son los que la FMP secuencia, así que el operador necesita verlos sin
    # tener que restar despegues e internacionales a ojo.
    #
    # Nota: el itinerario que publica la DGAC no trae vuelos militares ni
    # policiales (0 call signs FAP/MGP/PNP en `itinerary_entries`); esos se
    # cargan a mano en la grilla y por eso aparecen en la serie "trabajado",
    # no acá. Si alguna vez entran al itinerario, su origen SP los va a
    # incluir en esta cuenta sin tocar código.
    pronosticado_llegadas_nacionales: Counter[int] = Counter()
    # Arribos desde Cusco, para las posiciones FMP CUSCO: es el flujo que esa
    # posición secuencia, y en el total del aeródromo queda diluido entre casi
    # 50 operaciones por hora. Es un subconjunto de `pronosticado`, pero NO
    # necesariamente de las llegadas nacionales: una fila guardada como "CUZ"
    # es Cusco y sin embargo no pasa el prefijo OACI "SP".
    pronosticado_llegadas_cusco: Counter[int] = Counter()
    # Las nacionales que NO son de Cusco. Se cuenta aparte en vez de restar en
    # el frontend porque Cusco no está garantizado como subconjunto de las
    # nacionales (una fila "CUZ" es Cusco y no pasa el prefijo "SP"), y una
    # resta podría dar negativo. Así el gráfico puede apilar Cusco + otras
    # nacionales sabiendo que las dos partes no se solapan.
    pronosticado_llegadas_nacionales_otras: Counter[int] = Counter()
    # Despegues. Hoy la FMP no los gestiona, pero el gráfico deja activarlos
    # como serie aparte porque es trabajo previsto (ver la nota del área).
    pronosticado_despegues: Counter[int] = Counter()
    for hora_utc, direction, aerodromo in itinerary_entries:
        hour = _hour_of(hora_utc)
        if hour is not None:
            pronosticado[hour] += 1
            if direction == Direction.DEP:
                pronosticado_despegues[hour] += 1
            if direction == Direction.ARR:
                if _es_nacional(aerodromo):
                    pronosticado_llegadas_nacionales[hour] += 1
                    if not _es_cusco(aerodromo):
                        pronosticado_llegadas_nacionales_otras[hour] += 1
                # Se cuenta aparte, no dentro de la rama nacional: "CUZ" es
                # Cusco pero no empieza con SP, así que anidarlo dejaba fuera
                # justo las filas sin convertir a OACI.
                if _es_cusco(aerodromo):
                    pronosticado_llegadas_cusco[hour] += 1

    # Trabajado: vuelos ya gestionados en la(s) grilla(s) FMP, sin contar cancelados.
    stmt = select(Flight).where(Flight.flight_date == flight_date, Flight.cancelado.is_(False))
    if sector is not None:
        stmt = stmt.where(Flight.sector == sector)
    flights = db.execute(stmt).scalars().all()

    trabajado: Counter[int] = Counter()
    for flight in flights:
        hour = _hour_of(flight.eta_aircon_calculado or flight.eta_aircon)
        if hour is not None:
            trabajado[hour] += 1

    buckets = [
        ForecastBucket(
            rango_hora=_bucket_label(h),
            pronosticado=pronosticado.get(h, 0),
            pronosticado_llegadas_nacionales=pronosticado_llegadas_nacionales.get(h, 0),
            pronosticado_llegadas_cusco=pronosticado_llegadas_cusco.get(h, 0),
            pronosticado_llegadas_nacionales_otras=pronosticado_llegadas_nacionales_otras.get(h, 0),
            pronosticado_despegues=pronosticado_despegues.get(h, 0),
            trabajado=trabajado.get(h, 0),
            capacidad_maxima=capacidad_maxima,
        )
        for h in range(24)
    ]

    return ForecastResponse(sector=sector, fecha=flight_date, buckets=buckets)
