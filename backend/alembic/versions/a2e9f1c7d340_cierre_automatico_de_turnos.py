"""marca de cierre automatico de turnos

Revision ID: a2e9f1c7d340
Revises: f7c3d8e2a951
Create Date: 2026-09-04 23:30:00.000000

El vencimiento por inactividad dejaba de aceptar el pase a las 12 h, pero NUNCA
cerraba el turno: end_time seguia en NULL para siempre y la bitacora mostraba a
esa persona trabajando indefinidamente. Cada olvido sumaba uno mas.

Esta columna distingue el cierre automatico del que hace el operador a mano.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a2e9f1c7d340"
down_revision: Union[str, Sequence[str], None] = "f7c3d8e2a951"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "shift_logs",
        sa.Column("cerrado_automaticamente", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("shift_logs", "cerrado_automaticamente")
