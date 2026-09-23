"""baseline schema

Revision ID: ff29dcbcea20
Revises: 
Create Date: 2026-07-06 13:42:03.028742

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'ff29dcbcea20'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_unique_constraint('uq_flight_numero', 'flights', ['sector', 'flight_date', 'numero'])
    op.create_index('ix_itinerary_lookup', 'itinerary_entries', ['flight_date', 'call_sign', 'direction'], unique=False)
    op.add_column('shift_logs', sa.Column('session_token', sa.String(length=64), nullable=True))
    op.create_unique_constraint('uq_shift_logs_session_token', 'shift_logs', ['session_token'])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint('uq_shift_logs_session_token', 'shift_logs', type_='unique')
    op.drop_column('shift_logs', 'session_token')
    op.drop_index('ix_itinerary_lookup', table_name='itinerary_entries')
    op.drop_constraint('uq_flight_numero', 'flights', type_='unique')
