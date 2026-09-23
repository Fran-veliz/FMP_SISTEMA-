"""Único punto de acceso a `itinerary_entries`.

Existe por un problema concreto: hasta que entró Cusco, el itinerario de Lima
era "el" itinerario y nadie tenía que decir de quién era. Cuatro consultas
repartidas en tres routers leían la tabla filtrando solo por fecha, y ninguna
estaba mal -- simplemente no había otra estación posible.

Al sumar la segunda, esas cuatro se vuelven incorrectas solas, sin que nadie
toque una línea: el mismo call sign figura el mismo día en los dos itinerarios
(sale de Lima, llega a Cusco). Parchear los cuatro lugares arreglaba el
presente y dejaba el mismo agujero para la quinta consulta que alguien
escribiera después, con el mismo final silencioso.

Así que la tabla se consulta solo desde acá y `estacion` es obligatoria en
todas las funciones. Una consulta al itinerario sin estación ya no es un
descuido que se descubre en producción: no llega a escribirse.
"""

from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from app.models import Aerodromo, CapacidadAerodromo, Direction, Flight, ItineraryEntry, Sector
from app.services.ctot_calc import es_madrugada

# Aeródromo que el sistema gestionó en solitario hasta 2026: Lima. Es el valor
# con el que se rellenó todo el histórico y el que asumen los endpoints cuando
# no se pide otra cosa, para que nada de lo que ya funcionaba cambie.
ESTACION_POR_DEFECTO = "SPJC"

# Capacidad que se usa si la estación todavía no tiene la suya cargada. Es la
# de Lima (49 operaciones/hora, arribos + despegues), que es la que estuvo
# siempre fija en routers/forecast.py.
CAPACIDAD_POR_OMISION = 49


# Qué estación opera cada sector de la grilla. Hoy solo Lima tiene grilla: SUR
# y NOR son sus dos sectores. Las estaciones que se agreguen entran primero
# como itinerario nada más -- se guardan y se consultan, pero nadie las
# secuencia todavía -- así que no figuran acá.
#
# Las dos funciones de abajo leen de este mismo diccionario a propósito. Son
# recorridos inversos de la misma relación, y escritas por separado podían
# quedar diciendo cosas distintas: bastaba con agregar un sector en una y
# olvidarlo en la otra para que un vuelo resolviera su slot contra el
# itinerario de un aeródromo y a la vez quedara fuera del alcance de las
# cancelaciones de ese mismo aeródromo.
_ESTACION_DE_SECTOR: dict[Sector, str] = {
    Sector.SUR: ESTACION_POR_DEFECTO,
    Sector.NOR: ESTACION_POR_DEFECTO,
}

# Todo sector de la grilla tiene que saber de qué aeródromo es. Si mañana se
# agrega uno y nadie lo mapea, esto corta el arranque en vez de dejar que el
# sistema resuelva sus vuelos contra el itinerario de Lima sin decirlo: un
# slot de otro aeródromo escrito en la celda no se distingue de uno correcto,
# y el vuelo pasa el control de "está en el itinerario DGAC" sin estarlo.
#
# Se comprueba acá, al importar el módulo, y no dentro de la función: un fallo
# en medio de un POST deja al operador sin poder cargar el vuelo en plena
# operación; uno en el arranque aparece en el despliegue, que es cuando se
# puede arreglar.
_sin_estacion = [s.value for s in Sector if s not in _ESTACION_DE_SECTOR]
if _sin_estacion:
    raise RuntimeError(
        "Estos sectores de la grilla no tienen aeródromo asignado en "
        f"_ESTACION_DE_SECTOR: {', '.join(_sin_estacion)}"
    )


def sectores_de_estacion(estacion: str) -> list[Sector]:
    """Qué sectores de la grilla FMP operan esta estación.

    Devolver la lista vacía no es un caso degenerado, es la respuesta correcta:
    impide que cargar el itinerario de Cusco cancele vuelos de la grilla de
    Lima por coincidencia de call sign (ver `filtro_vuelos_del_tramo`).
    """
    return [s for s, e in _ESTACION_DE_SECTOR.items() if e == estacion]


def estacion_de_sector(sector: Sector) -> str:
    """De qué aeródromo es el vuelo que está en este sector de la grilla.

    Es el dato que le falta al cruce con el itinerario: sin él, `buscar_arribo`
    no sabe en qué itinerario mirar y el mismo call sign del mismo día resuelve
    contra el aeródromo equivocado (sale de Lima, llega a Cusco).

    No tiene valor por omisión a propósito: la comprobación de más arriba
    garantiza que todo sector esté mapeado antes de que el módulo cargue, así
    que acá no hay caso "no sé de quién es" que atender.
    """
    return _ESTACION_DE_SECTOR[sector]


