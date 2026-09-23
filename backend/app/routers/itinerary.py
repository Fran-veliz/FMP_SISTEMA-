from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app import clock
from app.auth import require_shift, require_viewable_date, require_writable_shift
from app.services import itinerario
from app.database import get_db
from app.uploads import read_upload_limited
from app.services.excel_import import (
    ImportResult,
    InvalidItineraryFile,
    ItineraryRow,
    detect_itinerary_format,
    parse_itinerary_csv,
    parse_itinerary_excel,
    parse_itinerary_season_excel,
)
from app.models import (
    Aerodromo,
    Direction,
    Flight,
    Importacion,
    ItineraryEntry,
    ShiftLog,
    TipoImportacion,
)
from app.schemas import (
    ItineraryLookup,
    ItineraryPreview,
    ItinerarySheet,
    ItineraryPreviewDay,
    ItineraryRowError,
    ItineraryUploadOut,
    ItineraryUploadReport,
)

router = APIRouter(tags=["itinerary"])

# El libro de temporada que manda la DGAC viene creciendo (12 MB en set-2026,
# 17 MB en alguna version anterior) porque arrastra las hojas de trabajo de
# toda la temporada. Con el limite de 10 MB el archivo se rechazaba con 413
# antes de poder leerlo.
MAX_UPLOAD_BYTES = 30 * 1024 * 1024


@router.get("/itinerary/lookup", response_model=ItineraryLookup)
def lookup_itinerary(
    flight_date: date,
    call_sign: str,
    estacion: str = Query(
        itinerario.ESTACION_POR_DEFECTO,
        description="Aeródromo cuyo itinerario se consulta (OACI)",
    ),
    db: Session = Depends(get_db),
    shift: ShiftLog = Depends(require_shift),
):
    """Consulta en vivo mientras el operador FMP tipea el call sign, ANTES de
    agregar el vuelo: ¿está en el itinerario DGAC? Aplica la misma regla que
    la creación: en la madrugada (0000–0559 UTC) también se busca en el
    itinerario del día anterior."""
    # Mismo motivo que en /forecast: el límite de año de la DGAC se aplicaba
    # solo en /flights, así que por acá se podía consultar el itinerario de un
    # año no autorizado.
    require_viewable_date(shift, flight_date)
    estacion = _validar_estacion(db, estacion)
    buscado = call_sign.strip().upper()

    # Aproximación: a esta altura el vuelo todavía no existe, así que se usa
    # la hora actual del servidor como estimado de cuál será su HORA (que se
    # autocompleta con la hora actual al crear). Puede diferir de la
    # decisión real tomada en _resolve_slot_arr (flights.py) si pasan varios
    # segundos entre este preview y el guardado, justo cruzando las 06:00 UTC.
    entry, dia_anterior = itinerario.resolver_arribo(
        db, estacion, flight_date, buscado,
        hora_referencia=clock.now_utc().strftime("%H%M"),
    )
    if entry is not None:
        return ItineraryLookup(
            found=True,
            fuente="dia_anterior" if dia_anterior else "hoy",
            hora_utc=entry.hora_utc,
            aerodromo=entry.aerodromo,
        )

    return ItineraryLookup(found=False)


@router.get("/itinerary/uploads", response_model=list[ItineraryUploadOut])
def list_uploads(db: Session = Depends(get_db), _: ShiftLog = Depends(require_shift)):
    """Historial de actualizaciones del itinerario, la más reciente primero."""
    return db.execute(
        select(Importacion)
        .where(Importacion.tipo == TipoImportacion.ITINERARIO)
        .order_by(Importacion.cargado_en.desc())
        .limit(50)
    ).scalars().all()


def _validar_estacion(db: Session, estacion: str) -> str:
    """La estación tiene que estar en el catálogo y activa.

    Se rechaza en vez de aceptar cualquier texto porque `estacion` decide qué
    filas borra la carga: un código mal escrito no daría error, cargaría el
    itinerario en un aeródromo fantasma que después nadie encuentra."""
    codigo = estacion.strip().upper()
    if not itinerario.existe(db, codigo):
        raise HTTPException(
            status_code=404,
            detail=(
                f"La estación {codigo!r} no está en el catálogo o está inactiva. "
                "Las estaciones se administran en la tabla `estaciones`."
            ),
        )
    return codigo


