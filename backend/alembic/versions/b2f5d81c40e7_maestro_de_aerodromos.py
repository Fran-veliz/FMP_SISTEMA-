"""un maestro unico de aerodromos, y la capacidad con vigencia

El mismo aeródromo vivía en dos tablas de CTOT sin relación entre sí:

- `catalogos.aeropuerto` (PK OACI) -- las estaciones con itinerario propio y
  su capacidad declarada.
- `catalogos.codigo_aeropuerto` (PK IATA) -- los extremos de rutas, incluidos
  los extranjeros, con la equivalencia IATA/OACI, ciudad y país.

Una tiene PK OACI y la otra PK IATA, así que **no había ninguna clave foránea
entre ellas y no se podían cruzar sin adivinar**. Arequipa estaba en las dos
-- como estación en una y como AQP en la otra -- y nada decía que fuera el
mismo lugar. Se unifican en `catalogos.aerodromo`.

Hay una tercera tabla con el mismo hecho, `aerodromos` del Portal ATFM, que es
la más completa: región, coordenadas, elevación, tipo. **No entra en esta
migración porque está en otra base de datos.** El maestro se crea con la forma
que la va a recibir -- esas columnas quedan nulas -- y sus 32 filas se cargan
cuando se consolide la base. No se inventan coordenadas mientras tanto.

**La unión, no la intersección.** El maestro incluye los extremos de rutas y
los aeródromos extranjeros: si se armara solo con las estaciones, la carga de
itinerario perdería los códigos IATA que necesita traducir.

**Estar en el maestro no convierte a un aeródromo en estación CTOT.** Eso lo
dice `es_estacion_ctot`, que se activa solo para las filas que venían de
`aeropuerto`. Que Lima aparezca en una equivalencia IATA junto a Ámsterdam no
los pone al mismo nivel operativo, y sin esa distinción el desplegable de la
carga ofrecería noventa aeropuertos del mundo.

**La conciliación es por código OACI**, que es la única correspondencia
comprobable entre las dos tablas. Una equivalencia IATA cuyo OACI ya esté
cargado como estación se agrega **a esa misma fila**; no se crea otra. Las
filas de `codigo_aeropuerto` sin OACI -- la columna lo admitía -- entran con
solo su IATA: por eso `codigo_oaci` es nulable en el maestro, con un CHECK que
exige al menos uno de los dos códigos.

**La capacidad pasa a `catalogos.capacidad_aerodromo`.** Era una columna de
`aeropuerto`, y antes de eso la constante 49 en el código de /forecast -- que
es el límite de Lima, no de cualquier aeródromo. Tiene entidad propia porque
no es un atributo del lugar sino una declaración con fecha: cambia de manera
excepcional, y cuando cambia hay que poder decir desde cuándo rige la nueva
sin perder la anterior. Es además el mismo hecho que el PDA declara en
`pda_aerodromo.declarada`, y esta tabla es donde van a converger.

`vigente_desde` queda nulo en lo migrado: no se sabe desde cuándo regía la
cifra que estaba en la columna, y fecharla en el día de la migración sería
inventarlo. `valor` nulo significa **sin declarar**, no cero.

**Las dos claves foráneas que apuntaban a `catalogos.aeropuerto` se repuntan
al maestro** -- `movimiento_programado.codigo_aeropuerto` e
`importacion.codigo_aeropuerto` --, porque la tabla vieja deja de mantenerse y
una FK contra una tabla que nadie actualiza no protege nada.

Las dos tablas viejas **no se eliminan**: quedan como originales congelados
para poder comparar el maestro contra lo que había, igual que las 23 columnas
de niveles.

Revision ID: b2f5d81c40e7
Revises: a4c9e02b7f18
Create Date: 2026-09-21 00:00:00.000000

"""
import logging
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b2f5d81c40e7"
down_revision: Union[str, Sequence[str], None] = "a4c9e02b7f18"
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


def _hay_restriccion(esquema: str, tabla: str, nombre: str) -> bool:
    return bool(
        op.get_bind().execute(
            sa.text(
                "SELECT 1 FROM information_schema.table_constraints "
                "WHERE table_schema = :e AND table_name = :t AND constraint_name = :c"
            ),
            {"e": esquema, "t": tabla, "c": nombre},
        ).first()
    )


