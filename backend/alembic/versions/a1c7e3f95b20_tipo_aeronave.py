"""tipo_aeronave: equivalencia IATA -> OACI de los tipos de aeronave

Los itinerarios traen el equipo en IATA (`738`, `32N`) y el sistema trabaja
con el designador OACI (`B738`, `A20N`). Hasta ahora esa conversión no tenía
dónde vivir.

La tabla se crea vacía y los datos se cargan directamente en la base: el
código no trae una lista inicial.

Revision ID: a1c7e3f95b20
Revises: f9d2b45e08a7
Create Date: 2026-09-30 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a1c7e3f95b20"
down_revision: Union[str, Sequence[str], None] = "f9d2b45e08a7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "tipo_aeronave",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("codigo_iata", sa.String(length=3), nullable=False),
        sa.Column("codigo_oaci", sa.String(length=4), nullable=False),
        sa.Column("activo", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_tipo_aeronave"),
        sa.UniqueConstraint("codigo_iata", name="uq_tipo_aeronave_codigo_iata"),
    )
    op.create_index("ix_tipo_aeronave_codigo_oaci", "tipo_aeronave", ["codigo_oaci"])


def downgrade() -> None:
    op.drop_index("ix_tipo_aeronave_codigo_oaci", table_name="tipo_aeronave")
    op.drop_table("tipo_aeronave")