def _parse_itinerary(
    content: bytes,
    filename: str | None,
    effective_from: date,
    alcance: str,
    db: Session,
    hoja: str | None = None,
    estacion: str = itinerario.ESTACION_POR_DEFECTO,
) -> tuple[ImportResult, str]:
    """Parsea el archivo con el formato que corresponda y devuelve
    (resultado, formato). Consulta el catalogo IATA/OACI, sin escribir datos.

    Lo usan por igual la vista previa y la carga real, para que lo que el
    operador revisa sea exactamente lo que después se aplica."""
    if (filename or "").lower().endswith(".csv"):
        result = parse_itinerary_csv(content)
        for row in result.rows:
            row.flight_date = effective_from
        return result, "csv"

    formato = detect_itinerary_format(content)
    if formato == "season":
        # Del maestro único. Antes salía de `catalogos.codigo_aeropuerto`, que
        # era una de las tres tablas que guardaban el mismo hecho.
        iata_to_icao = {
            iata: oaci
            for iata, oaci in db.execute(
                select(Aerodromo.codigo_iata, Aerodromo.codigo_oaci).where(
                    Aerodromo.codigo_iata.is_not(None),
                    Aerodromo.codigo_oaci.is_not(None),
                    Aerodromo.activo.is_(True),
                )
            ).all()
        }
        result = parse_itinerary_season_excel(
            content, iata_to_icao, min_date=effective_from, hoja=hoja, estacion=estacion
        )
        if alcance == "dia":
            # El archivo de temporada trae muchos días; con alcance "dia" se
            # toma solo el elegido. No son errores: simplemente no entran.
            result.rows = [r for r in result.rows if r.flight_date == effective_from]
        return result, "season"

    result = parse_itinerary_excel(content)
    for row in result.rows:
        row.flight_date = effective_from
    return result, "legacy"


def _advertencias(
    formato: str,
    effective_from: date,
    alcance: str,
    rows: list[ItineraryRow],
    arribos: int,
    salidas: int,
    filas_reemplazadas: int,
    dias_reemplazados: int,
    desaparecidos: list[str],
) -> list[str]:
    """Avisos de lo que suele salir mal al cargar un itinerario. No bloquean
    nada: el operador decide igual, pero con el problema a la vista."""
    tramo = (
        f"el día {effective_from}" if alcance == "dia" else f"del {effective_from} en adelante"
    )
    avisos: list[str] = []

    if not rows:
        avisos.append(
            "No se pudo leer ninguna fila válida. Aplicar esta carga dejaría el "
            f"itinerario VACÍO para {tramo}."
        )
        return avisos

    if formato != "season":
        avisos.append(
            f"El archivo no trae fecha propia (formato de carga diaria), así que las "
            f"{len(rows)} filas se van a guardar TODAS con la fecha {effective_from}. "
            "Confirmá que el archivo sea realmente de ese día."
        )
    if arribos == 0:
        avisos.append(
            "El archivo no trae ninguna llegada (ARR). Revisá que sea un itinerario "
            "y no una planilla de salidas o una grilla ya operada."
        )
    if salidas == 0:
        avisos.append("El archivo no trae ninguna salida (DEP).")
    if all(not r.tipo_aeronave for r in rows):
        avisos.append(
            "Ninguna fila trae TIPO DE AERONAVE. Suele ser señal de que el archivo "
            "no tiene el formato de itinerario esperado."
        )
    if filas_reemplazadas:
        avisos.append(
            f"Se borran {filas_reemplazadas} filas de itinerario ya cargadas "
            f"({dias_reemplazados} día(s), {tramo}) y se reemplazan por las de "
            "este archivo."
        )
    if desaparecidos:
        avisos.append(
            f"{len(desaparecidos)} vuelo(s) de la grilla no figuran en este archivo. "
            "La carga NO los modifica: quedan como están. Si alguno no va a operar, "
            "hay que cancelarlo desde la grilla."
        )
    return avisos


