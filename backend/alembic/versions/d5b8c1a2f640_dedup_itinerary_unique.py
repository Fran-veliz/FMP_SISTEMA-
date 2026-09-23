"""deduplica itinerario y agrega restricción de unicidad

Algunos Excel de origen (INFO) traían el bloque de itinerario pegado muchas
veces: 5 días de 2023 quedaron con el mismo vuelo repetido hasta 38 veces
(~59.000 filas de más sobre 225.128). Los vuelos (tabla flights) NO estaban
afectados, solo itinerary_entries. Esta migración:

  1. Borra las filas exactamente repetidas dejando una de cada una (la de menor
     id) por fecha+vigencia+vuelo+dirección+hora+aeródromo+aeronave+servicio.
  2. Crea el índice único uq_itinerary_dedup para que Postgres rechace de ahí
     en más cualquier duplicado (antes los aceptaba en silencio).

Revision ID: d5b8c1a2f640
Revises: c9a41d6f7b3e
Create Date: 2026-07-30 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'd5b8c1a2f640'
down_revision: Union[str, Sequence[str], None] = 'c9a41d6f7b3e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # 1) Deduplicar: conservar el id más bajo de cada grupo idéntico.
    #    PARTITION BY agrupa los NULL juntos, así que aeródromo/servicio vacío
    #    se deduplica bien.
    op.execute(
        """
        DELETE FROM itinerary_entries
        WHERE id IN (
            SELECT id FROM (
                SELECT id, ROW_NUMBER() OVER (
                    PARTITION BY flight_date, effective_from, call_sign, direction,
                                 hora_utc, aerodromo, tipo_aeronave, tipo_servicio
                    ORDER BY id
                ) AS rn
                FROM itinerary_entries
            ) t
            WHERE t.rn > 1
        )
        """
    )

    # 2) Restricción única. NULLS NOT DISTINCT (Postgres 15+) trata dos NULL
    #    como iguales para que un campo vacío no burle la unicidad.
    op.execute(
        """
        CREATE UNIQUE INDEX uq_itinerary_dedup ON itinerary_entries
            (flight_date, effective_from, call_sign, direction,
             hora_utc, aerodromo, tipo_aeronave, tipo_servicio)
            NULLS NOT DISTINCT
        """
    )


def downgrade() -> None:
    """Downgrade schema. (Las filas borradas no se recuperan.)"""
    op.drop_index("uq_itinerary_dedup", table_name="itinerary_entries")
