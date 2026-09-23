import logging
from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import clock
from app.auth import (
    es_correccion_de_dia_pasado,
    require_shift,
    require_viewable_date,
    require_writable_date,
    require_writable_shift,
)
from app.database import get_db
from app.models import Flight, FlightDeletionLog, FlightHistory, Sector, ShiftLog
from app.routers import flight_export, flight_history, flight_socket
from app.schemas import FlightCreate, FlightHistoryOut, FlightOut, FlightUpdate
from app.services import itinerario, revisiones
from app.services.recompute import recompute_flight
from app.ws import manager

logger = logging.getLogger(__name__)
router = APIRouter(tags=["flights"])

TRACKED_HISTORY_FIELDS = [
    "hora", "d_ats", "vuelo", "adep", "dep", "eta_aircon",
    "etd1", "ctot1", "sec1", "h_rev1", "rev1",
    "etd2", "ctot2", "sec2", "h_rev2", "rev2",
    "etd3", "ctot3", "sec3", "h_rev3", "rev3",
    "etd4", "ctot4", "sec4", "h_rev4", "rev4",
    "etd5", "ctot5", "sec5",
    "observaciones", "cancelado",
]


async def _avisar_si_corrige_dia_pasado(fecha: date, operador: str, vuelo: str, accion: str) -> None:
    """Deja constancia de que se tocó una jornada ya cerrada.

    Corregir el día anterior está permitido, pero no debe pasar inadvertido:
    queda en el registro del servidor y se difunde a las demás estaciones, que
    es donde el resto de la FMP lo va a ver."""
    if not es_correccion_de_dia_pasado(fecha):
        return
    logger.warning(
        "correccion de dia pasado: %s %s el vuelo %s del %s", operador, accion, vuelo, fecha
    )
    await manager.broadcast(
        {
            "type": "correccion_dia_pasado",
            "operator_name": operador,
            "flight_date": fecha.isoformat(),
            "vuelo": vuelo,
            "accion": accion,
        }
    )


def _resolve_slot_arr(db: Session, flight: Flight) -> None:
    """Cruce equivalente al VLOOKUP(D3, INFO!B:C, ...) del Excel. Un vuelo sin
    coincidencia (militar, policial, no comercial) no es un error: se deja el
    campo vacío en vez de mostrar un texto de alarma.

    El itinerario se consulta acotado a la estación del sector: desde que hay
    más de un aeródromo cargado, el mismo call sign figura el mismo día en dos
    itinerarios (sale de Lima, llega a Cusco) y sin acotar se le escribía a un
    vuelo de Lima la hora de Cusco.

    Caso madrugada: un vuelo del itinerario del día anterior que despega
    después de medianoche (comunicación entre 0000 y 0559 UTC) no aparece en
    el itinerario de hoy. Se busca también en el del día anterior y el slot
    se marca con "D-1" para que el operador FMP sepa de qué itinerario viene."""
    estacion = itinerario.estacion_de_sector(flight.sector)
    entry, dia_anterior = itinerario.resolver_arribo(
        db, estacion, flight.flight_date, flight.vuelo, hora_referencia=flight.hora
    )
    if entry and dia_anterior:
        flight.slot_arr_dgac = f"{entry.hora_utc} D-1" if entry.hora_utc else None
    else:
        flight.slot_arr_dgac = entry.hora_utc if entry else None
    if entry and not flight.adep:
        flight.adep = entry.aerodromo


def _record_history(
    db: Session,
    flight: Flight,
    changes: dict,
    operator_name: str | None,
    operador_id: int | None = None,
) -> None:
    now = clock.now_utc()
    for field_name, (old_value, new_value) in changes.items():
        if old_value == new_value:
            continue
        db.add(
            FlightHistory(
                flight_id=flight.id,
                field_name=field_name,
                old_value=None if old_value is None else str(old_value),
                new_value=None if new_value is None else str(new_value),
                # Se guardan los dos: el ID es la referencia canónica y el
                # nombre queda como el texto que se registró en su momento.
                operator_name=operator_name,
                operador_id=operador_id,
                changed_at=now,
            )
        )


@router.get("/flights", response_model=list[FlightOut])
def list_flights(
    sector: Sector,
    flight_date: date,
    db: Session = Depends(get_db),
    shift: ShiftLog = Depends(require_shift),
):
    """Exige turno abierto: hasta la v3.1 las lecturas eran anónimas, lo que
    dejaba la grilla completa al alcance de cualquiera con acceso al puerto
    (ver DECISIONES.md §5). Además aplica el límite de año de los perfiles
    de la DGAC."""
    require_viewable_date(shift, flight_date)
    flights = db.execute(
        select(Flight)
        .where(Flight.sector == sector, Flight.flight_date == flight_date)
        .order_by(Flight.numero_fila)
    ).scalars().all()
    return flights


@router.get("/flights/{flight_id}/history", response_model=list[FlightHistoryOut])
def get_flight_history(
    flight_id: int,
    db: Session = Depends(get_db),
    shift: ShiftLog = Depends(require_shift),
):
    # El límite de año se aplica por la fecha del vuelo: si no, un perfil
    # acotado veía el historial completo de cualquier vuelo pidiendo su id.
    vuelo = db.get(Flight, flight_id)
    if vuelo is None:
        raise HTTPException(status_code=404, detail="Vuelo no encontrado")
    require_viewable_date(shift, vuelo.flight_date)

    return db.execute(
        select(FlightHistory)
        .where(FlightHistory.flight_id == flight_id)
        .order_by(FlightHistory.changed_at.desc())
    ).scalars().all()


