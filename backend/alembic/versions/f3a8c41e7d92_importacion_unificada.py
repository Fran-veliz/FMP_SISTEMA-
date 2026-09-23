"""una sola tabla de ejecuciones de carga, y lo cargado apunta a su carga

Había dos registros de carga que eran casi la misma tabla:

- `cargas.lote_carga` -- subida de itinerario DGAC, alimenta
  `itinerarios.movimiento_programado`.
- `ctot.importacion_historica` -- subida de una grilla FMP ya cerrada,
  alimenta `ctot.vuelo_gestionado`.

Compartían `cargado_en`, `cargado_por`, `nombre_archivo`, `filas_aceptadas` y
`filas_rechazadas`, y se diferenciaban solo en los campos propios de cada tipo.
Se unifican en `integracion.importacion`, distinguidas por `tipo`.

**Lo que se gana no es ahorrar una tabla: es que lo cargado pueda apuntar a su
carga.** Ni los movimientos ni los vuelos tenían referencia a la ejecución que
los creó, así que la procedencia había que deducirla por coincidencia de
estación y fecha. Es literalmente lo que hace `borrar_historicos` en
scripts/import_historico.py: borra por pares (sector, fecha) porque no tiene un
identificador al que agarrarse. Con `importacion_id`, deshacer una carga es un
DELETE por identificador.

**El reconciliado de lo ya cargado es deliberadamente conservador:**

- `movimiento_programado.importacion_id` queda NULO en todo lo existente. Se
  podría adivinar por (estación, vigente_desde), pero varias cargas comparten
  esos valores -- una actualización de temporada y las diarias posteriores del
  mismo tramo -- y asignar cada fila a la carga "más probable" sería inventar
  justamente la trazabilidad que faltaba.
- `vuelo_gestionado.importacion_id` se completa **solo cuando exactamente una**
  importación histórica coincide en (sector, fecha_operacion). Si una fecha se
  importó dos veces, las dos filas coinciden y no hay forma de saber cuál
  produjo el vuelo: queda nulo.

`fila_origen` queda nulo en todo lo existente: ese dato nunca se guardó. Los
parsers ahora lo reportan, así que lo que se cargue de acá en adelante lo tiene.

**Las dos tablas viejas se eliminan**, a diferencia de lo que se hizo con las
23 columnas de niveles. El criterio es el riesgo del traslado: acá la
correspondencia es 1:1 y sin ambigüedad -- cada fila de log entra como una fila
de log --, así que no hay paridad que verificar contra el original. El
`downgrade` las recrea y devuelve las filas.

Revision ID: f3a8c41e7d92
Revises: e1f8b26d05a3
Create Date: 2026-09-21 00:00:00.000000

"""
import logging
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "f3a8c41e7d92"
down_revision: Union[str, Sequence[str], None] = "e1f8b26d05a3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

logger = logging.getLogger("alembic.runtime.migration")


def _hay_tabla(esquema: str, tabla: str) -> bool:
    return bool(
        op.get_bind().execute(
            sa.text(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema = :e AND table_name = :t"
            ),
            {"e": esquema, "t": tabla},
        ).first()
    )


def _columnas(esquema: str, tabla: str) -> set[str]:
    filas = op.get_bind().execute(
        sa.text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = :e AND table_name = :t"
        ),
        {"e": esquema, "t": tabla},
    )
    return {fila[0] for fila in filas}


