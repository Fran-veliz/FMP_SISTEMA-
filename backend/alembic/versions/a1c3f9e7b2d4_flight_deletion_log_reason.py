"""flight_deletion_log deletion_reason

Revision ID: a1c3f9e7b2d4
Revises: ff29dcbcea20
Create Date: 2026-07-21 07:45:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1c3f9e7b2d4'
down_revision: Union[str, Sequence[str], None] = 'ff29dcbcea20'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('flight_deletion_log', sa.Column('deletion_reason', sa.String(length=300), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('flight_deletion_log', 'deletion_reason')
