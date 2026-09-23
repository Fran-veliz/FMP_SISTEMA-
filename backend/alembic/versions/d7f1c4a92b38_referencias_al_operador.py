"""la identidad del operador pasa a ser su ID, no su nombre escrito

Cinco tablas guardaban el nombre del operador como texto de 64 caracteres:

- `ctot.turno.nombre_operador`
- `ctot.historial_vuelo.nombre_operador`
- `ctot.vuelo_gestionado.actualizado_por`
- `ctot.registro_eliminacion_vuelo.eliminado_por`
- `cargas.lote_carga.cargado_por` y `ctot.importacion_historica.cargado_por`

Son seis copias de una identidad que ya tiene tabla e `id`. Corregir la
grafía de un nombre en `ctot.operador` dejaba las seis desactualizadas y sin
forma de saber a quién se referían. En `turno`, además, la regla "un turno
abierto por persona" se imponía sobre el texto, así que dos grafías del mismo
nombre eran dos personas distintas para la base.

Se agrega la referencia y **se conserva el texto**. No es transición a medias:
el texto es la evidencia de lo que quedó registrado en su momento, y para los
vuelos históricos de 2023 y antes no siempre hay una fila de `operador` a la
que apuntar. Una referencia nula significa "autor legado sin conciliar", no
"nadie".

**El reconciliado compara nombre normalizado -- sin espacios al borde y sin
distinguir mayúsculas -- contra `ctot.operador.usuario`.** Es la misma
comparación que usa el inicio de turno, así que produce las mismas
correspondencias que el sistema ya da por buenas. No se concilia por parecido,
ni por iniciales, ni por apellido: si el texto no coincide con un usuario, la
referencia queda nula y el nombre se conserva tal como está. Atribuirle a una
persona conocida un cambio que hizo otra es peor que no saber quién lo hizo.

El log informa, tabla por tabla, cuántas filas quedaron sin conciliar y con
qué nombres, para poder resolverlas a mano. Los perfiles genéricos (DGAC) y
los nombres de gente que ya no está en la nómina son los casos esperados.

Las columnas nuevas son nulas y los índices se crean sobre ellas: nada de esto
reescribe las tablas ni bloquea la grilla. El UPDATE del reconciliado sí
recorre las filas, pero solo las que tienen nombre.

Revision ID: d7f1c4a92b38
Revises: c5e9d3b81a72
Create Date: 2026-09-21 00:00:00.000000

"""
import logging
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d7f1c4a92b38"
down_revision: Union[str, Sequence[str], None] = "c5e9d3b81a72"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

logger = logging.getLogger("alembic.runtime.migration")

# (esquema, tabla, columna de texto, columna de referencia, nombre de la FK)
REFERENCIAS = [
    ("ctot", "turno", "nombre_operador", "operador_id", "fk_turno_operador"),
    ("ctot", "historial_vuelo", "nombre_operador", "operador_id", "fk_historial_vuelo_operador"),
    ("ctot", "vuelo_gestionado", "actualizado_por", "actualizado_por_id", "fk_vuelo_gestionado_operador"),
    (
        "ctot",
        "registro_eliminacion_vuelo",
        "eliminado_por",
        "eliminado_por_id",
        "fk_registro_eliminacion_operador",
    ),
    ("cargas", "lote_carga", "cargado_por", "cargado_por_id", "fk_lote_carga_operador"),
    (
        "ctot",
        "importacion_historica",
        "cargado_por",
        "cargado_por_id",
        "fk_importacion_historica_operador",
    ),
]


def _columnas(esquema: str, tabla: str) -> set[str]:
    filas = op.get_bind().execute(
        sa.text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = :e AND table_name = :t"
        ),
        {"e": esquema, "t": tabla},
    )
    return {fila[0] for fila in filas}


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


