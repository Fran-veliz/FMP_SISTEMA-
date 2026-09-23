"""el catalogo de motivos pasa a `catalogos` y gana integridad

`ctot.motivo` es un catálogo, como `catalogos.aeropuerto` y
`catalogos.codigo_aeropuerto`, y no un dato de la gestión de un vuelo. Se
mueve al esquema que le corresponde.

Además tenía tres huecos:

1. **Sin unicidad `(categoria, codigo)`.** Nada impedía dos filas
   `SEC / DEMORA`. El router consultaba antes de insertar, pero entre la
   consulta y el INSERT hay un instante, y un INSERT directo por psql no
   pasaba por ese control.
2. **`codigo` de 24 caracteres contra 16 en el uso.** El vuelo guarda el
   motivo en `sec1..sec5` y `rev1..rev4`, que son `varchar(16)`. Se podía dar
   de alta un motivo de 20 caracteres, el alta respondía 200, y después el
   motivo no entraba donde hay que usarlo.
3. **Sin forma de retirar un motivo.** Solo quedaba borrarlo, y los vuelos ya
   gestionados guardan el código en texto: borrar la fila dejaba un código sin
   explicación. Ahora se desactiva.

Orden de las operaciones, que importa: primero se deduplica, porque la
unicidad no se puede crear sobre filas repetidas; después se comprueba el
largo, porque acortar el tipo sobre un valor más largo corta el dato.

**La deduplicación conserva el `id` más bajo de cada grupo y borra el resto.**
Es seguro porque nada referencia `motivo.id`: el vuelo guarda el código en
texto y no hay ninguna FK hacia esta tabla (eso llega con `vuelo_revision`).
Quitar una fila repetida solo deja de ofrecer el mismo código dos veces en la
lista. Las descripciones de las filas borradas se registran en el log, por si
alguna decía algo que la conservada no dice.

**Si algún código supera los 16 caracteres, la migración se detiene** en vez
de truncarlo. Un código truncado es un motivo aeronáutico distinto del que
alguien escribió, y no corresponde que una migración lo decida. Ese dato ya
está roto hoy: no puede guardarse en ningún vuelo. El mensaje dice qué filas
hay que corregir.

Revision ID: b8c4f2a71e05
Revises: a3d7e1b09c46
Create Date: 2026-09-21 00:00:00.000000

"""
import logging
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b8c4f2a71e05"
down_revision: Union[str, Sequence[str], None] = "a3d7e1b09c46"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

logger = logging.getLogger("alembic.runtime.migration")


def _esquema_de(tabla: str) -> str | None:
    fila = op.get_bind().execute(
        sa.text(
            "SELECT table_schema FROM information_schema.tables "
            "WHERE table_name = :t AND table_schema IN ('ctot', 'catalogos')"
        ),
        {"t": tabla},
    ).first()
    return fila[0] if fila else None


def _columnas(esquema: str, tabla: str) -> set[str]:
    filas = op.get_bind().execute(
        sa.text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = :e AND table_name = :t"
        ),
        {"e": esquema, "t": tabla},
    )
    return {fila[0] for fila in filas}


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
    origen = _esquema_de("motivo")
    if origen is None:
        logger.info("No existe la tabla motivo: nada que hacer.")
        return

    conn = op.get_bind()

    # 1. Deduplicar, o la unicidad de abajo no se puede crear.
    repetidos = conn.execute(
        sa.text(
            f"SELECT categoria::text, codigo, count(*) FROM {origen}.motivo "  # noqa: S608
            "GROUP BY categoria, codigo HAVING count(*) > 1"
        )
    ).all()
    if repetidos:
        for categoria, codigo, veces in repetidos:
            descartadas = conn.execute(
                sa.text(
                    f"SELECT id, descripcion FROM {origen}.motivo "  # noqa: S608
                    "WHERE categoria::text = :cat AND codigo = :cod "
                    "AND id > (SELECT min(id) FROM " + origen + ".motivo "
                    "WHERE categoria::text = :cat AND codigo = :cod)"
                ),
                {"cat": categoria, "cod": codigo},
            ).all()
            logger.warning(
                "Motivo repetido %s/%s (%s veces). Se conserva el id mas bajo y se "
                "descartan: %s",
                categoria,
                codigo,
                veces,
                ", ".join(f"id={i} descripcion={d!r}" for i, d in descartadas),
            )
        conn.execute(
            sa.text(
                f"DELETE FROM {origen}.motivo WHERE id NOT IN ("  # noqa: S608
                f"SELECT min(id) FROM {origen}.motivo GROUP BY categoria, codigo)"
            )
        )

    # 2. Comprobar el largo ANTES de acortar el tipo: truncar un codigo
    #    aeronautico lo convierte en otro motivo.
    largos = conn.execute(
        sa.text(
            f"SELECT id, categoria::text, codigo FROM {origen}.motivo "  # noqa: S608
            "WHERE length(codigo) > 16"
        )
    ).all()
    if largos:
        detalle = ", ".join(f"id={i} {c}/{cod!r} ({len(cod)} caracteres)" for i, c, cod in largos)
        raise RuntimeError(
            "Hay motivos con codigo de mas de 16 caracteres, que es el largo con el "
            "que el motivo se guarda en el vuelo (sec1..sec5, rev1..rev4). Esos "
            "codigos no se pueden usar hoy en ningun vuelo. Corregilos a mano y "
            f"volve a correr la migracion: {detalle}"
        )

    # 3. Mover al esquema que le corresponde.
    if origen != "catalogos":
        op.execute("ALTER TABLE ctot.motivo SET SCHEMA catalogos")

    # 4. La columna para retirar un motivo sin borrarlo.
    if "activo" not in _columnas("catalogos", "motivo"):
        op.add_column(
            "motivo",
            sa.Column("activo", sa.Boolean(), nullable=False, server_default=sa.true()),
            schema="catalogos",
        )

    # 5. Acortar el tipo al largo del uso. En Postgres, reducir la longitud de
    #    un varchar revalida los valores; ya se comprobo que todos entran.
    op.alter_column(
        "motivo",
        "codigo",
        existing_type=sa.String(24),
        type_=sa.String(16),
        existing_nullable=False,
        schema="catalogos",
    )

    # 6. Y recien ahora la unicidad, sobre datos ya conciliados.
    if not _hay_restriccion("catalogos", "motivo", "uq_motivo_categoria_codigo"):
        op.create_unique_constraint(
            "uq_motivo_categoria_codigo", "motivo", ["categoria", "codigo"], schema="catalogos"
        )


def downgrade() -> None:
    if _hay_restriccion("catalogos", "motivo", "uq_motivo_categoria_codigo"):
        op.drop_constraint("uq_motivo_categoria_codigo", "motivo", schema="catalogos", type_="unique")

    op.alter_column(
        "motivo",
        "codigo",
        existing_type=sa.String(16),
        type_=sa.String(24),
        existing_nullable=False,
        schema="catalogos",
    )

    if "activo" in _columnas("catalogos", "motivo"):
        op.drop_column("motivo", "activo", schema="catalogos")

    # Las filas repetidas que se borraron no vuelven: el downgrade repone la
    # estructura, no los datos descartados.
    op.execute("ALTER TABLE catalogos.motivo SET SCHEMA ctot")
