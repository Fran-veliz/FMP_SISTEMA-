"""perfil de solo lectura (DGAC)

Revision ID: b7d2e4f81a09
Revises: a1c3f9e7b2d4
Create Date: 2026-07-21 15:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b7d2e4f81a09'
down_revision: Union[str, Sequence[str], None] = 'a1c3f9e7b2d4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('controllers', sa.Column('read_only', sa.Boolean(), nullable=False, server_default=sa.false()))
    op.alter_column('controllers', 'read_only', server_default=None)
    op.add_column('shift_logs', sa.Column('read_only', sa.Boolean(), nullable=False, server_default=sa.false()))
    op.alter_column('shift_logs', 'read_only', server_default=None)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('shift_logs', 'read_only')
    op.drop_column('controllers', 'read_only')