def upgrade() -> None:
    conn = op.get_bind()

    op.execute("CREATE SCHEMA IF NOT EXISTS integracion")

    # Los tipos enumerados se crean UNA vez, aca, y las columnas de abajo los
    # referencian con create_type=False. Sin eso, `create_table` vuelve a
    # emitir el CREATE TYPE antes del CREATE TABLE y la migracion muere con
    # "type already exists" -- el tipo lo acaba de crear esta misma linea.
    #
    # Con `sector` es peor todavia: ese tipo YA existe en la base desde el
    # baseline, porque lo usa ctot.vuelo_gestionado. Dejar que create_table lo
    # cree seria un fallo garantizado contra produccion.
    tipo_importacion = postgresql.ENUM(
        "itinerario", "grilla_historica", name="tipo_importacion_enum", create_type=False
    )
    sa.Enum("itinerario", "grilla_historica", name="tipo_importacion_enum").create(
        conn, checkfirst=True
    )
    sector_existente = postgresql.ENUM("SUR", "NOR", name="sector", create_type=False)

    if not _hay_tabla("integracion", "importacion"):
        op.create_table(
            "importacion",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("tipo", tipo_importacion, nullable=False),
            sa.Column("cargado_en", sa.DateTime(), nullable=False),
            sa.Column("cargado_por", sa.String(64), nullable=True),
            sa.Column("cargado_por_id", sa.Integer(), nullable=True),
            sa.Column("nombre_archivo", sa.String(200), nullable=True),
            # propios de una carga de itinerario
            sa.Column("codigo_aeropuerto", sa.String(4), nullable=True),
            sa.Column("vigente_desde", sa.Date(), nullable=True),
            sa.Column("alcance", sa.String(8), nullable=True),
            # propios de una carga de grilla historica
            sa.Column("sector", sector_existente, nullable=True),
            sa.Column("fecha_operacion", sa.Date(), nullable=True),
            sa.Column("filas_aceptadas", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("filas_rechazadas", sa.Integer(), nullable=False, server_default="0"),
            sa.PrimaryKeyConstraint("id"),
            sa.ForeignKeyConstraint(
                ["cargado_por_id"], ["ctot.operador.id"], name="fk_importacion_operador"
            ),
            sa.ForeignKeyConstraint(
                ["codigo_aeropuerto"],
                ["catalogos.aeropuerto.codigo_oaci"],
                name="fk_importacion_aeropuerto",
            ),
            schema="integracion",
        )
        for columna in ("tipo", "cargado_por_id", "sector", "fecha_operacion"):
            op.create_index(
                f"ix_importacion_{columna}", "importacion", [columna], schema="integracion"
            )

    # --- traslado de las dos tablas viejas ---
    trasladadas = 0
    if _hay_tabla("cargas", "lote_carga"):
        resultado = conn.execute(
            sa.text(
                """
                INSERT INTO integracion.importacion (
                    tipo, cargado_en, cargado_por, cargado_por_id, nombre_archivo,
                    codigo_aeropuerto, vigente_desde, alcance,
                    filas_aceptadas, filas_rechazadas
                )
                SELECT 'itinerario', cargado_en, cargado_por, cargado_por_id,
                       nombre_archivo, codigo_aeropuerto, vigente_desde, alcance,
                       filas_aceptadas, filas_rechazadas
                FROM cargas.lote_carga
                """
            )
        )
        trasladadas += resultado.rowcount or 0
        logger.info("cargas.lote_carga: %s fila(s) trasladadas.", resultado.rowcount or 0)

    if _hay_tabla("ctot", "importacion_historica"):
        resultado = conn.execute(
            sa.text(
                """
                INSERT INTO integracion.importacion (
                    tipo, cargado_en, cargado_por, cargado_por_id, nombre_archivo,
                    sector, fecha_operacion, filas_aceptadas, filas_rechazadas
                )
                SELECT 'grilla_historica', cargado_en, cargado_por, cargado_por_id,
                       nombre_archivo, sector, fecha_operacion,
                       filas_aceptadas, filas_rechazadas
                FROM ctot.importacion_historica
                """
            )
        )
        trasladadas += resultado.rowcount or 0
        logger.info(
            "ctot.importacion_historica: %s fila(s) trasladadas.", resultado.rowcount or 0
        )

    # --- procedencia de lo cargado ---
    for esquema, tabla, nombre_fk in (
        ("itinerarios", "movimiento_programado", "fk_movimiento_programado_importacion"),
        ("ctot", "vuelo_gestionado", "fk_vuelo_gestionado_importacion"),
    ):
        if not _hay_tabla(esquema, tabla):
            continue
        columnas = _columnas(esquema, tabla)
        if "importacion_id" not in columnas:
            op.add_column(
                tabla, sa.Column("importacion_id", sa.Integer(), nullable=True), schema=esquema
            )
            op.create_foreign_key(
                nombre_fk, tabla, "importacion", ["importacion_id"], ["id"],
                source_schema=esquema, referent_schema="integracion",
            )
            op.create_index(
                f"ix_{tabla}_importacion_id", tabla, ["importacion_id"], schema=esquema
            )
        if "fila_origen" not in columnas:
            op.add_column(
                tabla, sa.Column("fila_origen", sa.Integer(), nullable=True), schema=esquema
            )

    # Solo donde la correspondencia es inequivoca: exactamente una importacion
    # historica para ese sector y esa fecha. Si una fecha se importo dos veces
    # no hay forma de saber cual produjo el vuelo, y queda nulo.
    if _hay_tabla("ctot", "vuelo_gestionado"):
        resultado = conn.execute(
            sa.text(
                """
                UPDATE ctot.vuelo_gestionado AS v
                SET importacion_id = i.id
                FROM (
                    SELECT sector, fecha_operacion, min(id) AS id, count(*) AS cuantas
                    FROM integracion.importacion
                    WHERE tipo = 'grilla_historica'
                    GROUP BY sector, fecha_operacion
                ) AS i
                WHERE v.importacion_id IS NULL
                  AND i.cuantas = 1
                  AND v.sector = i.sector
                  AND v.fecha_operacion = i.fecha_operacion
                """
            )
        )
        logger.info(
            "ctot.vuelo_gestionado: %s vuelo(s) quedaron apuntando a su carga historica. "
            "Los demas quedan nulos: o los creo un operador desde la grilla, o su fecha "
            "se importo mas de una vez y no hay forma de saber cual carga los produjo.",
            resultado.rowcount or 0,
        )

    logger.warning(
        "itinerarios.movimiento_programado.importacion_id queda nulo en todo lo ya "
        "cargado: varias cargas comparten estacion y vigencia, y asignar cada fila a "
        "la carga mas probable seria inventar la trazabilidad que faltaba."
    )

    # --- se retiran las tablas viejas ---
    if _hay_tabla("cargas", "lote_carga"):
        op.drop_table("lote_carga", schema="cargas")
    if _hay_tabla("ctot", "importacion_historica"):
        op.drop_table("importacion_historica", schema="ctot")

    logger.info("integracion.importacion: %s ejecucion(es) en total.", trasladadas)


def downgrade() -> None:
    conn = op.get_bind()

    op.create_table(
        "lote_carga",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("cargado_en", sa.DateTime(), nullable=False),
        sa.Column("cargado_por", sa.String(64), nullable=True),
        sa.Column("cargado_por_id", sa.Integer(), nullable=True),
        sa.Column("nombre_archivo", sa.String(200), nullable=True),
        sa.Column("codigo_aeropuerto", sa.String(4), nullable=False),
        sa.Column("vigente_desde", sa.Date(), nullable=False),
        sa.Column("alcance", sa.String(8), nullable=False, server_default="desde"),
        sa.Column("filas_aceptadas", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("filas_rechazadas", sa.Integer(), nullable=False, server_default="0"),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["cargado_por_id"], ["ctot.operador.id"], name="fk_lote_carga_operador"
        ),
        sa.ForeignKeyConstraint(
            ["codigo_aeropuerto"],
            ["catalogos.aeropuerto.codigo_oaci"],
            name="fk_lote_carga_aeropuerto",
        ),
        schema="cargas",
    )
    op.create_table(
        "importacion_historica",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column(
            "sector",
            postgresql.ENUM("SUR", "NOR", name="sector", create_type=False),
            nullable=False,
        ),
        sa.Column("fecha_operacion", sa.Date(), nullable=False),
        sa.Column("cargado_en", sa.DateTime(), nullable=False),
        sa.Column("cargado_por", sa.String(64), nullable=True),
        sa.Column("cargado_por_id", sa.Integer(), nullable=True),
        sa.Column("nombre_archivo", sa.String(200), nullable=True),
        sa.Column("filas_aceptadas", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("filas_rechazadas", sa.Integer(), nullable=False, server_default="0"),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["cargado_por_id"],
            ["ctot.operador.id"],
            name="fk_importacion_historica_operador",
        ),
        schema="ctot",
    )

    conn.execute(
        sa.text(
            """
            INSERT INTO cargas.lote_carga (
                cargado_en, cargado_por, cargado_por_id, nombre_archivo,
                codigo_aeropuerto, vigente_desde, alcance,
                filas_aceptadas, filas_rechazadas
            )
            SELECT cargado_en, cargado_por, cargado_por_id, nombre_archivo,
                   codigo_aeropuerto, vigente_desde, coalesce(alcance, 'desde'),
                   filas_aceptadas, filas_rechazadas
            FROM integracion.importacion
            WHERE tipo = 'itinerario' AND codigo_aeropuerto IS NOT NULL
              AND vigente_desde IS NOT NULL
            """
        )
    )
    conn.execute(
        sa.text(
            """
            INSERT INTO ctot.importacion_historica (
                sector, fecha_operacion, cargado_en, cargado_por, cargado_por_id,
                nombre_archivo, filas_aceptadas, filas_rechazadas
            )
            SELECT sector, fecha_operacion, cargado_en, cargado_por, cargado_por_id,
                   nombre_archivo, filas_aceptadas, filas_rechazadas
            FROM integracion.importacion
            WHERE tipo = 'grilla_historica' AND sector IS NOT NULL
              AND fecha_operacion IS NOT NULL
            """
        )
    )

    for esquema, tabla, nombre_fk in (
        ("itinerarios", "movimiento_programado", "fk_movimiento_programado_importacion"),
        ("ctot", "vuelo_gestionado", "fk_vuelo_gestionado_importacion"),
    ):
        columnas = _columnas(esquema, tabla)
        if "fila_origen" in columnas:
            op.drop_column(tabla, "fila_origen", schema=esquema)
        if "importacion_id" in columnas:
            op.drop_index(f"ix_{tabla}_importacion_id", table_name=tabla, schema=esquema)
            op.drop_constraint(nombre_fk, tabla, schema=esquema, type_="foreignkey")
            op.drop_column(tabla, "importacion_id", schema=esquema)

    op.drop_table("importacion", schema="integracion")
    sa.Enum(name="tipo_importacion_enum").drop(conn, checkfirst=True)
