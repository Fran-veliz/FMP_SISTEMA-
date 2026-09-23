"""las tablas dejan los esquemas de dominio y quedan todas en public

La base tenia siete esquemas -- catalogos, cargas, itinerarios, ctot,
informes, operaciones e integracion -- con catorce tablas repartidas. Todas
pasan a `public` y los esquemas se retiran.

**Por que.** Un esquema separa NOMBRES, no privilegios: los permisos igual hay
que darlos objeto por objeto, asi que lo unico que aportaban era agrupamiento
visual. A cambio, cada referencia habia que calificarla, y `search_path`
decidia en silencio a que tabla apuntaba un nombre sin calificar -- que es
exactamente como el Portal ATFM se quedo sin demanda en set-2026, pidiendo
`public.itinerary_entries` cuando la tabla ya vivia en `itinerarios`.

El costo se volvio concreto al consolidar con el Portal: sus tablas tenian que
entrar a alguno de esos esquemas o crear el suyo, y ninguna de las dos cosas
describe lo que son. Con todo en `public`, el nombre de la tabla es la unica
coordenada que hace falta, y las de ATFM entran al lado sin discusion.

**No hay colisiones de nombre.** Se comprobo antes de mover: las catorce
tablas de CTOT tienen nombres distintos entre si, y tambien distintos de las
siete del Portal (`pda`, `pda_jornada`, `pda_aerodromo`, `pda_publicacion`,
`pda_jornada_aerodromo`, `pda_demanda_horaria` y `aerodromos`). La unica
superposicion es conceptual: `aerodromos` del Portal y `aerodromo` de CTOT son
el mismo hecho, y se fusionan en la migracion siguiente en vez de convivir.

**Mover una tabla de esquema no toca sus filas.** `ALTER TABLE ... SET SCHEMA`
es una operacion de catalogo: no reescribe datos ni revalida restricciones, y
las claves foraneas, indices y secuencias siguen a su tabla. Sobre 224.379
vuelos y 816.120 movimientos, tarda lo mismo que sobre una tabla vacia.

**Los tipos enumerados tambien se mueven.** Viven en un esquema como las
tablas, y dejar `tipo_movimiento_enum` en `itinerarios` mientras se borra ese
esquema haria fallar el DROP -- o, peor, dejaria el tipo huerfano en un
esquema que ya nadie tiene en el `search_path`.

Los esquemas se borran con `DROP SCHEMA ... RESTRICT`, sin CASCADE: si algo
quedo adentro que esta migracion no previo, falla y lo dice, en vez de
llevarselo por delante en silencio.

Revision ID: c7a4e91d3f60
Revises: b2f5d81c40e7
Create Date: 2026-09-22 00:00:00.000000

"""
import logging
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c7a4e91d3f60"
down_revision: Union[str, Sequence[str], None] = "b2f5d81c40e7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

logger = logging.getLogger("alembic.runtime.migration")

# esquema de origen -> tablas que vivian ahi
REPARTO = {
    "catalogos": ["aerodromo", "aeropuerto", "capacidad_aerodromo", "codigo_aeropuerto", "motivo"],
    "ctot": [
        "historial_vuelo",
        "operador",
        "registro_eliminacion_vuelo",
        "sesion",
        "turno",
        "vuelo_gestionado",
        "vuelo_revision",
    ],
    "integracion": ["importacion"],
    "itinerarios": ["movimiento_programado"],
    # Quedaron vacios: nunca llegaron a tener tablas o ya se vaciaron.
    "cargas": [],
    "informes": [],
    "operaciones": [],
}

ESQUEMAS = list(REPARTO)


def _esquema_de_tabla(tabla: str) -> str | None:
    fila = op.get_bind().execute(
        sa.text(
            "SELECT table_schema FROM information_schema.tables WHERE table_name = :t"
        ),
        {"t": tabla},
    ).first()
    return fila[0] if fila else None


def _mover_tipos(destino: str) -> list[str]:
    """Traslada los tipos enumerados que esten en los esquemas de dominio."""
    conn = op.get_bind()
    tipos = conn.execute(
        sa.text(
            "SELECT n.nspname, t.typname FROM pg_type t "
            "JOIN pg_namespace n ON n.oid = t.typnamespace "
            "WHERE t.typtype = 'e' AND n.nspname = ANY(:esquemas)"
        ),
        {"esquemas": ESQUEMAS if destino == "public" else ["public"]},
    ).all()
    movidos = []
    for esquema, tipo in tipos:
        op.execute(f'ALTER TYPE "{esquema}"."{tipo}" SET SCHEMA "{destino}"')
        movidos.append(f"{esquema}.{tipo}")
    return movidos


def upgrade() -> None:
    movidas = []
    for esquema, tablas in REPARTO.items():
        for tabla in tablas:
            actual = _esquema_de_tabla(tabla)
            if actual is None:
                logger.info("No existe la tabla %s: se omite.", tabla)
                continue
            if actual == "public":
                continue
            op.execute(f'ALTER TABLE "{actual}"."{tabla}" SET SCHEMA public')
            movidas.append(f"{actual}.{tabla}")

    tipos = _mover_tipos("public")

    logger.info(
        "Trasladadas a public: %s tabla(s) [%s] y %s tipo(s) enumerado(s) [%s].",
        len(movidas),
        ", ".join(movidas) or "ninguna",
        len(tipos),
        ", ".join(tipos) or "ninguno",
    )

    # RESTRICT y no CASCADE: si quedo algo adentro que esta migracion no
    # previo, que falle y lo diga. Un CASCADE aca se llevaria por delante lo
    # que sea que quedo, sin dejar rastro de que era.
    for esquema in ESQUEMAS:
        op.execute(f'DROP SCHEMA IF EXISTS "{esquema}" RESTRICT')

    logger.info("Esquemas de dominio retirados: %s.", ", ".join(ESQUEMAS))


def downgrade() -> None:
    for esquema in ESQUEMAS:
        op.execute(f'CREATE SCHEMA IF NOT EXISTS "{esquema}"')

    for esquema, tablas in REPARTO.items():
        for tabla in tablas:
            if _esquema_de_tabla(tabla) == "public":
                op.execute(f'ALTER TABLE public."{tabla}" SET SCHEMA "{esquema}"')

    # Los tipos vuelven al esquema donde los pone `create_all`: el primero del
    # search_path. No se reparten por dominio porque nunca lo estuvieron de
    # forma deliberada -- cayeron donde cayeron segun como se armo la base.
    logger.info(
        "Esquemas de dominio repuestos. Los tipos enumerados quedan en public, "
        "que es donde los deja create_all."
    )
