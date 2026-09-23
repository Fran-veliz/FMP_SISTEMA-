"""carga de itinerario por día y cancelación automática opcional

Revision ID: d8a3c7b21e94
Revises: c1d7b3e69a24
Create Date: 2026-08-31 00:00:00.000000

Hasta acá toda carga de itinerario reemplazaba "de la fecha en adelante" y
cancelaba sola los vuelos que dejaban de figurar. Eso sirve para la
actualización de temporada de la DGAC, pero no para subir un día suelto:
borraba los días posteriores ya cargados y cancelaba vuelos por un archivo
que nunca pretendió cubrirlos.

Se registra en el historial con qué alcance y con qué política de
cancelación se aplicó cada carga. Las cargas viejas quedan como 'desde' con
cancelación activada, que es lo que efectivamente hicieron.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd8a3c7b21e94'
down_revision: Union[str, Sequence[str], None] = 'c1d7b3e69a24'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'itinerary_uploads',
        sa.Column('alcance', sa.String(length=8), nullable=False, server_default='desde'),
    )
    op.add_column(
        'itinerary_uploads',
        sa.Column('cancelar_faltantes', sa.Boolean(), nullable=False, server_default=sa.true()),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('itinerary_uploads', 'cancelar_faltantes')
    op.drop_column('itinerary_uploads', 'alcance')
