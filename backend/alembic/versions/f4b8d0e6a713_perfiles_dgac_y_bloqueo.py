"""perfiles DGAC diferenciados, límites de consulta y bloqueo por intentos

Hasta ahora había un único perfil de solo lectura ("DGAC") sin más límites
que no poder escribir. El área pidió dos perfiles distintos para la DGAC,
cada uno con su propio alcance, y protección contra fuerza bruta del PIN:

  - can_export        : si puede descargar el CSV de la grilla.
  - export_max_weeks  : cuántas semanas hacia atrás puede descargar
                        (NULL = sin límite).
  - view_year_only    : si solo puede consultar el año en curso.
  - failed_attempts   : intentos fallidos consecutivos de PIN.
  - locked_until      : hasta cuándo queda bloqueado el perfil (UTC).

Los tres primeros se copian al turno (shift_logs) al hacer clock-in, igual
que ya se hacía con read_only: así la autorización de cada request se
resuelve con el turno y no depende de que la nómina no haya cambiado en el
medio.

Revision ID: f4b8d0e6a713
Revises: e3f7a91c25b8
Create Date: 2026-07-31 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'f4b8d0e6a713'
down_revision: Union[str, Sequence[str], None] = 'e3f7a91c25b8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- nómina ---
    op.add_column("controllers", sa.Column("can_export", sa.Boolean(), nullable=False, server_default=sa.true()))
    op.add_column("controllers", sa.Column("export_max_weeks", sa.Integer(), nullable=True))
    op.add_column("controllers", sa.Column("view_year_only", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("controllers", sa.Column("failed_attempts", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("controllers", sa.Column("locked_until", sa.DateTime(), nullable=True))

    # --- turno (copia del perfil al abrir sesión) ---
    op.add_column("shift_logs", sa.Column("can_export", sa.Boolean(), nullable=False, server_default=sa.true()))
    op.add_column("shift_logs", sa.Column("export_max_weeks", sa.Integer(), nullable=True))
    op.add_column("shift_logs", sa.Column("view_year_only", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    for col in ("view_year_only", "export_max_weeks", "can_export"):
        op.drop_column("shift_logs", col)
    for col in ("locked_until", "failed_attempts", "view_year_only", "export_max_weeks", "can_export"):
        op.drop_column("controllers", col)
