"""Importacion de grillas historicas; conserva los valores originales del archivo."""
from datetime import date

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import clock
from app.auth import require_shift, require_writable_shift
from app.database import get_db
from app.models import (
    Flight, FlightDeletionLog, FlightHistory, Importacion, Sector, ShiftLog,
    TipoImportacion,
)
from app.schemas import (
    FlightHistoryImportOut, FlightHistoryImportReport, FlightHistoryImportRowError,
    FlightHistoryOut, FlightHistoryPreview, FlightHistoryPreviewRow, FlightOut,
)
from app.services.flight_history_import import (
    InvalidFlightHistoryFile, campos_del_vuelo_historico, parse_flight_history_file,
)
from app.uploads import read_upload_limited

# 5 MB alcanzaban para el CSV de una hoja, pero el CONSOLIDADO del dia es
# un libro completo: el mas pesado de los 1.835 archivados pesa 3,1 MB y
# estaba a un pelo del limite.
MAX_HISTORY_UPLOAD_BYTES = 10 * 1024 * 1024

router = APIRouter()

@router.get("/flights/import-history/uploads", response_model=list[FlightHistoryImportOut])
def list_history_imports(db: Session = Depends(get_db), _: ShiftLog = Depends(require_shift)):
    """Historial de importaciones de vuelos históricos, la más reciente primero."""
    return db.execute(
        select(Importacion)
        .where(Importacion.tipo == TipoImportacion.GRILLA_HISTORICA)
        .order_by(Importacion.cargado_en.desc())
        .limit(50)
    ).scalars().all()


@router.post("/flights/import-history/preview", response_model=FlightHistoryPreview)
async def preview_flight_history(
    sector: Sector,
    flight_date: date,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    operator: ShiftLog = Depends(require_writable_shift),
):
    """Vista previa de la importación: parsea el archivo y devuelve las filas
    tal como quedarían, SIN guardar nada. El operador revisa la tabla y
    recién entonces confirma con POST /flights/import-history ("Procesar")."""
    content = await read_upload_limited(file, MAX_HISTORY_UPLOAD_BYTES)

    try:
        result = parse_flight_history_file(content, sector.value)
    except InvalidFlightHistoryFile as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    vuelos_existentes = db.execute(
        select(func.count()).select_from(Flight).where(
            Flight.sector == sector, Flight.flight_date == flight_date
        )
    ).scalar() or 0

    return FlightHistoryPreview(
        filas_aceptadas=result.filas_aceptadas,
        filas_rechazadas=result.filas_rechazadas,
        errores=[FlightHistoryImportRowError(row=r, motivo=m) for r, m in result.errores],
        rows=[FlightHistoryPreviewRow.model_validate(row, from_attributes=True) for row in result.rows],
        vuelos_existentes=vuelos_existentes,
    )


def _reemplazar_dia(
    db: Session, sector: Sector, flight_date: date, operator: ShiftLog, archivo: str | None
) -> int:
    """Borra los vuelos de ese sector y día, dejando constancia de cada uno.

    No es un DELETE a secas. El borrado de un vuelo se lleva su historial en
    cascada, así que antes se guarda una foto completa de ambos en
    `registro_eliminacion_vuelo` -- exactamente lo que hace el borrado
    individual desde la grilla (ver delete_flight). Reemplazar cien vuelos no
    puede dejar menos rastro que borrar uno.

    Se lleva TODO lo del sector y el día, no solo lo que vino de una carga
    anterior. El CONSOLIDADO es el registro completo de esa jornada: dejar
    vivos los vuelos cargados a mano los duplicaría contra los del archivo.
    El motivo queda escrito por si hay que recuperarlos.
    """
    ahora = clock.now_utc()
    motivo = f"Reemplazado por la carga de {archivo or 'un archivo'}"

    vuelos = db.execute(
        select(Flight).where(Flight.sector == sector, Flight.flight_date == flight_date)
    ).scalars().all()

    historial_por_vuelo: dict[int, list[FlightHistory]] = {}
    for fila in db.execute(
        select(FlightHistory).where(
            FlightHistory.flight_id.in_([v.id for v in vuelos])
        ).order_by(FlightHistory.changed_at)
    ).scalars().all():
        historial_por_vuelo.setdefault(fila.flight_id, []).append(fila)

    for vuelo in vuelos:
        db.add(
            FlightDeletionLog(
                original_flight_id=vuelo.id,
                sector=vuelo.sector,
                flight_date=vuelo.flight_date,
                vuelo=vuelo.vuelo,
                flight_snapshot=FlightOut.model_validate(vuelo).model_dump(mode="json"),
                history_snapshot=[
                    FlightHistoryOut.model_validate(h).model_dump(mode="json")
                    for h in historial_por_vuelo.get(vuelo.id, [])
                ],
                deleted_at=ahora,
                deleted_by=operator.operator_name,
                deleted_by_id=operator.operador_id,
                deletion_reason=motivo,
            )
        )
        db.delete(vuelo)

    # Se vuelca antes de insertar los nuevos: si no, la restricción de
    # (sector, fecha, numero_fila) choca contra las filas que todavía están.
    db.flush()
    return len(vuelos)


