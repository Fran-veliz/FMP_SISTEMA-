"""renombra el tipo enumerado `direction` a `tipo_movimiento_enum`

La migración anterior (c4e8b7a19d63) tradujo tablas y columnas al español,
pero se olvidó de los TIPOS. La columna pasó a llamarse `tipo_movimiento` y el
tipo enumerado que la respalda se quedó llamándose `direction`, que es como
nació en la base original.

`app/models.py` declara `Enum(Direction, name="tipo_movimiento_enum")`, así que
al insertar, SQLAlchemy escribe el parámetro como `... ::tipo_movimiento_enum`.
Contra una columna que sigue siendo de tipo `direction`, PostgreSQL corta con
`DatatypeMismatch` y se lleva puesta la transacción entera: cargar el
itinerario devolvía 500 en el `commit`, con las filas ya leídas y ninguna
guardada.

Hay además un tipo `tipo_movimiento_enum` suelto, sin ninguna columna que lo
use, creado por el `create_all` de arranque. No es inocente: el usuario de la
base se llama igual que uno de los esquemas de dominio (`ctot`), así que el
`search_path` por omisión -- «"$user", public» -- lo resuelve ANTES que
`public`. Mientras siga en pie, renombrar el tipo bueno no arreglaría nada:
el `::tipo_movimiento_enum` sin calificar seguiría cayendo en el huérfano. Por
eso se lo borra primero.

Las dos operaciones son de catálogo; no recorren las 225.128 filas del
itinerario.

Una base nacida de `create_all` (instalación nueva, ver scripts/init_db.py) ya
tiene el tipo con el nombre bueno y en uso: ahí esta migración no encuentra ni
huérfano que borrar ni `direction` que renombrar, y no hace nada.

Revision ID: d2f7b6c14a80
Revises: c4e8b7a19d63
Create Date: 2026-09-13 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd2f7b6c14a80'
down_revision: Union[str, Sequence[str], None] = 'c4e8b7a19d63'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


VIEJO = "direction"
NUEVO = "tipo_movimiento_enum"


def _tipos_llamados(nombre: str) -> list[tuple[str, int]]:
    """(esquema, oid) de cada tipo con ese nombre, en cualquier esquema.

    No se busca solo en `ctot`: el huérfano aparece en el esquema al que
    apunte `$user`, y el usuario de la base no se llama igual en todas las
    instalaciones.
    """
    filas = op.get_bind().execute(
        sa.text(
            "SELECT n.nspname, t.oid FROM pg_type t "
            "JOIN pg_namespace n ON n.oid = t.typnamespace "
            "WHERE t.typname = :t "
            "AND n.nspname NOT IN ('pg_catalog', 'information_schema')"
        ),
        {"t": nombre},
    )
    return [(fila[0], fila[1]) for fila in filas]


def _lo_usa_alguna_columna(oid: int) -> bool:
    """Si alguna tabla lo usa, no es un huérfano y no se toca.

    Se miran solo las relaciones con columnas propias -- tablas, particionadas,
    vistas, foráneas y tipos compuestos. Los índices quedan fuera a propósito:
    tienen entradas en pg_attribute que heredan el tipo de la columna indexada
    y contarían dos veces lo mismo.
    """
    return bool(
        op.get_bind().execute(
            sa.text(
                "SELECT EXISTS (SELECT 1 FROM pg_attribute a "
                "JOIN pg_class c ON c.oid = a.attrelid "
                "WHERE a.atttypid = :oid AND a.attnum > 0 "
                "AND NOT a.attisdropped "
                "AND c.relkind IN ('r', 'p', 'v', 'm', 'f', 'c'))"
            ),
            {"oid": oid},
        ).scalar()
    )


def _renombrar_tipo(de: str, a: str) -> None:
    """Renombra el `de` de `public` a `a`, si hay algo que renombrar."""
    if not any(esquema == "public" for esquema, _ in _tipos_llamados(de)):
        return  # ya renombrado, o la base nació con el nombre bueno
    if any(esquema == "public" for esquema, _ in _tipos_llamados(a)):
        raise RuntimeError(
            f"public.{de} y public.{a} existen los dos: no se puede decidir "
            "cuál respalda la columna sin mirar la base."
        )
    op.execute(f"ALTER TYPE public.{de} RENAME TO {a}")


def _borrar_huerfanos(nombre: str) -> None:
    for esquema, oid in _tipos_llamados(nombre):
        if esquema == "public" or _lo_usa_alguna_columna(oid):
            continue
        op.execute(f"DROP TYPE {esquema}.{nombre}")


def upgrade() -> None:
    """Upgrade schema."""
    # Un ALTER TYPE necesita ACCESS EXCLUSIVE sobre lo que dependa del tipo.
    # Cinco segundos y un error legible es mejor que encolar detrás de un
    # pg_dump nocturno a todas las consultas que lleguen mientras espera.
    op.execute("SET lock_timeout = '5s'")
    # Ver la nota de alembic/env.py: el usuario de la base se llama igual que
    # un esquema de dominio, así que lo que no se califique iría a parar ahí.
    op.execute("SET search_path TO public")

    # Primero el huérfano: mientras esté, tapa al bueno en el search_path.
    _borrar_huerfanos(NUEVO)
    _renombrar_tipo(VIEJO, NUEVO)


def downgrade() -> None:
    """Downgrade schema.

    Solo se deshace el renombrado. El huérfano no se recrea: no lo usaba
    ninguna columna, volver a dejarlo sería restaurar la avería.
    """
    op.execute("SET lock_timeout = '5s'")
    op.execute("SET search_path TO public")
    _renombrar_tipo(NUEVO, VIEJO)
