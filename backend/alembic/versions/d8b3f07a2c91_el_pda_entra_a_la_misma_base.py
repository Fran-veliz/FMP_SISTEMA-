"""el archivo del PDA entra a la misma base

Hasta ahora habia dos bases de datos en dos servidores distintos: `fmu_lima`
(antes `ctot`) con la gestion FMP, y `atfm_db` con el Plan Diario ATFM. El
Portal leia la programacion de CTOT abriendo una segunda conexion contra la
otra base, con un usuario de solo lectura.

Esa separacion costaba mas de lo que aportaba. El mismo aerodromo estaba
cargado dos veces sin forma de cruzarlo, la capacidad declarada se editaba por
separado en cada lado, y una consulta que quisiera contrastar la demanda
publicada del PDA con la programacion real no se podia escribir: son dos
servidores.

Esta migracion crea las seis tablas del PDA aca. **Solo la estructura**: las
filas se copian aparte, porque estan en otro servidor y ninguna migracion
puede alcanzarlas.

**`aerodromos` del Portal NO se crea.** Es el mismo hecho que `aerodromo`, y
duplicarlo dentro de la misma base seria repetir el problema que se acaba de
resolver -- solo que ahora sin la excusa de que estaban separadas. Sus 32
filas se funden en el maestro: 23 enriquecen aerodromos que ya existian con la
geografia que a CTOT le faltaba (region, coordenadas, elevacion, tipo) y 9
entran nuevos. El Portal pasa a leer `aerodromo`.

**Las claves foraneas contra el maestro se declaran ahora.** En `atfm_db` no
se podian: el catalogo se resincronizaba entero desde el CSV en cada arranque
y borraba lo ausente, asi que una FK habria bloqueado el arranque de la API.
Al pasar el catalogo a ser el maestro de `fmu_lima`, ese borrado deja de
existir y la referencia se puede exigir.

Las tablas conservan sus nombres: `pda`, `pda_jornada`, `pda_aerodromo`,
`pda_publicacion`, `pda_jornada_aerodromo` y `pda_demanda_horaria`. Ninguno
choca con los catorce de CTOT, y el prefijo ya dice de que subsistema son
mejor que un esquema aparte.

Revision ID: d8b3f07a2c91
Revises: c7a4e91d3f60
Create Date: 2026-09-22 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "d8b3f07a2c91"
down_revision: Union[str, Sequence[str], None] = "c7a4e91d3f60"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLAS = [
    "pda_demanda_horaria",
    "pda_publicacion",
    "pda_jornada_aerodromo",
    "pda_aerodromo",
    "pda",
    "pda_jornada",
]


def upgrade() -> None:
    # --- la jornada: que aerodromos cubre el documento y con que enlaces cierra ---
    op.create_table(
        "pda_jornada",
        sa.Column("fecha", sa.Date(), nullable=False),
        # El array se conserva porque es lo que el Portal lee hoy. Su
        # reemplazo, pda_jornada_aerodromo, entra mas abajo y ya esta poblado:
        # el array se retira cuando la lectura pase a la tabla.
        sa.Column("aerodromos", postgresql.ARRAY(sa.Text()), nullable=False),
        # Los enlaces se archivan CON el documento y no como configuracion
        # global: reabrir el del mes pasado debe reproducirlo tal como se
        # distribuyo, no con la lista vigente hoy.
        sa.Column("enlaces", postgresql.JSONB(), nullable=False),
        sa.Column("publicado_en", sa.DateTime(timezone=True), nullable=False),
        sa.Column("publicado_por", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("fecha", name="pda_jornada_pkey"),
        sa.CheckConstraint("cardinality(aerodromos) > 0", name="pda_jornada_con_aerodromos"),
    )
    op.create_index(
        "pda_jornada_publicado_idx", "pda_jornada", [sa.text("publicado_en DESC")]
    )

    # --- el bloque de un aerodromo en una jornada: la unidad que se firma ---
    op.create_table(
        "pda",
        sa.Column("aerodromo_icao", sa.String(4), nullable=False),
        sa.Column("fecha", sa.Date(), nullable=False),
        # El documento entero, tal como se publico. Es la evidencia: se firma y
        # se distribuye asi, y reabrirlo tiene que reproducirlo.
        sa.Column("documento", postgresql.JSONB(), nullable=False),
        sa.Column("publicado_en", sa.DateTime(timezone=True), nullable=False),
        sa.Column("publicado_por", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("aerodromo_icao", "fecha", name="pda_pkey"),
        sa.CheckConstraint("aerodromo_icao ~ '^[A-Z]{4}$'", name="pda_icao_formato"),
        # Ahora si: en atfm_db esta FK era imposible porque el catalogo se
        # borraba y recargaba en cada arranque.
        sa.ForeignKeyConstraint(
            ["aerodromo_icao"], ["aerodromo.codigo_oaci"], name="fk_pda_aerodromo"
        ),
    )

    # --- configuracion editorial: los valores con que se precarga un borrador ---
    op.create_table(
        "pda_aerodromo",
        sa.Column("aerodromo_icao", sa.String(4), nullable=False),
        # El rotulo con que se imprime. Existe aparte del nombre del maestro
        # porque ese viene de OurAirports, en ingles, y el documento es oficial
        # y en castellano.
        sa.Column("nombre", sa.Text(), nullable=False),
        sa.Column("declarada", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("reserva_pct", sa.Numeric(5, 2), nullable=False, server_default="0"),
        sa.Column("detalle", sa.Text(), nullable=False, server_default=""),
        sa.Column("regimen", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "actualizado_en",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("aerodromo_icao", name="pda_aerodromo_pkey"),
        sa.CheckConstraint("declarada >= 0", name="pda_aerodromo_declarada_valida"),
        sa.CheckConstraint(
            "reserva_pct >= 0 AND reserva_pct <= 100", name="pda_aerodromo_reserva_rango"
        ),
        sa.CheckConstraint(
            "aerodromo_icao ~ '^[A-Z]{4}$'", name="pda_aerodromo_icao_formato"
        ),
        sa.ForeignKeyConstraint(
            ["aerodromo_icao"], ["aerodromo.codigo_oaci"], name="fk_pda_aerodromo_config"
        ),
    )

    # --- el orden de impresion, que antes vivia en un array ---
    op.create_table(
        "pda_jornada_aerodromo",
        sa.Column("fecha", sa.Date(), nullable=False),
        sa.Column("aerodromo_icao", sa.String(4), nullable=False),
        sa.Column("orden", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("fecha", "aerodromo_icao"),
        sa.UniqueConstraint("fecha", "orden", name="pda_jornada_aerodromo_orden_unico"),
        sa.CheckConstraint("orden >= 1", name="pda_jornada_aerodromo_orden_positivo"),
        sa.ForeignKeyConstraint(
            ["fecha"], ["pda_jornada.fecha"], name="fk_pda_jornada_aerodromo_jornada",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["aerodromo_icao"], ["aerodromo.codigo_oaci"],
            name="fk_pda_jornada_aerodromo_maestro",
        ),
    )

    # --- cada edicion publicada, no solo la ultima ---
    op.create_table(
        "pda_publicacion",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("aerodromo_icao", sa.String(4), nullable=False),
        sa.Column("fecha", sa.Date(), nullable=False),
        sa.Column("numero_version", sa.Integer(), nullable=False),
        sa.Column("documento", postgresql.JSONB(), nullable=False),
        sa.Column("publicado_en", sa.DateTime(timezone=True), nullable=False),
        sa.Column("publicado_por", sa.Text(), nullable=False),
        sa.Column("es_vigente", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("motivo_correccion", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "aerodromo_icao", "fecha", "numero_version", name="pda_publicacion_version_unica"
        ),
        sa.CheckConstraint("numero_version >= 1", name="pda_publicacion_version_positiva"),
        sa.CheckConstraint(
            "aerodromo_icao ~ '^[A-Z]{4}$'", name="pda_publicacion_icao_formato"
        ),
        sa.ForeignKeyConstraint(
            ["aerodromo_icao"], ["aerodromo.codigo_oaci"], name="fk_pda_publicacion_aerodromo"
        ),
    )
    # Una sola edicion vigente por aerodromo y jornada. Parcial, porque la
    # regla aplica solo a la vigente: las anteriores conviven.
    op.create_index(
        "pda_publicacion_vigente_idx",
        "pda_publicacion",
        ["aerodromo_icao", "fecha"],
        unique=True,
        postgresql_where=sa.text("es_vigente"),
    )
    op.create_index("pda_publicacion_fecha_idx", "pda_publicacion", [sa.text("fecha DESC")])

    # --- la curva de demanda publicada, hora por hora ---
    op.create_table(
        "pda_demanda_horaria",
        sa.Column("publicacion_id", sa.BigInteger(), nullable=False),
        sa.Column("hora_utc", sa.SmallInteger(), nullable=False),
        sa.Column("arribos", sa.Integer(), nullable=True),
        sa.Column("despegues", sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint("publicacion_id", "hora_utc"),
        sa.CheckConstraint("hora_utc BETWEEN 0 AND 23", name="pda_demanda_hora_valida"),
        # NULL es "no se sabe" y cero es "ninguno": son cosas distintas.
        sa.CheckConstraint(
            "arribos IS NULL OR arribos >= 0", name="pda_demanda_arribos_validos"
        ),
        sa.CheckConstraint(
            "despegues IS NULL OR despegues >= 0", name="pda_demanda_despegues_validos"
        ),
        sa.ForeignKeyConstraint(
            ["publicacion_id"], ["pda_publicacion.id"],
            name="fk_pda_demanda_publicacion", ondelete="CASCADE",
        ),
    )


def downgrade() -> None:
    # IF EXISTS y no drop_table a secas: estas seis tablas no tienen modelo en
    # app/models.py -- las crea solo esta migracion --, asi que una base armada
    # con create_all no las tiene y el downgrade moria al intentar borrar la
    # primera. Es justo lo que hace la prueba que compara los modelos contra la
    # cadena de migraciones, que quedo caida por esto.
    #
    # Un downgrade tampoco deberia fallar porque lo que viene a deshacer ya no
    # este: el estado final es el mismo.
    for tabla in TABLAS:
        op.execute(f'DROP TABLE IF EXISTS "{tabla}"')
