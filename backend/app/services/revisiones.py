"""Cierre de las filas de `ctot.vuelo_revision` antes de guardarlas.

Los niveles se escriben desde los atributos planos del vuelo (`etd1`, `ctot3`,
`rev2`...), que no saben quién está editando ni qué hay en el catálogo de
motivos. Eso se resuelve acá, en un solo lugar, justo antes del commit.
"""
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import clock
from app.models import Flight, ReasonCategory, ReasonCode


def _catalogo(db: Session) -> dict[tuple[str, str], int]:
    """(categoría, código normalizado) -> id del motivo.

    Incluye los inactivos: un motivo retirado sigue explicando los vuelos que
    ya lo tienen escrito, y la referencia debe poder apuntarle."""
    return {
        (categoria.value, codigo.strip().upper()): motivo_id
        for motivo_id, categoria, codigo in db.execute(
            select(ReasonCode.id, ReasonCode.category, ReasonCode.code)
        ).all()
        if codigo
    }


def sellar(db: Session, flight: Flight, operador_id: int | None) -> None:
    """Completa autoría y referencias al catálogo en los niveles del vuelo.

    - **Autoría:** un nivel se sella una sola vez, cuando se crea. Corregir
      una digitación después no cambia quién abrió esa revisión; ese cambio
      queda en el historial del vuelo, que es donde corresponde.
    - **Motivos:** se resuelve la referencia por código normalizado. Si el
      código no está en el catálogo, la referencia queda nula y el texto se
      conserva: hay códigos históricos que nunca se dieron de alta, y
      apuntarlos al motivo "más parecido" cambiaría el motivo aeronáutico que
      alguien registró.
    """
    catalogo = _catalogo(db)
    ahora = clock.now_utc()

    for revision in flight.revisiones:
        if revision.creado_en is None:
            revision.creado_en = ahora
            revision.creado_por_id = operador_id

        revision.motivo_secuencia_id = (
            catalogo.get((ReasonCategory.SEC.value, revision.motivo_secuencia.strip().upper()))
            if revision.motivo_secuencia
            else None
        )
        revision.motivo_revision_id = (
            catalogo.get((ReasonCategory.REV.value, revision.motivo_revision.strip().upper()))
            if revision.motivo_revision
            else None
        )