@router.post("/itinerary/preview", response_model=ItineraryPreview)
async def preview_itinerary(
    effective_from: date = Query(..., description="Fecha desde la que regiría la carga"),
    alcance: Literal["dia", "desde"] = Query(
        "desde",
        description='"dia" = solo esa fecha; "desde" = de esa fecha en adelante',
    ),
    estacion: str = Query(
        itinerario.ESTACION_POR_DEFECTO,
        description="Aeródromo al que pertenece este itinerario (OACI)",
    ),
    hoja: str | None = Query(
        None,
        description="Hoja del libro a leer; si no se indica se elige la que mejor cubre la fecha",
    ),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    operator: ShiftLog = Depends(require_writable_shift),
):
    """Vista previa: valida el archivo y calcula qué cambiaría, SIN tocar el
    itinerario ni la grilla.

    Existe porque la carga era irreversible de un solo paso: se soltaba el
    archivo y ya quedaba aplicado. Ahora el operador ve con qué formato se
    leyó, qué fechas cubre y qué itinerario ya cargado se reemplaza; recién
    entonces confirma con POST /itinerary/upload?confirmado=true.

    `alcance` tiene que ser el mismo que en la carga real: cambia por completo
    qué itinerario se reemplaza."""
    estacion = _validar_estacion(db, estacion)
    content = await read_upload_limited(file, MAX_UPLOAD_BYTES)

    try:
        result, formato = _parse_itinerary(
            content, file.filename, effective_from, alcance, db, hoja=hoja, estacion=estacion
        )
    except InvalidItineraryFile as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    por_fecha: dict[date, list[int]] = {}
    for row in result.rows:
        acc = por_fecha.setdefault(row.flight_date, [0, 0])
        acc[0 if row.direction == "ARR" else 1] += 1

    old_pairs = itinerario.pares_cargados(db, estacion, alcance, effective_from)
    new_pairs = {(r.flight_date, r.call_sign) for r in result.rows}
    faltantes = old_pairs - new_pairs

    # Vuelos de la grilla que quedan sin itinerario. Se informan y nada más:
    # esta carga no los modifica. Los que el operador ya canceló a mano no se
    # listan -- no hay nada que revisar ahí.
    desaparecidos: list[str] = [
        f"{flight.flight_date} {flight.vuelo}"
        for flight in db.execute(
            select(Flight).where(
                itinerario.filtro_vuelos_del_tramo(estacion, alcance, effective_from)
            )
        ).scalars().all()
        if (flight.flight_date, flight.vuelo) in faltantes and not flight.cancelado
    ]

    filas_reemplazadas = db.execute(
        select(func.count()).select_from(ItineraryEntry).where(
            itinerario.filtro_tramo(estacion, alcance, effective_from)
        )
    ).scalar() or 0
    dias_reemplazados = len({fd for fd, _ in old_pairs})

    arribos = sum(v[0] for v in por_fecha.values())
    salidas = sum(v[1] for v in por_fecha.values())

    return ItineraryPreview(
        estacion=estacion,
        formato=formato,
        hoja=result.hoja,
        hojas=[
            ItinerarySheet(
                nombre=h.nombre,
                filas=h.filas,
                fecha_min=h.fecha_min,
                fecha_max=h.fecha_max,
                elegida=h.nombre == result.hoja,
            )
            for h in result.hojas
        ],
        alcance=alcance,
        filas_aceptadas=result.filas_aceptadas,
        filas_rechazadas=result.filas_rechazadas,
        errores=[ItineraryRowError(row=r, motivo=m) for r, m in result.errores],
        fecha_min=min(por_fecha) if por_fecha else None,
        fecha_max=max(por_fecha) if por_fecha else None,
        por_fecha=[
            ItineraryPreviewDay(fecha=f, arribos=v[0], salidas=v[1])
            for f, v in sorted(por_fecha.items())
        ],
        filas_reemplazadas=filas_reemplazadas,
        dias_reemplazados=dias_reemplazados,
        # Se acotan a 100 porque con la temporada entera pueden ser cientos; el
        # total va en las advertencias.
        vuelos_sin_itinerario=sorted(desaparecidos)[:100],
        advertencias=_advertencias(
            formato, effective_from, alcance, result.rows,
            arribos, salidas, filas_reemplazadas, dias_reemplazados, desaparecidos,
        ),
    )


