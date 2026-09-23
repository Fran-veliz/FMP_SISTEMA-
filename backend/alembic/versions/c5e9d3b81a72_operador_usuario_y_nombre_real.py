"""separa el acceso del nombre real en ctot.operador

`ctot.operador.nombre` cumplía dos papeles a la vez: era el identificador de
acceso -- lo que la persona teclea para iniciar turno -- y también lo que se
mostraba. Por eso era único, y por eso corregir la grafía de un nombre
cambiaba con qué se entra al sistema y dejaba desalineadas las cinco copias de
texto que guardan turno, historial, autorías de vuelo, cargas y eliminaciones.

Se separan:

- `nombre` pasa a `usuario`: sigue siendo único y sigue siendo la credencial.
- `nombre_completo` es nuevo y opcional: el nombre real de la persona, para
  mostrar. Puede repetirse (dos homónimos son dos cuentas distintas) y se
  puede corregir sin tocar el acceso ni las autorías ya registradas.
- `activo` es nuevo: una persona que deja la FMP se desactiva en vez de
  borrarse, porque sus turnos, cambios y cargas siguen en la bitácora y tienen
  que poder atribuirse. Un perfil inactivo no puede iniciar turno.

`nombre_completo` queda NULO para los perfiles existentes, a propósito: no se
puede deducir el nombre real de una persona a partir de su usuario, y
rellenarlo con el usuario haría pasar por nombre real algo que no lo es. Se
completa a mano, perfil por perfil.

Se renombra también la restricción de unicidad que Postgres nombró por la
columna (`operador_nombre_key`), para que no siga nombrando una columna que ya
no existe.

Nada de esto toca las copias de texto que ya están en turno, historial y
autorías. Esas se convierten en FK en la migración siguiente, que es donde hay
que conciliar los nombres legados.

Revision ID: c5e9d3b81a72
Revises: b8c4f2a71e05
Create Date: 2026-09-21 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c5e9d3b81a72"
down_revision: Union[str, Sequence[str], None] = "b8c4f2a71e05"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


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
    columnas = _columnas("ctot", "operador")

    if "nombre" in columnas and "usuario" not in columnas:
        op.alter_column("operador", "nombre", new_column_name="usuario", schema="ctot")

    if _hay_restriccion("ctot", "operador", "operador_nombre_key"):
        op.execute(
            "ALTER TABLE ctot.operador "
            "RENAME CONSTRAINT operador_nombre_key TO operador_usuario_key"
        )

    if "nombre_completo" not in columnas:
        op.add_column(
            "operador",
            sa.Column("nombre_completo", sa.String(120), nullable=True),
            schema="ctot",
        )

    if "activo" not in columnas:
        op.add_column(
            "operador",
            sa.Column("activo", sa.Boolean(), nullable=False, server_default=sa.true()),
            schema="ctot",
        )


def downgrade() -> None:
    columnas = _columnas("ctot", "operador")

    if "activo" in columnas:
        op.drop_column("operador", "activo", schema="ctot")

    # Se pierden los nombres reales que se hayan completado a mano: el
    # downgrade repone la estructura anterior, que no tenia donde guardarlos.
    if "nombre_completo" in columnas:
        op.drop_column("operador", "nombre_completo", schema="ctot")

    if _hay_restriccion("ctot", "operador", "operador_usuario_key"):
        op.execute(
            "ALTER TABLE ctot.operador "
            "RENAME CONSTRAINT operador_usuario_key TO operador_nombre_key"
        )

    if "usuario" in columnas and "nombre" not in columnas:
        op.alter_column("operador", "usuario", new_column_name="nombre", schema="ctot")