def buscar_arribo(
    db: Session, estacion: str, flight_date: date, call_sign: str
) -> ItineraryEntry | None:
    """La llegada de ese call sign a esa estación ese día, si está en el
    itinerario. Es el cruce equivalente al VLOOKUP(D3, INFO!B:C, ...) del
    Excel original."""
    return db.execute(
        select(ItineraryEntry).where(
            ItineraryEntry.estacion == estacion,
            ItineraryEntry.flight_date == flight_date,
            ItineraryEntry.call_sign == call_sign,
            ItineraryEntry.direction == Direction.ARR,
        )
    ).scalars().first()


def resolver_arribo(
    db: Session,
    estacion: str,
    flight_date: date,
    call_sign: str,
    *,
    hora_referencia: str | None,
) -> tuple[ItineraryEntry | None, bool]:
    """Busca hoy y, solo en madrugada, ayer; indica si la coincidencia es D-1.

    El llamador aporta la hora: la comunicación del vuelo al guardarlo o la
    hora actual del servidor durante la consulta previa.
    """
    entry = buscar_arribo(db, estacion, flight_date, call_sign)
    if entry is not None:
        return entry, False
    if es_madrugada(hora_referencia):
        entry = buscar_arribo(db, estacion, flight_date - timedelta(days=1), call_sign)
        if entry is not None:
            return entry, True
    return None, False


def entradas_del_dia(
    db: Session, estacion: str, flight_date: date
) -> list[tuple[str | None, Direction, str | None]]:
    """(hora_utc, direction, aerodromo) de todo el itinerario del día en esa
    estación -- arribos y despegues. Lo usa /forecast para el pronosticado."""
    return list(
        db.execute(
            select(
                ItineraryEntry.hora_utc,
                ItineraryEntry.direction,
                ItineraryEntry.aerodromo,
            ).where(
                ItineraryEntry.estacion == estacion,
                ItineraryEntry.flight_date == flight_date,
            )
        ).all()
    )


def filtro_tramo(estacion: str, alcance: str, effective_from: date) -> ColumnElement[bool]:
    """Qué tramo del itinerario toca una carga.

    "dia"   -> solo `effective_from` (carga diaria suelta: no toca los días
               posteriores ya cargados).
    "desde" -> de `effective_from` en adelante (actualización de temporada de
               la DGAC, que sí reemplaza todo lo que sigue).

    Alimenta el DELETE de upload_itinerary, así que la estación no es un
    detalle: sin ella, subir el itinerario de Cusco con alcance "desde"
    borraba el de Lima de esa fecha en adelante, sin vuelta atrás.
    """
    por_estacion = ItineraryEntry.estacion == estacion
    if alcance == "dia":
        return por_estacion & (ItineraryEntry.flight_date == effective_from)
    return por_estacion & (ItineraryEntry.flight_date >= effective_from)


def filtro_vuelos_del_tramo(
    estacion: str, alcance: str, effective_from: date
) -> ColumnElement[bool]:
    """El mismo tramo, pero sobre la grilla de vuelos.

    Acotado a los sectores de la estación: una carga de Cusco no puede
    cancelar ni restaurar vuelos de la grilla de Lima. Si la estación no tiene
    sectores, `in_([])` es falso y no alcanza a ningún vuelo, que es lo que
    corresponde.
    """
    por_sector = Flight.sector.in_(sectores_de_estacion(estacion))
    if alcance == "dia":
        return por_sector & (Flight.flight_date == effective_from)
    return por_sector & (Flight.flight_date >= effective_from)


def pares_cargados(
    db: Session, estacion: str, alcance: str, effective_from: date
) -> set[tuple[date, str]]:
    """(fecha, call sign) del itinerario ya cargado en el tramo que esta carga
    va a reemplazar."""
    return {
        (fd, cs)
        for fd, cs in db.execute(
            select(ItineraryEntry.flight_date, ItineraryEntry.call_sign).where(
                filtro_tramo(estacion, alcance, effective_from)
            )
        ).all()
    }


def capacidad_declarada(db: Session, estacion: str) -> int:
    """Operaciones/hora que admite el aeródromo. Es un dato por estación --
    los 49 de Lima no son los de Cusco -- así que sale del catálogo y no de
    una constante."""
    valor = db.execute(
        select(CapacidadAerodromo.valor)
        .join(Aerodromo, Aerodromo.id == CapacidadAerodromo.aerodromo_id)
        .where(
            Aerodromo.codigo_oaci == estacion,
            CapacidadAerodromo.valor.is_not(None),
            # La vigente: la que no tiene fecha de fin. Hoy hay una sola por
            # aeródromo; la columna existe para cuando la DGAC publique un
            # cambio y haya que conservar la anterior.
            CapacidadAerodromo.vigente_hasta.is_(None),
        )
        .order_by(CapacidadAerodromo.vigente_desde.desc().nullslast())
        .limit(1)
    ).scalar_one_or_none()
    return valor if valor is not None else CAPACIDAD_POR_OMISION


def existe(db: Session, estacion: str) -> bool:
    """Si la estación está en el catálogo y activa."""
    return db.execute(
        select(Aerodromo.id).where(
            Aerodromo.codigo_oaci == estacion,
            Aerodromo.es_estacion_ctot.is_(True),
            Aerodromo.activo.is_(True),
        )
    ).scalar_one_or_none() is not None
