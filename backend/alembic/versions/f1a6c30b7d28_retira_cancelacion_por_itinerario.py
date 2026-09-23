"""retira la cancelación automática de vuelos por itinerario

La carga de itinerario marcaba CANCELADO al vuelo de la grilla que dejaba de
figurar en el archivo de la DGAC, y traía un tilde para desactivar esa regla
por carga ("conviene destildarlo en una carga diaria: el archivo no pretende
cubrir esos vuelos").

Se retira la regla completa. Cancelar un vuelo es una decisión del operador
FMP; que un archivo de programación lo omita no es esa decisión, y aplicarla
automáticamente escribía en la grilla algo que nadie había resuelto. Además,
la mitad del tiempo el tilde había que desactivarlo, lo que ya decía que la
regla no correspondía. El itinerario sigue reemplazándose igual; simplemente
no toca `ctot.vuelo_gestionado`.

Se van tres columnas:

- `cargas.lote_carga.cancelar_faltantes` -- el tilde.
- `cargas.lote_carga.vuelos_cancelados` -- cuántos vuelos canceló esa carga.
- `ctot.vuelo_gestionado.cancelado_por_itinerario` -- la marca en el vuelo.

Sobre `cancelado_por_itinerario`: la declaraba el modelo desde c4e8b7a19d63,
pero NINGUNA ruta la escribía nunca. La cancelación ponía `cancelado = True` y
nada más, así que la columna estuvo siempre en false. Retirarla no pierde
información porque nunca tuvo ninguna.

Sobre `vuelos_cancelados`: sí pudo tener valores. Es el conteo de lo que cada
carga canceló, y al borrar la columna se pierde. Antes del upgrade se deja el
total en el log para que quede en el registro de la migración. Lo que NO se
toca es `ctot.vuelo_gestionado.cancelado`: los vuelos que ya quedaron marcados
siguen marcados. Fueron cancelaciones reales que los operadores vieron en la
grilla, y reescribirlas ahora sería inventar historia. Si alguna es incorrecta,
se corrige desde la grilla, vuelo por vuelo, con su registro en el historial.

El downgrade repone las tres columnas con su valor por omisión, pero no puede
reponer los conteos ni la regla: el código que la aplicaba ya no existe.

Las tres operaciones son de catálogo. `lote_carga` tiene una fila por carga de
itinerario y `vuelo_gestionado` no se recorre: quitar una columna en PostgreSQL
no reescribe la tabla.

Revision ID: f1a6c30b7d28
Revises: e1a5c92f3d64
Create Date: 2026-09-21 00:00:00.000000

"""
import logging
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f1a6c30b7d28"
down_revision: Union[str, Sequence[str], None] = "e1a5c92f3d64"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

logger = logging.getLogger("alembic.runtime.migration")


def _columnas(esquema: str, tabla: str) -> set[str]:
    """Se pregunta al catálogo y no al inspector de SQLAlchemy, que cachea lo
    que reflejó -- mismo criterio que c4e8b7a19d63."""
    filas = op.get_bind().execute(
        sa.text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = :e AND table_name = :t"
        ),
        {"e": esquema, "t": tabla},
    )
    return {fila[0] for fila in filas}


def upgrade() -> None:
    columnas_carga = _columnas("cargas", "lote_carga")

    # Deja constancia de lo que la columna contaba antes de borrarla. Una base
    # recién creada no tiene la columna y este bloque no corre.
    if "vuelos_cancelados" in columnas_carga:
        total, cargas = op.get_bind().execute(
            sa.text(
                "SELECT COALESCE(SUM(vuelos_cancelados), 0), "
                "COUNT(*) FILTER (WHERE vuelos_cancelados > 0) "
                "FROM cargas.lote_carga"
            )
        ).one()
        if total:
            logger.info(
                "Se retira cargas.lote_carga.vuelos_cancelados: %s vuelo(s) "
                "cancelados por %s carga(s) de itinerario. Los vuelos siguen "
                "marcados como CANCELADO en ctot.vuelo_gestionado.",
                total,
                cargas,
            )
        op.drop_column("lote_carga", "vuelos_cancelados", schema="cargas")

    if "cancelar_faltantes" in columnas_carga:
        op.drop_column("lote_carga", "cancelar_faltantes", schema="cargas")

    if "cancelado_por_itinerario" in _columnas("ctot", "vuelo_gestionado"):
        op.drop_column("vuelo_gestionado", "cancelado_por_itinerario", schema="ctot")


def downgrade() -> None:
    op.add_column(
        "vuelo_gestionado",
        sa.Column(
            "cancelado_por_itinerario",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        schema="ctot",
    )
    op.add_column(
        "lote_carga",
        sa.Column(
            "cancelar_faltantes",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
        schema="cargas",
    )
    op.add_column(
        "lote_carga",
        sa.Column(
            "vuelos_cancelados",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        schema="cargas",
    )
