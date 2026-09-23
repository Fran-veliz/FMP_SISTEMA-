from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import require_shift
from app.database import get_db
from app.models import Aerodromo, CapacidadAerodromo, ShiftLog
from app.schemas import EstacionOut

router = APIRouter(tags=["estaciones"])


@router.get("/estaciones", response_model=list[EstacionOut])
def list_estaciones(db: Session = Depends(get_db), _: ShiftLog = Depends(require_shift)):
    """Aeródromos con itinerario propio, para elegir cuál se está cargando.

    Router aparte de `/airports` porque son dos cosas distintas que suenan
    igual: `/airports` es el mapeo IATA↔OACI de cualquier aeropuerto del mundo
    que aparezca como origen o destino de un vuelo (KMIA, SCEL), y esto es el
    puñado de aeródromos de los que la FMP recibe itinerario.

    Solo los activos. Es la misma condición que exige la carga
    (`_validar_estacion`), y ofrecer en la pantalla uno que el backend va a
    rechazar es prometer algo que no se puede cumplir.
    """
    # La capacidad ya no es una columna del aeródromo sino su declaración
    # vigente, así que se trae junta en vez de dejar el campo en nulo: un nulo
    # acá significaría "sin declarar", que es otra cosa.
    vigente = (
        select(CapacidadAerodromo.aerodromo_id, CapacidadAerodromo.valor)
        .where(CapacidadAerodromo.vigente_hasta.is_(None))
        .subquery()
    )
    filas = db.execute(
        select(Aerodromo, vigente.c.valor)
        .outerjoin(vigente, vigente.c.aerodromo_id == Aerodromo.id)
        .where(Aerodromo.es_estacion_ctot.is_(True), Aerodromo.activo.is_(True))
        .order_by(Aerodromo.codigo_oaci)
    ).all()

    return [
        EstacionOut(
            codigo_oaci=aerodromo.codigo_oaci,
            nombre=aerodromo.nombre_operativo or aerodromo.nombre_oficial or "",
            capacidad_declarada=capacidad,
        )
        for aerodromo, capacidad in filas
    ]
