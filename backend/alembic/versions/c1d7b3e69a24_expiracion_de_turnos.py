"""expiración de turnos por inactividad

Hasta ahora el token de sesión valía hasta que alguien cerrara turno
explícitamente. Si un operador se iba sin cerrar -- se cortó la luz, cerró el
navegador, se llevó la laptop -- ese token quedaba válido **para siempre**, y
además guardado en el localStorage de esa computadora.

Se agrega `last_seen_at`: la última vez que el turno se usó para autorizar
algo. `require_shift` rechaza los turnos que llevan demasiado tiempo sin
actividad (ver app/auth.py::SHIFT_IDLE_TIMEOUT_HOURS).

Los turnos ya abiertos arrancan con `last_seen_at = start_time`, que es lo
más honesto que se puede afirmar de ellos.

Revision ID: c1d7b3e69a24
Revises: b9e4f2a07c15
Create Date: 2026-07-31 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c1d7b3e69a24'
down_revision: Union[str, Sequence[str], None] = 'b9e4f2a07c15'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("shift_logs", sa.Column("last_seen_at", sa.DateTime(), nullable=True))
    op.execute("UPDATE shift_logs SET last_seen_at = start_time WHERE last_seen_at IS NULL")


def downgrade() -> None:
    op.drop_column("shift_logs", "last_seen_at")
