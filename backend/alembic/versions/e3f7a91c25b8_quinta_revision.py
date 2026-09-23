"""quinta revisión: nivel 5 de negociación del CTOT

Hasta ahora la grilla llegaba al Nivel 4 (cuatro rondas piloto/operador FMP,
con tres revisiones que las abrían: H.REV2..H.REV4). El área pidió una ronda
más, así que se agregan:

  - h_rev4 / rev4 : la llamada que abre el Nivel 5 (se etiquetan H.REV5/REV5)
  - etd5 / ctot5 / sec5 : la quinta ronda de negociación

Todas nulables: los vuelos existentes quedan sin datos en el nivel nuevo, y
la grilla solo muestra el Nivel 5 cuando alguien lo carga (igual que los
niveles 2-4, ver DECISIONES.md §2.6).

Revision ID: e3f7a91c25b8
Revises: d5b8c1a2f640
Create Date: 2026-07-31 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'e3f7a91c25b8'
down_revision: Union[str, Sequence[str], None] = 'd5b8c1a2f640'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


NEW_COLUMNS = [
    ("h_rev4", sa.String(length=4)),
    ("rev4", sa.String(length=16)),
    ("etd5", sa.String(length=4)),
    ("ctot5", sa.String(length=4)),
    ("sec5", sa.String(length=16)),
]


def upgrade() -> None:
    for name, type_ in NEW_COLUMNS:
        op.add_column("flights", sa.Column(name, type_, nullable=True))


def downgrade() -> None:
    for name, _ in reversed(NEW_COLUMNS):
        op.drop_column("flights", name)
