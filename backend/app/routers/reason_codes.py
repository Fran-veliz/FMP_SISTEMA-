from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import require_shift, require_writable_shift
from app.database import get_db
from app.models import ReasonCategory, ReasonCode, ShiftLog
from app.schemas import ReasonCodeCreate, ReasonCodeOut

router = APIRouter(tags=["reason_codes"])


@router.get("/reason-codes", response_model=list[ReasonCodeOut])
def list_reason_codes(
    category: ReasonCategory | None = None,
    incluir_inactivos: bool = False,
    db: Session = Depends(get_db),
    _: ShiftLog = Depends(require_shift),
):
    """Los motivos que la grilla ofrece al operador.

    Por omisión solo los activos: un motivo retirado no debe seguir
    apareciendo en la lista de opciones, pero tampoco se borra, porque los
    vuelos ya gestionados guardan su código y el catálogo tiene que poder
    explicarlo. `incluir_inactivos` es para administrar el catálogo."""
    stmt = select(ReasonCode).order_by(ReasonCode.category, ReasonCode.code)
    if category is not None:
        stmt = stmt.where(ReasonCode.category == category)
    if not incluir_inactivos:
        stmt = stmt.where(ReasonCode.activo.is_(True))
    return db.execute(stmt).scalars().all()


@router.post("/reason-codes", response_model=ReasonCodeOut)
def create_reason_code(
    payload: ReasonCodeCreate,
    db: Session = Depends(get_db),
    operator: ShiftLog = Depends(require_writable_shift),
):
    existing = db.execute(
        select(ReasonCode).where(
            ReasonCode.category == payload.category, ReasonCode.code == payload.code
        )
    ).scalars().first()
    if existing:
        # Si estaba retirado, volver a darlo de alta es reactivarlo: es lo que
        # el operador pidió. Sin esto el alta respondía 200 con una fila que
        # la lista seguía sin mostrar.
        if not existing.activo:
            existing.activo = True
            db.commit()
            db.refresh(existing)
        return existing

    reason = ReasonCode(category=payload.category, code=payload.code, description=payload.description)
    db.add(reason)
    db.commit()
    db.refresh(reason)
    return reason
