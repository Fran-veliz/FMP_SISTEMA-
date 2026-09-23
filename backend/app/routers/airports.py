from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import require_shift, require_writable_shift
from app.database import get_db
from app.models import Aerodromo, ShiftLog
from app.schemas import AirportCodeOut

router = APIRouter(tags=["airports"])

# Administración de las equivalencias IATA/OACI que usa la carga de itinerario
# para traducir la columna ORIGEN/DESTINO del archivo de la DGAC.
#
# Los datos ya no están en `catalogos.codigo_aeropuerto` sino en el maestro
# `catalogos.aerodromo`, que unificó las tres tablas que guardaban el mismo
# hecho. El contrato de la API no cambió: sigue hablando de `iata`, `icao`,
# `name`, `city` y `country`, porque lo que se administra desde acá sigue
# siendo lo mismo.


def _como_codigo(aerodromo: Aerodromo) -> AirportCodeOut:
    return AirportCodeOut(
        iata=aerodromo.codigo_iata,
        icao=aerodromo.codigo_oaci,
        name=aerodromo.nombre_oficial,
        city=aerodromo.ciudad,
        country=aerodromo.pais,
    )


@router.get("/airports", response_model=list[AirportCodeOut])
def list_airports(db: Session = Depends(get_db), _: ShiftLog = Depends(require_shift)):
    """Los aeródromos que tienen equivalencia IATA. Los que solo tienen OACI
    no entran: esta lista existe para traducir la columna IATA del itinerario,
    y una fila sin IATA no traduce nada."""
    aerodromos = db.execute(
        select(Aerodromo)
        .where(Aerodromo.codigo_iata.is_not(None), Aerodromo.activo.is_(True))
        .order_by(Aerodromo.codigo_iata)
    ).scalars().all()
    return [_como_codigo(a) for a in aerodromos]


@router.put("/airports/{iata}", response_model=AirportCodeOut)
def upsert_airport(
    iata: str,
    payload: AirportCodeOut,
    db: Session = Depends(get_db),
    operator: ShiftLog = Depends(require_writable_shift),
):
    """Da de alta o corrige una equivalencia.

    Si el OACI que se manda ya existe en el maestro, se le agrega el IATA a esa
    misma fila en vez de crear una nueva: el aeródromo es uno y el maestro
    tiene que tenerlo una sola vez. Es justamente lo que las tres tablas
    separadas no podían garantizar."""
    iata = iata.upper()
    oaci = (payload.icao or "").strip().upper() or None

    aerodromo = db.execute(
        select(Aerodromo).where(Aerodromo.codigo_iata == iata)
    ).scalars().first()

    if aerodromo is None and oaci is not None:
        # Puede estar cargado por su OACI -- por ejemplo, una estación CTOT --
        # y lo que falta es la equivalencia.
        aerodromo = db.execute(
            select(Aerodromo).where(Aerodromo.codigo_oaci == oaci)
        ).scalars().first()

    if aerodromo is None:
        aerodromo = Aerodromo(codigo_iata=iata)
        db.add(aerodromo)
    else:
        aerodromo.codigo_iata = iata

    # El OACI es único en el maestro: reasignarlo a otro aeródromo dejaría dos
    # filas para el mismo lugar, que es el problema que se viene de resolver.
    if oaci is not None and aerodromo.codigo_oaci != oaci:
        chocado = db.execute(
            select(Aerodromo).where(Aerodromo.codigo_oaci == oaci)
        ).scalars().first()
        if chocado is not None and chocado.id != aerodromo.id:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"El código OACI {oaci} ya está cargado en otro aeródromo del "
                    f"catálogo ({chocado.codigo_iata or chocado.codigo_oaci}). "
                    "Corregí esa fila en vez de duplicar el aeródromo."
                ),
            )
        aerodromo.codigo_oaci = oaci

    aerodromo.nombre_oficial = payload.name
    aerodromo.ciudad = payload.city
    aerodromo.pais = payload.country
    db.commit()
    db.refresh(aerodromo)
    return _como_codigo(aerodromo)
