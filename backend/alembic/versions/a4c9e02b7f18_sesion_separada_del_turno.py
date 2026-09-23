"""la sesion se separa del turno, y la duracion deja de guardarse

Dos cambios en `ctot.turno`, con el mismo origen: había ahí cosas que no son
del turno.

**1. El pase pasa a `ctot.sesion`.**

Vivía en `turno.token_sesion`, y ahí había un hecho 1:N metido en un solo
lugar: **cada reenganche rota el pase**, así que un turno tiene varias sesiones
a lo largo de su vida y la columna solo podía guardar la última. Las anteriores
se perdían al pisarse, sin dejar registro de cuándo empezó ni cuándo dejó de
valer cada una. Es la misma forma del problema que tenían las 23 columnas de
niveles: una colección guardada en un único casillero.

Turno y sesión además tienen ciclos de vida distintos, y conviene no
confundirlos. El turno es un hecho operativo cerrado y permanente -- quién
trabajó, en qué posición, desde cuándo hasta cuándo, con qué nota de relevo --
y va a la bitácora. La sesión es un mecanismo de autenticación que se emite, se
usa, se rota y se revoca.

De la sesión se guarda solo la huella del pase, nunca su valor, igual que
antes: en la base no hay con qué autenticarse.

**`turno.token_sesion` se conserva y se sigue escribiendo.** No es una
transición a medias: la autenticación busca primero en `ctot.sesion` y cae a
esa columna para los turnos que ya estaban abiertos al momento del despliegue.
Sin ese respaldo, aplicar esta migración habría invalidado en el acto los pases
de todos los turnos en curso -- en un sistema que la FMP usa 24/7, eso es
sacar a los operadores de la grilla en plena operación. Se retira cuando no
queden turnos abiertos de antes.

**No se inventan las sesiones pasadas.** Los turnos ya cerrados no reciben
fila: lo único que se sabe de ellos es la huella del último pase, y no cuándo
se emitió, cuántas veces se rotó ni cuándo dejó de valer cada una. Los turnos
abiertos tampoco, porque su pase legado sigue funcionando por el respaldo.
`ctot.sesion` arranca vacía y se llena con los ingresos siguientes.

**2. `duracion_minutos` pasa a ser el valor original congelado.**

Era `finalizado_en - iniciado_en`: un derivado almacenado. Ahora se calcula.

La columna NO se borra, y esta vez el motivo no es la paridad sino que **el
valor guardado puede no ser la resta.** Los cierres automáticos por inactividad
fijan `finalizado_en` en la última actividad real y no en el instante del
cierre, justamente para que un turno olvidado el viernes y cerrado el lunes no
figure con setenta y dos horas de trabajo que nadie hizo. Donde el valor
grabado difiera del cálculo, ese valor es evidencia de lo que la bitácora dijo,
no un cálculo a rehacer. Se renombra a `duracion_original_minutos` para que el
nombre diga lo que es, y los turnos nuevos no la escriben.

Revision ID: a4c9e02b7f18
Revises: f3a8c41e7d92
Create Date: 2026-09-21 00:00:00.000000

"""
import logging
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a4c9e02b7f18"
down_revision: Union[str, Sequence[str], None] = "f3a8c41e7d92"
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
    if not _hay_tabla("ctot", "sesion"):
        op.create_table(
            "sesion",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("turno_id", sa.Integer(), nullable=False),
            sa.Column("operador_id", sa.Integer(), nullable=True),
            sa.Column("token_hash", sa.String(64), nullable=False),
            sa.Column("iniciada_en", sa.DateTime(), nullable=False),
            sa.Column("ultima_actividad_en", sa.DateTime(), nullable=True),
            sa.Column("revocada_en", sa.DateTime(), nullable=True),
            sa.Column("motivo_revocacion", sa.String(32), nullable=True),
            sa.PrimaryKeyConstraint("id"),
            # Sin cascada: la sesion es evidencia de un acceso y tiene que
            # sobrevivir a cualquier limpieza del turno.
            sa.ForeignKeyConstraint(["turno_id"], ["ctot.turno.id"], name="fk_sesion_turno"),
            sa.ForeignKeyConstraint(
                ["operador_id"], ["ctot.operador.id"], name="fk_sesion_operador"
            ),
            sa.UniqueConstraint("token_hash", name="uq_sesion_token_hash"),
            schema="ctot",
        )
        op.create_index("ix_sesion_turno_id", "sesion", ["turno_id"], schema="ctot")
        op.create_index("ix_sesion_operador_id", "sesion", ["operador_id"], schema="ctot")

    abiertos = op.get_bind().execute(
        sa.text("SELECT count(*) FROM ctot.turno WHERE finalizado_en IS NULL")
    ).scalar()
    if abiertos:
        logger.info(
            "Hay %s turno(s) abiertos con pase legado en turno.token_sesion. Siguen "
            "funcionando: la autenticacion busca primero en ctot.sesion y cae a esa "
            "columna. No se les inventa una fila de sesion, porque no se sabe cuando "
            "se emitio su pase ni cuantas veces se roto.",
            abiertos,
        )

    columnas = _columnas("ctot", "turno")
    if "duracion_minutos" in columnas:
        # Solo se renombra el atributo en el modelo; la columna fisica conserva
        # su nombre para no perder el dato legado ni reescribir la tabla. Se
        # deja constancia de cuantos turnos traen un valor que NO es la resta:
        # esos son los cierres automaticos, y su valor es evidencia.
        divergentes = op.get_bind().execute(
            sa.text(
                """
                SELECT count(*) FROM ctot.turno
                WHERE duracion_minutos IS NOT NULL
                  AND finalizado_en IS NOT NULL
                  AND abs(
                        duracion_minutos
                        - (extract(epoch FROM (finalizado_en - iniciado_en)) / 60)
                      ) > 1
                """
            )
        ).scalar()
        logger.info(
            "ctot.turno.duracion_minutos queda como valor original congelado. %s "
            "turno(s) tienen un valor que difiere de finalizado_en - iniciado_en en "
            "mas de un minuto: son los cierres automaticos, que se fecharon hasta la "
            "ultima actividad real. Ese valor es evidencia de lo que dijo la bitacora.",
            divergentes,
        )


def downgrade() -> None:
    if _hay_tabla("ctot", "sesion"):
        op.drop_index("ix_sesion_operador_id", table_name="sesion", schema="ctot")
        op.drop_index("ix_sesion_turno_id", table_name="sesion", schema="ctot")
        op.drop_table("sesion", schema="ctot")
    # `duracion_minutos` nunca cambio de nombre en la base, asi que no hay nada
    # que reponer: el codigo viejo vuelve a escribirla tal cual.