def upgrade() -> None:
    conn = op.get_bind()

    for esquema, tabla, col_texto, col_ref, nombre_fk in REFERENCIAS:
        if not _hay_tabla(esquema, tabla):
            logger.info("No existe %s.%s: se omite.", esquema, tabla)
            continue

        columnas = _columnas(esquema, tabla)
        if col_ref not in columnas:
            op.add_column(
                tabla, sa.Column(col_ref, sa.Integer(), nullable=True), schema=esquema
            )
            op.create_foreign_key(
                nombre_fk, tabla, "operador", [col_ref], ["id"],
                source_schema=esquema, referent_schema="ctot",
            )
            op.create_index(
                f"ix_{tabla}_{col_ref}", tabla, [col_ref], schema=esquema
            )

        if col_texto not in columnas:
            logger.info("%s.%s no tiene %s: no hay nada que conciliar.", esquema, tabla, col_texto)
            continue

        # Reconciliado: la misma comparación normalizada que usa el inicio de
        # turno. Solo toca filas con nombre y sin referencia.
        resultado = conn.execute(
            sa.text(
                f"UPDATE {esquema}.{tabla} AS t "  # noqa: S608
                "SET " + col_ref + " = o.id "
                "FROM ctot.operador AS o "
                "WHERE t." + col_ref + " IS NULL "
                "  AND t." + col_texto + " IS NOT NULL "
                "  AND lower(btrim(t." + col_texto + ")) = lower(btrim(o.usuario))"
            )
        )
        conciliadas = resultado.rowcount or 0

        sin_conciliar = conn.execute(
            sa.text(
                f"SELECT btrim({col_texto}) AS nombre, count(*) AS filas "  # noqa: S608
                f"FROM {esquema}.{tabla} "
                f"WHERE {col_ref} IS NULL AND {col_texto} IS NOT NULL "
                f"GROUP BY btrim({col_texto}) ORDER BY count(*) DESC"
            )
        ).all()

        if sin_conciliar:
            logger.warning(
                "%s.%s: %s fila(s) conciliadas. Quedan sin conciliar, por nombre: %s. "
                "Se conserva el texto y la referencia queda nula: son perfiles que ya "
                "no estan en la nomina o autores genericos. No se les asigna una "
                "persona por parecido.",
                esquema,
                tabla,
                conciliadas,
                ", ".join(f"{nombre!r} ({filas})" for nombre, filas in sin_conciliar),
            )
        else:
            logger.info(
                "%s.%s: %s fila(s) conciliadas, ninguna quedo pendiente.",
                esquema,
                tabla,
                conciliadas,
            )

    # La regla "un turno abierto por persona" pasa a imponerse sobre la
    # referencia, ADEMAS del indice que ya existe sobre el texto. Los dos
    # conviven durante la transicion: el viejo sigue cubriendo los turnos sin
    # conciliar, el nuevo cubre todo lo que se abra de aca en adelante.
    if _hay_tabla("ctot", "turno"):
        op.create_index(
            "uq_turno_abierto_por_operador_id",
            "turno",
            ["operador_id"],
            unique=True,
            postgresql_where=sa.text("finalizado_en IS NULL AND operador_id IS NOT NULL"),
            schema="ctot",
        )


def downgrade() -> None:
    # IF EXISTS en los indices: el downgrade de f3a8c41e7d92, que corre antes
    # que este, vuelve a partir `importacion` en `lote_carga` e
    # `importacion_historica`, pero recrea las columnas sin sus indices. Al
    # llegar aca, `cargado_por_id` esta y `ix_lote_carga_cargado_por_id` no, y
    # el drop moria. La columna sigue guardada por `_columnas`, que es la
    # comprobacion que importa; el indice es accesorio y deshacerlo dos veces
    # tiene que dar lo mismo.
    if _hay_tabla("ctot", "turno"):
        op.execute('DROP INDEX IF EXISTS ctot.uq_turno_abierto_por_operador_id')

    for esquema, tabla, _col_texto, col_ref, nombre_fk in REFERENCIAS:
        if not _hay_tabla(esquema, tabla):
            continue
        if col_ref in _columnas(esquema, tabla):
            op.execute(f'DROP INDEX IF EXISTS "{esquema}"."ix_{tabla}_{col_ref}"')
            op.drop_constraint(nombre_fk, tabla, schema=esquema, type_="foreignkey")
            op.drop_column(tabla, col_ref, schema=esquema)
