"""renombra ctot.vuelo_gestionado.numero a numero_fila

`numero` es el correlativo de la fila DENTRO de la grilla de un sector y un
día: se asigna como MAX+1 al crear el vuelo y la grilla ordena por él. No es
el número del vuelo, no identifica al vuelo y no es comparable entre días ni
entre sectores.

El nombre viejo estaba al lado de la columna `vuelo`, así que cualquiera que
leyera la tabla asumía que era el número del vuelo. Es el único cambio de esta
migración: el significado y los valores son los mismos.

Se renombra también la restricción de unicidad que lo usa, de
`uq_vuelo_gestionado_numero` a `uq_vuelo_gestionado_numero_fila`, para que el
nombre siga describiendo a qué columna protege. Es la restricción que impide
dos filas con el mismo correlativo cuando dos POST /flights concurrentes leen
el mismo MAX antes de que el primero confirme.

Las dos operaciones son de catálogo: un renombre de columna en PostgreSQL no
reescribe la tabla ni recorre las filas, y renombrar una restricción tampoco
la revalida.

Es defensiva por el mismo motivo que f1a6c30b7d28: la base de producción
divergió del modelo en algún momento, así que se comprueba el estado real
antes de tocar. Si la columna ya se llama `numero_fila` no hace nada.

Revision ID: a3d7e1b09c46
Revises: f1a6c30b7d28
Create Date: 2026-09-21 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a3d7e1b09c46"
down_revision: Union[str, Sequence[str], None] = "f1a6c30b7d28"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _columnas(esquema: str, tabla: str) -> set[str]:
    filas = op.get_bind().execute(
        sa.text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = :e AND table_name = :t"
        ),
        {"e": esquema, "t": tabla},
    )
    return {fila[0] for fila in filas}


def _hay_restriccion(esquema: str, tabla: str, nombre: str) -> bool:
    return bool(
        op.get_bind().execute(
            sa.text(
                "SELECT 1 FROM information_schema.table_constraints "
                "WHERE table_schema = :e AND table_name = :t AND constraint_name = :c"
            ),
            {"e": esquema, "t": tabla, "c": nombre},
        ).first()
    )


def upgrade() -> None:
    columnas = _columnas("ctot", "vuelo_gestionado")
    if "numero" in columnas and "numero_fila" not in columnas:
        op.alter_column(
            "vuelo_gestionado", "numero", new_column_name="numero_fila", schema="ctot"
        )

    if _hay_restriccion("ctot", "vuelo_gestionado", "uq_vuelo_gestionado_numero"):
        op.execute(
            "ALTER TABLE ctot.vuelo_gestionado "
            "RENAME CONSTRAINT uq_vuelo_gestionado_numero "
            "TO uq_vuelo_gestionado_numero_fila"
        )


def downgrade() -> None:
    columnas = _columnas("ctot", "vuelo_gestionado")
    if "numero_fila" in columnas and "numero" not in columnas:
        op.alter_column(
            "vuelo_gestionado", "numero_fila", new_column_name="numero", schema="ctot"
        )

    if _hay_restriccion("ctot", "vuelo_gestionado", "uq_vuelo_gestionado_numero_fila"):
        op.execute(
            "ALTER TABLE ctot.vuelo_gestionado "
            "RENAME CONSTRAINT uq_vuelo_gestionado_numero_fila "
            "TO uq_vuelo_gestionado_numero"
        )