@router.post("/flights/import-history", response_model=FlightHistoryImportReport)
async def import_flight_history(
    sector: Sector,
    flight_date: date,
    reemplazar: bool = Query(
        False,
        description="Borra lo que ya haya cargado de ese sector y día, y carga el archivo en su lugar",
    ),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    operator: ShiftLog = Depends(require_writable_shift),
):
    """Carga vuelos ya cerrados (grilla FMP de meses/años anteriores) desde el
    CONSOLIDADO del día o el CSV exportado, un archivo por día. A diferencia de crear un vuelo desde la
    grilla en vivo, acá NO se cruza con el itinerario ni se recalcula nada
    (_resolve_slot_arr / recompute_flight): son datos históricos que ya
    tienen sus propios valores calculados en el archivo."""
    content = await read_upload_limited(file, MAX_HISTORY_UPLOAD_BYTES)

    existing = db.execute(
        select(func.count()).select_from(Flight).where(
            Flight.sector == sector, Flight.flight_date == flight_date
        )
    ).scalar()
    # Corregir un día ya cargado era un callejón sin salida: la única salida
    # que ofrecía el mensaje era borrar los vuelos uno por uno desde la
    # grilla, y un día tiene cerca de cien. Cuando la FMP recibe el
    # CONSOLIDADO corregido de una jornada, volver a cargarlo es la operación
    # normal, no la excepción.
    #
    # Sigue sin pisar nada por accidente: hay que pedirlo, igual que la carga
    # de itinerario exige `confirmado`.
    if existing and not reemplazar:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Ya hay {existing} vuelo(s) cargados para {sector.value} el {flight_date}. "
                "Volvé a cargar marcando \"reemplazar\" si querés que este archivo "
                "los sustituya."
            ),
        )

    reemplazados = 0
    if existing and reemplazar:
        reemplazados = _reemplazar_dia(db, sector, flight_date, operator, file.filename)

    try:
        result = parse_flight_history_file(content, sector.value)
    except InvalidFlightHistoryFile as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    now = clock.now_utc()

    # La ejecucion se registra ANTES de los vuelos, y se vuelca para obtener su
    # id: asi cada vuelo queda apuntando a la carga que lo creo. Antes el
    # registro se agregaba al final y no habia forma de relacionarlos.
    importacion = Importacion(
        tipo=TipoImportacion.GRILLA_HISTORICA,
        sector=sector,
        fecha_operacion=flight_date,
        cargado_en=now,
        cargado_por=operator.operator_name,
        cargado_por_id=operator.operador_id,
        nombre_archivo=file.filename,
        filas_aceptadas=result.filas_aceptadas,
        filas_rechazadas=result.filas_rechazadas,
    )
    db.add(importacion)
    db.flush()

    for numero, row in enumerate(result.rows, start=1):
        db.add(
            Flight(
                **campos_del_vuelo_historico(row),
                sector=sector,
                flight_date=flight_date,
                numero_fila=numero,
                updated_at=now,
                updated_by=operator.operator_name,
                updated_by_id=operator.operador_id,
                importacion_id=importacion.id,
                fila_origen=row.fila_origen,
            )
        )

    db.commit()

    return FlightHistoryImportReport(
        filas_aceptadas=result.filas_aceptadas,
        filas_rechazadas=result.filas_rechazadas,
        vuelos_reemplazados=reemplazados,
        errores=[FlightHistoryImportRowError(row=r, motivo=m) for r, m in result.errores],
    )
