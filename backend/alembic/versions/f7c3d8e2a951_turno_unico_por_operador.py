"""un solo turno abierto por operador

Revision ID: f7c3d8e2a951
Revises: e6a9c4b71d20
Create Date: 2026-09-04 20:00:00.000000

La regla vivía solo en el código: clock_in consultaba si el operador ya tenía
un turno abierto y después creaba el nuevo. Entre la consulta y la creación hay
un instante, y dos peticiones simultáneas -- doble clic, dos pestañas, un
reintento de red -- podían pasar las dos la consulta y crear dos turnos
abiertos para la misma persona, con dos tokens válidos.

El índice es PARCIAL: solo alcanza a los turnos sin cerrar. Los cerrados se
repiten tantas veces como jornadas trabaje cada uno.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f7c3d8e2a951"
down_revision: Union[str, Sequence[str], None] = "e6a9c4b71d20"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Se comprueba antes de crear: si ya hubiera duplicados, el índice fallaría
    # con un error críptico en pleno arranque del contenedor. Mejor decir qué
    # pasa y dejar que TI los resuelva a mano.
    conexion = op.get_bind()
    duplicados = conexion.execute(
        sa.text(
            "SELECT operator_name, count(*) AS n FROM shift_logs "
            "WHERE end_time IS NULL GROUP BY operator_name HAVING count(*) > 1"
        )
    ).fetchall()
    if duplicados:
        detalle = ", ".join(f"{fila[0]} ({fila[1]} turnos)" for fila in duplicados)
        raise RuntimeError(
            "Hay operadores con más de un turno abierto y no se puede crear el "
            f"índice hasta resolverlos: {detalle}. Cerrá los sobrantes "
            "(UPDATE shift_logs SET end_time = ... WHERE id = ...) y reintentá."
        )

    op.create_index(
        "uq_turno_abierto_por_operador",
        "shift_logs",
        ["operator_name"],
        unique=True,
        postgresql_where=sa.text("end_time IS NULL"),
        sqlite_where=sa.text("end_time IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_turno_abierto_por_operador", table_name="shift_logs")