@router.post("/flights", response_model=FlightOut)
async def create_flight(
    payload: FlightCreate,
    db: Session = Depends(get_db),
    operator: ShiftLog = Depends(require_writable_shift),
):
    # La jornada tiene que estar abierta a correcciones. Esta regla vivía solo
    # en el frontend y una llamada directa la sorteaba por completo.
    require_writable_date(payload.flight_date)

    flight = Flight(**payload.model_dump())
    # La HORA es la hora de la comunicación desde provincia: se registra
    # automáticamente con la hora UTC del momento en que se crea el vuelo.
    if not flight.hora:
        flight.hora = clock.now_utc().strftime("%H%M")
    _resolve_slot_arr(db, flight)
    recompute_flight(flight)
    revisiones.sellar(db, flight, operator.operador_id)

    # numero_fila = MAX(numero_fila)+1 sin lock: dos POST concurrentes para el mismo
    # sector/fecha pueden leer el mismo máximo. La constraint única lo
    # detecta y acá se reintenta con un número recalculado en vez de dejar
    # pasar filas duplicadas.
    max_attempts = 3
    for attempt in range(max_attempts):
        max_numero = db.execute(
            select(func.max(Flight.numero_fila)).where(
                Flight.sector == flight.sector, Flight.flight_date == flight.flight_date
            )
        ).scalar()
        flight.numero_fila = (max_numero or 0) + 1
        db.add(flight)
        try:
            db.commit()
            break
        except IntegrityError:
            db.rollback()
            if attempt == max_attempts - 1:
                raise
    db.refresh(flight)

    await manager.broadcast({"type": "flight_upserted", "flight": FlightOut.model_validate(flight).model_dump(mode="json")})
    await _avisar_si_corrige_dia_pasado(flight.flight_date, operator.operator_name, flight.vuelo, "creó")
    return flight


@router.patch("/flights/{flight_id}", response_model=FlightOut)
async def update_flight(
    flight_id: int,
    payload: FlightUpdate,
    db: Session = Depends(get_db),
    operator: ShiftLog = Depends(require_writable_shift),
):
    flight = db.get(Flight, flight_id)
    if flight is None:
        raise HTTPException(status_code=404, detail="Vuelo no encontrado")
    require_writable_date(flight.flight_date)

    updates = payload.model_dump(exclude_unset=True)
    # El operador que firma el cambio es siempre el del turno autenticado,
    # nunca el que mande el cliente en el payload (evita que cualquiera
    # firme cambios con el nombre de otro operador FMP).
    updates.pop("updated_by", None)
    operator_name = operator.operator_name
    # Regla de negocio: HORA (hora de la comunicación) y VUELO son inmutables
    # una vez registrados. Si hubo error de tipeo, se elimina la fila y se
    # vuelve a crear.
    updates.pop("hora", None)
    updates.pop("vuelo", None)
    changes = {
        key: (getattr(flight, key), value)
        for key, value in updates.items()
        if key in TRACKED_HISTORY_FIELDS
    }
    for key, value in updates.items():
        setattr(flight, key, value)
    flight.updated_by = operator_name
    flight.updated_by_id = operator.operador_id

    _resolve_slot_arr(db, flight)
    recompute_flight(flight)
    revisiones.sellar(db, flight, operator.operador_id)
    _record_history(db, flight, changes, operator_name, operator.operador_id)
    db.commit()
    db.refresh(flight)

    await manager.broadcast({"type": "flight_upserted", "flight": FlightOut.model_validate(flight).model_dump(mode="json")})
    return flight


@router.delete("/flights/{flight_id}", status_code=204)
async def delete_flight(
    flight_id: int,
    reason: str | None = None,
    db: Session = Depends(get_db),
    operator: ShiftLog = Depends(require_writable_shift),
):
    flight = db.get(Flight, flight_id)
    if flight is None:
        raise HTTPException(status_code=404, detail="Vuelo no encontrado")
    require_writable_date(flight.flight_date)
    fecha_borrada, vuelo_borrado = flight.flight_date, flight.vuelo

    # El borrado de acá para abajo es permanente y se lleva el historial en
    # cascada (FlightHistory.flight_id) -- se guarda una foto de ambos antes
    # de borrar, para poder auditar después qué vuelo era y quién lo borró.
    history_rows = db.execute(
        select(FlightHistory).where(FlightHistory.flight_id == flight_id).order_by(FlightHistory.changed_at)
    ).scalars().all()
    db.add(
        FlightDeletionLog(
            original_flight_id=flight.id,
            sector=flight.sector,
            flight_date=flight.flight_date,
            vuelo=flight.vuelo,
            flight_snapshot=FlightOut.model_validate(flight).model_dump(mode="json"),
            history_snapshot=[FlightHistoryOut.model_validate(h).model_dump(mode="json") for h in history_rows],
            deleted_at=clock.now_utc(),
            deleted_by=operator.operator_name,
            deleted_by_id=operator.operador_id,
            deletion_reason=reason.strip() if reason and reason.strip() else None,
        )
    )
    db.delete(flight)
    db.commit()
    await manager.broadcast({"type": "flight_deleted", "id": flight_id})
    await _avisar_si_corrige_dia_pasado(fecha_borrada, operator.operator_name, vuelo_borrado, "borró")

router.include_router(flight_history.router)
router.include_router(flight_export.router)
router.include_router(flight_socket.router)