def upgrade() -> None:
    conn = op.get_bind()

    if not _hay_tabla("catalogos", "aerodromo"):
        op.create_table(
            "aerodromo",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("codigo_oaci", sa.String(4), nullable=True),
            sa.Column("codigo_iata", sa.String(4), nullable=True),
            sa.Column("nombre_oficial", sa.String(200), nullable=True),
            sa.Column("nombre_operativo", sa.String(100), nullable=True),
            sa.Column("ciudad", sa.String(100), nullable=True),
            sa.Column("region", sa.String(100), nullable=True),
            sa.Column("pais", sa.String(4), nullable=True),
            sa.Column("latitud", sa.Float(), nullable=True),
            sa.Column("longitud", sa.Float(), nullable=True),
            sa.Column("elevacion_ft", sa.Integer(), nullable=True),
            sa.Column("tipo", sa.String(16), nullable=True),
            sa.Column("servicio_comercial", sa.Boolean(), nullable=True),
            sa.Column(
                "es_estacion_ctot", sa.Boolean(), nullable=False, server_default=sa.false()
            ),
            sa.Column("activo", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("codigo_oaci", name="uq_aerodromo_codigo_oaci"),
            sa.UniqueConstraint("codigo_iata", name="uq_aerodromo_codigo_iata"),
            # Un aeródromo sin ningún código no se puede referenciar ni cruzar
            # con nada: no es un registro, es una fila vacía.
            sa.CheckConstraint(
                "codigo_oaci IS NOT NULL OR codigo_iata IS NOT NULL",
                name="ck_aerodromo_algun_codigo",
            ),
            # Coordenadas imposibles son un dato falso en un sistema
            # aeronáutico. Mismo criterio que el esquema del Portal.
            sa.CheckConstraint(
                "latitud IS NULL OR (latitud BETWEEN -90 AND 90)",
                name="ck_aerodromo_latitud",
            ),
            sa.CheckConstraint(
                "longitud IS NULL OR (longitud BETWEEN -180 AND 180)",
                name="ck_aerodromo_longitud",
            ),
            schema="catalogos",
        )

    if not _hay_tabla("catalogos", "capacidad_aerodromo"):
        op.create_table(
            "capacidad_aerodromo",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("aerodromo_id", sa.Integer(), nullable=False),
            sa.Column("valor", sa.Integer(), nullable=True),
            sa.Column("configuracion", sa.String(200), nullable=True),
            sa.Column("regimen", sa.String(120), nullable=True),
            sa.Column("vigente_desde", sa.Date(), nullable=True),
            sa.Column("vigente_hasta", sa.Date(), nullable=True),
            sa.Column("fuente", sa.String(64), nullable=True),
            sa.PrimaryKeyConstraint("id"),
            sa.ForeignKeyConstraint(
                ["aerodromo_id"], ["catalogos.aerodromo.id"], name="fk_capacidad_aerodromo"
            ),
            # Una capacidad negativa no existe. El cero sí: significa cerrado,
            # y es distinto de NULL, que es "sin declarar".
            sa.CheckConstraint("valor IS NULL OR valor >= 0", name="ck_capacidad_valor"),
            sa.CheckConstraint(
                "vigente_desde IS NULL OR vigente_hasta IS NULL "
                "OR vigente_hasta >= vigente_desde",
                name="ck_capacidad_vigencia",
            ),
            schema="catalogos",
        )
        op.create_index(
            "ix_capacidad_aerodromo_aerodromo_id",
            "capacidad_aerodromo",
            ["aerodromo_id"],
            schema="catalogos",
        )

    ya_cargado = conn.execute(sa.text("SELECT count(*) FROM catalogos.aerodromo")).scalar()
    if ya_cargado:
        logger.info(
            "catalogos.aerodromo ya tiene %s fila(s): no se vuelve a conciliar.", ya_cargado
        )
    else:
        # 1. Las estaciones. Van primero para que las equivalencias IATA se
        #    peguen a su fila y no creen una nueva.
        if _hay_tabla("catalogos", "aeropuerto"):
            resultado = conn.execute(
                sa.text(
                    """
                    INSERT INTO catalogos.aerodromo (
                        codigo_oaci, nombre_operativo, es_estacion_ctot, activo
                    )
                    SELECT codigo_oaci, nombre, true, activa
                    FROM catalogos.aeropuerto
                    """
                )
            )
            logger.info("Estaciones trasladadas: %s.", resultado.rowcount or 0)

            # La capacidad pasa a ser una declaración. `vigente_desde` queda
            # nulo: no se sabe desde cuándo regía.
            conn.execute(
                sa.text(
                    """
                    INSERT INTO catalogos.capacidad_aerodromo (
                        aerodromo_id, valor, fuente
                    )
                    SELECT a.id, p.capacidad_declarada, 'migracion_catalogo'
                    FROM catalogos.aeropuerto AS p
                    JOIN catalogos.aerodromo AS a ON a.codigo_oaci = p.codigo_oaci
                    """
                )
            )

        # 2. Las equivalencias IATA que traen un OACI ya cargado: se completan
        #    sobre la misma fila.
        if _hay_tabla("catalogos", "codigo_aeropuerto"):
            completadas = conn.execute(
                sa.text(
                    """
                    UPDATE catalogos.aerodromo AS a
                    SET codigo_iata = c.codigo_iata,
                        nombre_oficial = coalesce(a.nombre_oficial, c.nombre),
                        ciudad = coalesce(a.ciudad, c.ciudad),
                        pais = coalesce(a.pais, c.pais)
                    FROM catalogos.codigo_aeropuerto AS c
                    WHERE c.codigo_oaci IS NOT NULL
                      AND a.codigo_oaci = c.codigo_oaci
                    """
                )
            )
            logger.info(
                "Equivalencias IATA pegadas a una estacion ya cargada: %s.",
                completadas.rowcount or 0,
            )

            # 3. El resto entra como filas nuevas. Las que no tienen OACI
            #    entran con solo su IATA: la columna lo admitia y descartarlas
            #    romperia la traduccion de esos destinos.
            nuevas = conn.execute(
                sa.text(
                    """
                    INSERT INTO catalogos.aerodromo (
                        codigo_oaci, codigo_iata, nombre_oficial, ciudad, pais,
                        es_estacion_ctot, activo
                    )
                    SELECT c.codigo_oaci, c.codigo_iata, c.nombre, c.ciudad, c.pais,
                           false, true
                    FROM catalogos.codigo_aeropuerto AS c
                    WHERE NOT EXISTS (
                        SELECT 1 FROM catalogos.aerodromo AS a
                        WHERE a.codigo_iata = c.codigo_iata
                    )
                    """
                )
            )
            logger.info("Extremos de rutas agregados: %s.", nuevas.rowcount or 0)

            sin_oaci = conn.execute(
                sa.text(
                    "SELECT codigo_iata FROM catalogos.aerodromo "
                    "WHERE codigo_oaci IS NULL ORDER BY codigo_iata"
                )
            ).scalars().all()
            if sin_oaci:
                logger.warning(
                    "Aerodromos cargados solo con IATA, sin equivalencia OACI: %s. "
                    "Entran igual porque la columna lo admitia; la carga de "
                    "itinerario no puede traducirlos hasta que se les complete el "
                    "codigo OACI a mano.",
                    ", ".join(sin_oaci),
                )

    # 4. Las FK que apuntaban a la tabla vieja pasan al maestro.
    for esquema, tabla, vieja, nueva in (
        (
            "itinerarios",
            "movimiento_programado",
            "fk_movimiento_programado_aeropuerto",
            "fk_movimiento_programado_aerodromo",
        ),
        ("integracion", "importacion", "fk_importacion_aeropuerto", "fk_importacion_aerodromo"),
    ):
        if not _hay_tabla(esquema, tabla):
            continue
        if _hay_restriccion(esquema, tabla, vieja):
            op.drop_constraint(vieja, tabla, schema=esquema, type_="foreignkey")
        if not _hay_restriccion(esquema, tabla, nueva):
            op.create_foreign_key(
                nueva,
                tabla,
                "aerodromo",
                ["codigo_aeropuerto"],
                ["codigo_oaci"],
                source_schema=esquema,
                referent_schema="catalogos",
            )

    logger.info(
        "catalogos.aeropuerto y catalogos.codigo_aeropuerto quedan como originales "
        "congelados: nada las lee ni las escribe. Se retiran cuando la paridad del "
        "maestro este verificada. Las 32 filas del catalogo del Portal ATFM entran "
        "cuando se consolide la base: las columnas geograficas estan esperandolas."
    )


def downgrade() -> None:
    for esquema, tabla, vieja, nueva in (
        (
            "itinerarios",
            "movimiento_programado",
            "fk_movimiento_programado_aeropuerto",
            "fk_movimiento_programado_aerodromo",
        ),
        ("integracion", "importacion", "fk_importacion_aeropuerto", "fk_importacion_aerodromo"),
    ):
        if not _hay_tabla(esquema, tabla):
            continue
        if _hay_restriccion(esquema, tabla, nueva):
            op.drop_constraint(nueva, tabla, schema=esquema, type_="foreignkey")
        if not _hay_restriccion(esquema, tabla, vieja):
            op.create_foreign_key(
                vieja,
                tabla,
                "aeropuerto",
                ["codigo_aeropuerto"],
                ["codigo_oaci"],
                source_schema=esquema,
                referent_schema="catalogos",
            )

    # Las dos tablas de origen nunca se tocaron, asi que volver atras es
    # descartar el maestro: el dato sigue intacto donde estaba.
    op.drop_table("capacidad_aerodromo", schema="catalogos")
    op.drop_table("aerodromo", schema="catalogos")
