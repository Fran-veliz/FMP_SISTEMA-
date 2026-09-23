"""nómina en mayúsculas y perfiles DGAC1 / DGAC2

Decisión del área (2026-07-31): los usuarios se escriben en mayúsculas
(SALCANTARA, MROMERO…), igual que se los nombra en la operación, y los
perfiles de consulta pasan a llamarse DGAC1 y DGAC2.

El alcance de cada perfil no cambia: DGAC1 sigue siendo el que consulta y
descarga el día seleccionado; DGAC2 solo consulta el año en curso.

El login compara el nombre sin distinguir mayúsculas ni espacios (ver
routers/shifts.py::clock_in), así que quien ya venía entrando como
"salcantara" sigue entrando igual — no hay que repartir usuarios nuevos.

Revision ID: b9e4f2a07c15
Revises: a7c2e5d81f3b
Create Date: 2026-07-31 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op


revision: str = 'b9e4f2a07c15'
down_revision: Union[str, Sequence[str], None] = 'a7c2e5d81f3b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Perfiles de consulta primero: su nombre nuevo no es solo la versión en
    # mayúsculas del anterior.
    op.execute("UPDATE controllers SET name = 'DGAC1' WHERE lower(name) IN ('dgac-principal', 'dgac')")
    op.execute("UPDATE controllers SET name = 'DGAC2' WHERE lower(name) IN ('dgac-secundario', 'dgac2')")
    op.execute("UPDATE shift_logs SET operator_name = 'DGAC1' WHERE lower(operator_name) IN ('dgac-principal', 'dgac')")
    op.execute("UPDATE shift_logs SET operator_name = 'DGAC2' WHERE lower(operator_name) IN ('dgac-secundario', 'dgac2')")

    # Resto de la nómina: a mayúsculas. Los nombres de operador viven como
    # texto en varias tablas (a propósito: el histórico sobrevive a las bajas
    # de la nómina), así que se actualizan todas para no terminar con dos
    # grafías de la misma persona en la bitácora y el historial.
    op.execute("UPDATE controllers SET name = upper(name) WHERE name <> upper(name)")
    for tabla, columna in (
        ("shift_logs", "operator_name"),
        ("flight_history", "operator_name"),
        ("flights", "updated_by"),
        ("flight_deletion_log", "deleted_by"),
        ("itinerary_uploads", "uploaded_by"),
        ("flight_history_imports", "uploaded_by"),
    ):
        # "IMPORT HISTÓRICO" ya está en mayúsculas, así que la condición lo
        # deja fuera sola.
        op.execute(f"UPDATE {tabla} SET {columna} = upper({columna}) WHERE {columna} <> upper({columna})")


def downgrade() -> None:
    # Los nombres previos eran minúsculas para la nómina y con guion para la
    # DGAC; se reconstruyen así.
    op.execute("UPDATE controllers SET name = 'dgac-principal' WHERE name = 'DGAC1'")
    op.execute("UPDATE controllers SET name = 'dgac-secundario' WHERE name = 'DGAC2'")
    op.execute("UPDATE shift_logs SET operator_name = 'dgac-principal' WHERE operator_name = 'DGAC1'")
    op.execute("UPDATE shift_logs SET operator_name = 'dgac-secundario' WHERE operator_name = 'DGAC2'")
    op.execute("UPDATE controllers SET name = lower(name) WHERE name NOT IN ('dgac-principal', 'dgac-secundario')")
