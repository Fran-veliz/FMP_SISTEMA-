"""renombra los perfiles DGAC y ajusta su alcance

Decisión del área (2026-07-31, revisando la primera versión):

  - Los nombres pasan a decir qué es cada uno: `dgac` -> `dgac-principal`,
    `dgac2` -> `dgac-secundario`.
  - El principal descarga **el día que tenga seleccionado**, sin ventana de
    semanas: se quita `export_max_weeks`.
  - La restricción de año queda solo en el secundario, que consulta el año
    en curso y no descarga.

Se renombra en vez de borrar y recrear para no perder el PIN ya repartido ni
el rastro de los turnos que esos perfiles hayan abierto.

Revision ID: a7c2e5d81f3b
Revises: f4b8d0e6a713
Create Date: 2026-07-31 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op


revision: str = 'a7c2e5d81f3b'
down_revision: Union[str, Sequence[str], None] = 'f4b8d0e6a713'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Principal: sin ventana de semanas y sin límite de año.
    op.execute("""
        UPDATE controllers
           SET name = 'dgac-principal',
               export_max_weeks = NULL,
               view_year_only = false
         WHERE name = 'dgac'
    """)
    # Secundario: conserva el año en curso y sigue sin descargar.
    op.execute("""
        UPDATE controllers
           SET name = 'dgac-secundario',
               can_export = false,
               view_year_only = true
         WHERE name = 'dgac2'
    """)
    # Los turnos guardan el nombre como texto (a propósito, para que el
    # histórico sobreviva a bajas de la nómina): se renombran también, si no
    # la bitácora mostraría dos identidades para la misma persona.
    op.execute("UPDATE shift_logs SET operator_name = 'dgac-principal' WHERE operator_name = 'dgac'")
    op.execute("UPDATE shift_logs SET operator_name = 'dgac-secundario' WHERE operator_name = 'dgac2'")


def downgrade() -> None:
    op.execute("UPDATE shift_logs SET operator_name = 'dgac2' WHERE operator_name = 'dgac-secundario'")
    op.execute("UPDATE shift_logs SET operator_name = 'dgac' WHERE operator_name = 'dgac-principal'")
    op.execute("UPDATE controllers SET name = 'dgac2' WHERE name = 'dgac-secundario'")
    op.execute("""
        UPDATE controllers
           SET name = 'dgac', export_max_weeks = 4, view_year_only = true
         WHERE name = 'dgac-principal'
    """)
