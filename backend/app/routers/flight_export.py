"""Exportacion CSV de la grilla con permisos y proteccion de celdas."""
import csv
import io
from datetime import date

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import require_export_allowed, require_shift
from app.database import get_db
from app.models import Flight, Sector, ShiftLog

router = APIRouter()

# Caracteres que Excel/Sheets interpretan como inicio de fórmula si son el
# primer carácter de una celda -- se neutralizan al exportar CSV para que un
# valor tipeado por un operador FMP no pueda ejecutarse como fórmula/comando
# en la planilla de quien abre el archivo (CSV/Formula Injection, OWASP).
_FORMULA_TRIGGER_CHARS = ("=", "+", "-", "@")


def _csv_safe(value: str) -> str:
    return f"'{value}" if value and value[0] in _FORMULA_TRIGGER_CHARS else value

EXPORT_COLUMNS = [
    ("numero_fila", "N°"), ("sector", "SECTOR"), ("hora", "HORA"), ("d_ats", "D. ATS"),
    ("vuelo", "VUELO"), ("adep", "ADEP"), ("dep", "DEP"),
    ("eta_aircon", "ETA AIRCON"), ("eta_aircon_calculado", "ETA AIRCON CALCULADO"),
    ("slot_arr_dgac", "SLOT ARR DGAC"),
    ("etd1", "ETD 1"), ("ctot1", "CTOT 1"), ("sec1", "SEC1"), ("rev1", "REV1"),
    ("etd2", "ETD 2"), ("ctot2", "CTOT 2"), ("sec2", "SEC2"), ("rev2", "REV2"),
    ("etd3", "ETD 3"), ("ctot3", "CTOT 3"), ("sec3", "SEC3"), ("rev3", "REV3"),
    ("etd4", "ETD 4"), ("ctot4", "CTOT 4"), ("sec4", "SEC4"), ("rev4", "REV4"),
    ("etd5", "ETD 5"), ("ctot5", "CTOT 5"), ("sec5", "SEC5"),
    ("dla_minutos", "DLA (min)"), ("observaciones", "OBSERVACIONES"), ("cancelado", "CANCELADO"),
]

@router.get("/flights/export")
def export_flights(
    flight_date: date,
    sector: Sector | None = None,
    db: Session = Depends(get_db),
    shift: ShiftLog = Depends(require_shift),
):
    """Exporta la grilla del día en CSV (columnas de FormatoFMP) para subir
    a la plataforma obligatoria (ej. Ciro). Sin sector, exporta SUR+NOR.

    Los perfiles de la DGAC tienen alcance acotado: uno descarga solo las
    últimas semanas, el otro no descarga nada (ver require_export_allowed)."""
    require_export_allowed(shift, flight_date)
    stmt = select(Flight).where(Flight.flight_date == flight_date)
    if sector is not None:
        stmt = stmt.where(Flight.sector == sector)
    stmt = stmt.order_by(Flight.sector, Flight.numero_fila)
    flights = db.execute(stmt).scalars().all()

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow([label for _, label in EXPORT_COLUMNS])
    for flight in flights:
        row = []
        for key, _ in EXPORT_COLUMNS:
            value = getattr(flight, key)
            if key == "sector":
                value = value.value
            elif key == "cancelado":
                value = "SI" if value else ""
            if isinstance(value, str):
                value = _csv_safe(value)
            row.append(value if value is not None else "")
        writer.writerow(row)

    buffer.seek(0)
    filename = f"ctot_{flight_date}{'_' + sector.value if sector else ''}.csv"
    return StreamingResponse(
        iter([buffer.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
