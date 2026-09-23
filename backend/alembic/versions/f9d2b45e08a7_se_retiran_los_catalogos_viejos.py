"""se retiran aeropuerto y codigo_aeropuerto

Las dos quedaron en la base cuando el catalogo se unifico en `aerodromo`
(migracion b2f5d81c40e7). Igual que las 23 columnas de nivel, no fue un
olvido: se conservaron como el dato ORIGINAL congelado, para poder comparar
el maestro contra lo que habia antes de que nada dependiera de el.

**La paridad se verifico contra produccion antes de esta migracion**, y dio
cero diferencias en las dos:

- `aeropuerto` (2 estaciones): las dos estan en `aerodromo`, con el mismo
  nombre en `nombre_operativo`, marcadas como estacion CTOT, con el mismo
  estado de actividad, y su capacidad declarada trasladada a
  `capacidad_aerodromo`.
- `codigo_aeropuerto` (93 equivalencias): las 93 estan en `aerodromo` por su
  IATA, con el mismo OACI, nombre, ciudad y pais.

Tampoco quedaba nada apuntandolas: las dos claves foraneas que las
referenciaban -- desde `movimiento_programado` e `importacion` -- se
repuntaron al maestro en b2f5d81c40e7, justamente porque una FK contra una
tabla que ya nadie actualiza no protege nada.

El codigo dejo de leerlas en esa misma migracion: `/estaciones` lista desde
`aerodromo` filtrando por `es_estacion_ctot`, y el mapa IATA->OACI de la carga
de itinerario sale del maestro. El modelo las mapeaba solo para seguir
describiendo la base real.

**El downgrade las recrea y las vuelve a llenar desde `aerodromo`.** Puede
hacerlo porque el maestro contiene todo lo que ellas tenian -- eso es lo que
la paridad demuestra -- y porque `es_estacion_ctot` conserva la distincion que
separaba a una tabla de la otra: las estaciones con itinerario propio de los
simples extremos de ruta.

No devuelve exactamente las mismas filas, y conviene saberlo: `aeropuerto`
vuelve con sus 2 estaciones, pero `codigo_aeropuerto` vuelve con 101
equivalencias en vez de 93. Las 8 de mas son aerodromos que aporto el catalogo
del Portal al consolidarse (d8b3f07a2c91) y que tienen codigo IATA. Es un
superconjunto de lo que habia, no una perdida: son equivalencias reales, y
volver atras con ellas deja el sistema mejor de como estaba.

Revision ID: f9d2b45e08a7
Revises: e5c81a6b9d34
Create Date: 2026-09-22 00:00:00.000000

"""
import logging
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f9d2b45e08a7"
down_revision: Union[str, Sequence[str], None] = "e5c81a6b9d34"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

logger = logging.getLogger("alembic.runtime.migration")


def _hay_tabla(tabla: str) -> bool:
    return bool(
        op.get_bind().execute(
            sa.text(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_name = :t"
            ),
            {"t": tabla},
        ).first()
    )


def upgrade() -> None:
    conn = op.get_bind()

    # Antes de borrar: que nada las apunte. Si alguien agrego una FK despues
    # de b2f5d81c40e7, el DROP fallaria de todos modos; asi el mensaje dice
    # cual es en vez de dejar un error de Postgres suelto.
    apuntadas = conn.execute(
        sa.text(
            "SELECT conrelid::regclass::text FROM pg_constraint "
            "WHERE contype = 'f' AND confrelid::regclass::text IN "
            "('aeropuerto', 'codigo_aeropuerto')"
        )
    ).scalars().all()
    if apuntadas:
        raise RuntimeError(
            "Todavia hay claves foraneas apuntando a los catalogos viejos, desde: "
            f"{', '.join(sorted(set(apuntadas)))}. Repuntarlas a `aerodromo` antes "
            "de retirarlos."
        )

    # Y que su contenido este en el maestro. Se comprueba aca y no solo antes
    # de escribir la migracion, porque esto tiene que valer tambien en una base
    # que llegue a este punto por otro camino.
    huerfanas = conn.execute(
        sa.text(
            """
            SELECT
              (SELECT count(*) FROM aeropuerto p
                 WHERE NOT EXISTS (SELECT 1 FROM aerodromo a
                                   WHERE a.codigo_oaci = p.codigo_oaci)),
              (SELECT count(*) FROM codigo_aeropuerto c
                 WHERE NOT EXISTS (SELECT 1 FROM aerodromo a
                                   WHERE a.codigo_iata = c.codigo_iata))
            """
        )
    ).one() if _hay_tabla("aeropuerto") and _hay_tabla("codigo_aeropuerto") else (0, 0)
    if any(huerfanas):
        raise RuntimeError(
            f"{huerfanas[0]} estacion(es) y {huerfanas[1]} equivalencia(s) no estan "
            "en `aerodromo`. Borrar los catalogos viejos perderia esas filas."
        )

    for tabla in ("aeropuerto", "codigo_aeropuerto"):
        if _hay_tabla(tabla):
            op.drop_table(tabla)
            logger.info("Retirada la tabla %s: su contenido vive en `aerodromo`.", tabla)


def downgrade() -> None:
    op.create_table(
        "aeropuerto",
        sa.Column("codigo_oaci", sa.String(4), nullable=False),
        sa.Column("nombre", sa.String(100), nullable=False),
        sa.Column("capacidad_declarada", sa.Integer(), nullable=True),
        sa.Column("activa", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.PrimaryKeyConstraint("codigo_oaci"),
    )
    op.create_table(
        "codigo_aeropuerto",
        sa.Column("codigo_iata", sa.String(4), nullable=False),
        sa.Column("codigo_oaci", sa.String(4), nullable=True),
        sa.Column("nombre", sa.String(200), nullable=True),
        sa.Column("ciudad", sa.String(100), nullable=True),
        sa.Column("pais", sa.String(4), nullable=True),
        sa.PrimaryKeyConstraint("codigo_iata"),
    )

    # Se rellenan desde el maestro, no quedan vacias. `es_estacion_ctot` es lo
    # que separa una tabla de la otra: las estaciones con itinerario propio
    # frente a los simples extremos de ruta.
    op.execute(
        """
        INSERT INTO aeropuerto (codigo_oaci, nombre, capacidad_declarada, activa)
        SELECT a.codigo_oaci,
               coalesce(a.nombre_operativo, a.nombre_oficial, a.codigo_oaci),
               (SELECT c.valor FROM capacidad_aerodromo c
                WHERE c.aerodromo_id = a.id AND c.vigente_hasta IS NULL
                ORDER BY c.vigente_desde DESC NULLS LAST LIMIT 1),
               a.activo
        FROM aerodromo a
        WHERE a.es_estacion_ctot AND a.codigo_oaci IS NOT NULL
        """
    )
    op.execute(
        """
        INSERT INTO codigo_aeropuerto (codigo_iata, codigo_oaci, nombre, ciudad, pais)
        SELECT a.codigo_iata, a.codigo_oaci, a.nombre_oficial, a.ciudad, a.pais
        FROM aerodromo a
        WHERE a.codigo_iata IS NOT NULL
        """
    )
