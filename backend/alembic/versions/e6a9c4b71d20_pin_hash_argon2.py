"""amplía pin_hash para almacenar hashes Argon2id

Revision ID: e6a9c4b71d20
Revises: d8a3c7b21e94
Create Date: 2026-09-03 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e6a9c4b71d20"
down_revision: Union[str, Sequence[str], None] = "d8a3c7b21e94"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        "controllers",
        "pin_hash",
        existing_type=sa.String(length=64),
        type_=sa.String(length=255),
        existing_nullable=False,
    )


def downgrade() -> None:
    # No se puede volver a 64 mientras existan hashes Argon2id. El downgrade
    # deliberadamente no trunca credenciales ni deja usuarios sin acceso.
    pass
