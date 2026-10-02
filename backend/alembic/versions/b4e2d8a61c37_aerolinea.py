"""aerolinea: catálogo de aerolíneas con su designador IATA y OACI

Los indicativos se guardan con el prefijo OACI, pero algunos itinerarios los
traen con el IATA. Hasta ahora no había dónde buscar la equivalencia.

La tabla se crea vacía y los datos se cargan directamente en la base: el
código no trae una lista inicial.

Revision ID: b4e2d8a61c37
Revises: a1c7e3f95b20
Create Date: 2026-09-30 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b4e2d8a61c37"
down_revision: Union[str, Sequence[str], None] = "a1c7e3f95b20"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "aerolinea",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("codigo_oaci", sa.String(length=3), nullable=False),
        sa.Column("codigo_iata", sa.String(length=2), nullable=True),
        sa.Column("activo", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_aerolinea"),
        sa.UniqueConstraint("codigo_oaci", name="uq_aerolinea_codigo_oaci"),
        sa.UniqueConstraint("codigo_iata", name="uq_aerolinea_codigo_iata"),
    )


def downgrade() -> None:
    op.drop_table("aerolinea")
