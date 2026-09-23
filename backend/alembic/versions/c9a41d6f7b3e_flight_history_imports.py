"""importación de vuelos históricos (grilla FMP de años anteriores)

Revision ID: c9a41d6f7b3e
Revises: b7d2e4f81a09
Create Date: 2026-07-22 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'c9a41d6f7b3e'
down_revision: Union[str, Sequence[str], None] = 'b7d2e4f81a09'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'flight_history_imports',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('sector', postgresql.ENUM('SUR', 'NOR', name='sector', create_type=False), nullable=False),
        sa.Column('flight_date', sa.Date(), nullable=False),
        sa.Column('uploaded_at', sa.DateTime(), nullable=False),
        sa.Column('uploaded_by', sa.String(length=64), nullable=True),
        sa.Column('filename', sa.String(length=200), nullable=True),
        sa.Column('filas_aceptadas', sa.Integer(), nullable=False),
        sa.Column('filas_rechazadas', sa.Integer(), nullable=False),
    )
    op.create_index('ix_flight_history_imports_sector', 'flight_history_imports', ['sector'])
    op.create_index('ix_flight_history_imports_flight_date', 'flight_history_imports', ['flight_date'])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_flight_history_imports_flight_date', table_name='flight_history_imports')
    op.drop_index('ix_flight_history_imports_sector', table_name='flight_history_imports')
    op.drop_table('flight_history_imports')