@router.post("/itinerary/upload", response_model=ItineraryUploadReport)
async def upload_itinerary(
    effective_from: date = Query(..., description="Fecha desde la que rige la carga"),
    alcance: Literal["dia", "desde"] = Query(
        "desde",
        description='"dia" = reemplaza solo esa fecha; "desde" = de esa fecha en adelante',
    ),
    estacion: str = Query(
        itinerario.ESTACION_POR_DEFECTO,
        description="Aeródromo al que pertenece este itinerario (OACI)",
    ),
    confirmado: bool = Query(
        False,
        description="Debe ser true: la carga solo se aplica después de revisar la vista previa",
    ),
    hoja: str | None = Query(
        None,
        description="La misma hoja que se revisó en la vista previa",
    ),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    operator: ShiftLog = Depends(require_writable_shift),
):
    """Aplica la carga: reemplaza el itinerario del tramo que indique
    `alcance` -- solo `effective_from` ("dia", para subir un día suelto sin
    tocar los que siguen) o de esa fecha en adelante ("desde", la
    actualización de temporada de la DGAC).

    Reemplaza itinerario y nada más: ningún vuelo de la grilla se modifica,
    tampoco los que dejan de figurar en el archivo. Cancelar un vuelo es una
    decisión del operador FMP, no la consecuencia de que la DGAC lo omita.

    Es destructivo e irreversible, así que exige `confirmado=true` -- soltar
    un archivo no alcanza; hay que pasar antes por POST /itinerary/preview y
    confirmar lo que se vio."""
    if not confirmado:
        raise HTTPException(
            status_code=400,
            detail=(
                "Falta confirmar la carga. Revisá primero la vista previa y recién "
                "entonces aplicá el archivo."
            ),
        )

    # Mismo control que la vista previa: un código mal escrito no puede llegar
    # al DELETE de abajo, que borra por estación.
    estacion = _validar_estacion(db, estacion)
    content = await read_upload_limited(file, MAX_UPLOAD_BYTES)

    try:
        result, _formato = _parse_itinerary(
            content, file.filename, effective_from, alcance, db, hoja=hoja, estacion=estacion
        )
    except InvalidItineraryFile as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    db.execute(
        delete(ItineraryEntry).where(
            itinerario.filtro_tramo(estacion, alcance, effective_from)
        )
    )

    # Igual que en la carga historica: la ejecucion se registra primero y se
    # vuelca, para que cada movimiento pueda apuntar a la carga que lo creo.
    importacion = Importacion(
        tipo=TipoImportacion.ITINERARIO,
        estacion=estacion,
        vigente_desde=effective_from,
        alcance=alcance,
        cargado_en=clock.now_utc(),
        cargado_por=operator.operator_name,
        cargado_por_id=operator.operador_id,
        nombre_archivo=file.filename,
        filas_aceptadas=result.filas_aceptadas,
        filas_rechazadas=result.filas_rechazadas,
    )
    db.add(importacion)
    db.flush()

    for row in result.rows:
        db.add(
            ItineraryEntry(
                estacion=estacion,
                flight_date=row.flight_date,
                effective_from=effective_from,
                call_sign=row.call_sign,
                direction=row.direction,
                hora_utc=row.hora_utc,
                aerodromo=row.aerodromo,
                tipo_aeronave=row.tipo_aeronave,
                tipo_servicio=row.tipo_servicio,
                asientos=row.asientos,
                importacion_id=importacion.id,
                fila_origen=row.fila_origen,
            )
        )

    # Ningún vuelo de la grilla se toca acá, a propósito: el itinerario es la
    # programación de la DGAC y la grilla es la gestión del FMP. Que un vuelo
    # deje de figurar en el archivo no es una cancelación, y marcarlo como tal
    # ponía en la grilla una decisión que el operador no había tomado.

    db.commit()

    return ItineraryUploadReport(
        filas_aceptadas=result.filas_aceptadas,
        filas_rechazadas=result.filas_rechazadas,
        errores=[ItineraryRowError(row=r, motivo=m) for r, m in result.errores],
    )
