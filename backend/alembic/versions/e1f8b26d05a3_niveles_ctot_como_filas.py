"""los niveles CTOT pasan de 23 columnas a filas de ctot.vuelo_revision

`ctot.vuelo_gestionado` tenía `etd1..etd5`, `ctot1..ctot5`, `sec1..sec5` y
`h_rev1..rev4`: 23 columnas para una colección de niveles de negociación. No
es una violación formal de 1FN -- son escalares --, pero sí una colección
limitada por columnas: cuando un vuelo necesitó un quinto nivel hubo que
hacer una migración con ALTER TABLE (e3f7a91c25b8) en vez de un INSERT, y el
sexto habría necesitado otra. Ahora un nivel más es una fila más.

**El traslado.** El motivo de revisión pertenece al nivel que se ABRE, no al
que se cierra, que es la lectura que ya hacía la grilla:

    nivel 1: etd1, ctot1, sec1                      (sin motivo de revisión)
    nivel 2: etd2, ctot2, sec2 + h_rev1, rev1
    nivel 3: etd3, ctot3, sec3 + h_rev2, rev2
    nivel 4: etd4, ctot4, sec4 + h_rev3, rev3
    nivel 5: etd5, ctot5, sec5 + h_rev4, rev4

El nivel 1 no lleva motivo de revisión porque no hay nivel anterior que lo
justifique. No hay `h_rev5`: no existe un nivel 6 que abrir.

**Se migra un nivel si cualquiera de sus campos tiene contenido**, incluso si
está incompleto -- un nivel con ETD y sin CTOT es una solicitud sin respuesta,
que es un estado real de la operación y no un error de datos. Un nivel con los
cinco campos vacíos no genera fila.

**Las 23 columnas NO se borran.** Quedan como el dato original congelado para
poder comparar la descomposición contra lo que había. Se retiran en una
migración posterior, cuando esa paridad esté verificada contra producción. El
código ya no las lee: `app/models.py` las mapea como `etd1_original`... y
expone `etd1`... como propiedades sobre estas filas.

**`creado_en` y `creado_por_id` quedan nulos en todo lo migrado.** No se puede
saber cuándo ni quién registró una revisión de 2023: atribuirla al último autor
del vuelo, o fecharla en el momento de la migración, sería inventar el dato.
Nulo es la respuesta correcta.

**Los motivos se referencian por código normalizado contra `catalogos.motivo`,
y el texto se conserva.** Los códigos históricos que nunca se dieron de alta
quedan con referencia nula y su texto intacto. El log dice cuáles son: no se
apuntan al motivo "más parecido", porque eso cambiaría el motivo aeronáutico
que alguien registró.

Esta migración recorre `vuelo_gestionado` entero -- 2023 completo incluido --
y escribe hasta cinco filas por vuelo. Es la única de esta serie que mueve
datos en volumen.

Revision ID: e1f8b26d05a3
Revises: d7f1c4a92b38
Create Date: 2026-09-21 00:00:00.000000

"""
import logging
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e1f8b26d05a3"
down_revision: Union[str, Sequence[str], None] = "d7f1c4a92b38"
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


def upgrade() -> None:
    if not _hay_tabla("ctot", "vuelo_revision"):
        op.create_table(
            "vuelo_revision",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("vuelo_gestionado_id", sa.Integer(), nullable=False),
            sa.Column("numero_revision", sa.Integer(), nullable=False),
            sa.Column("etd", sa.String(4), nullable=True),
            sa.Column("ctot", sa.String(4), nullable=True),
            sa.Column("motivo_secuencia", sa.String(16), nullable=True),
            sa.Column("motivo_secuencia_id", sa.Integer(), nullable=True),
            sa.Column("hora_revision", sa.String(4), nullable=True),
            sa.Column("motivo_revision", sa.String(16), nullable=True),
            sa.Column("motivo_revision_id", sa.Integer(), nullable=True),
            sa.Column("creado_en", sa.DateTime(), nullable=True),
            sa.Column("creado_por_id", sa.Integer(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
            sa.ForeignKeyConstraint(
                ["vuelo_gestionado_id"],
                ["ctot.vuelo_gestionado.id"],
                name="fk_vuelo_revision_vuelo",
                ondelete="CASCADE",
            ),
            sa.ForeignKeyConstraint(
                ["motivo_secuencia_id"],
                ["catalogos.motivo.id"],
                name="fk_vuelo_revision_motivo_secuencia",
            ),
            sa.ForeignKeyConstraint(
                ["motivo_revision_id"],
                ["catalogos.motivo.id"],
                name="fk_vuelo_revision_motivo_revision",
            ),
            sa.ForeignKeyConstraint(
                ["creado_por_id"], ["ctot.operador.id"], name="fk_vuelo_revision_operador"
            ),
            sa.UniqueConstraint(
                "vuelo_gestionado_id", "numero_revision", name="uq_vuelo_revision_nivel"
            ),
            schema="ctot",
        )
        op.create_index(
            "ix_vuelo_revision_vuelo_gestionado_id",
            "vuelo_revision",
            ["vuelo_gestionado_id"],
            schema="ctot",
        )
        op.create_index(
            "ix_vuelo_revision_motivo_secuencia_id",
            "vuelo_revision",
            ["motivo_secuencia_id"],
            schema="ctot",
        )
        op.create_index(
            "ix_vuelo_revision_motivo_revision_id",
            "vuelo_revision",
            ["motivo_revision_id"],
            schema="ctot",
        )
        op.create_index(
            "ix_vuelo_revision_creado_por_id",
            "vuelo_revision",
            ["creado_por_id"],
            schema="ctot",
        )

    conn = op.get_bind()

    ya_migrado = conn.execute(sa.text("SELECT count(*) FROM ctot.vuelo_revision")).scalar()
    if ya_migrado:
        logger.info(
            "ctot.vuelo_revision ya tiene %s fila(s): no se vuelve a descomponer.", ya_migrado
        )
        return

    # Un nivel se migra si CUALQUIERA de sus campos tiene contenido. El motivo
    # de revisión del nivel N sale de h_rev{N-1}/rev{N-1}, que es el motivo con
    # el que ese nivel se abrió.
    total = 0
    for nivel in range(1, 6):
        anterior = nivel - 1
        hora_rev = f"h_rev{anterior}" if anterior >= 1 else "NULL"
        motivo_rev = f"rev{anterior}" if anterior >= 1 else "NULL"

        resultado = conn.execute(
            sa.text(
                f"""
                INSERT INTO ctot.vuelo_revision (
                    vuelo_gestionado_id, numero_revision,
                    etd, ctot, motivo_secuencia, hora_revision, motivo_revision
                )
                SELECT
                    v.id, {nivel},
                    nullif(btrim(v.etd{nivel}), ''),
                    nullif(btrim(v.ctot{nivel}), ''),
                    nullif(btrim(v.sec{nivel}), ''),
                    {"nullif(btrim(v." + hora_rev + "), '')" if anterior >= 1 else "NULL"},
                    {"nullif(btrim(v." + motivo_rev + "), '')" if anterior >= 1 else "NULL"}
                FROM ctot.vuelo_gestionado AS v
                WHERE nullif(btrim(v.etd{nivel}), '')  IS NOT NULL
                   OR nullif(btrim(v.ctot{nivel}), '') IS NOT NULL
                   OR nullif(btrim(v.sec{nivel}), '')  IS NOT NULL
                   {"OR nullif(btrim(v." + hora_rev + "), '')  IS NOT NULL" if anterior >= 1 else ""}
                   {"OR nullif(btrim(v." + motivo_rev + "), '') IS NOT NULL" if anterior >= 1 else ""}
                """  # noqa: S608
            )
        )
        creadas = resultado.rowcount or 0
        total += creadas
        logger.info("Nivel %s: %s fila(s) de revision.", nivel, creadas)

    # Referencias al catalogo de motivos, por codigo normalizado. Incluye los
    # inactivos: un motivo retirado sigue explicando los vuelos que lo tienen.
    for columna_texto, columna_ref, categoria in (
        ("motivo_secuencia", "motivo_secuencia_id", "SEC"),
        ("motivo_revision", "motivo_revision_id", "REV"),
    ):
        conn.execute(
            sa.text(
                f"UPDATE ctot.vuelo_revision AS r "  # noqa: S608
                f"SET {columna_ref} = m.id "
                "FROM catalogos.motivo AS m "
                f"WHERE r.{columna_texto} IS NOT NULL "
                f"  AND m.categoria::text = '{categoria}' "
                f"  AND upper(btrim(r.{columna_texto})) = upper(btrim(m.codigo))"
            )
        )
        pendientes = conn.execute(
            sa.text(
                f"SELECT upper(btrim({columna_texto})) AS codigo, count(*) "  # noqa: S608
                "FROM ctot.vuelo_revision "
                f"WHERE {columna_ref} IS NULL AND {columna_texto} IS NOT NULL "
                f"GROUP BY upper(btrim({columna_texto})) ORDER BY count(*) DESC"
            )
        ).all()
        if pendientes:
            logger.warning(
                "%s: codigos que no estan en catalogos.motivo (%s). Se conserva el "
                "texto y la referencia queda nula: no se los apunta al motivo mas "
                "parecido, porque eso cambiaria el motivo aeronautico registrado: %s",
                columna_texto,
                categoria,
                ", ".join(f"{codigo!r} ({filas})" for codigo, filas in pendientes),
            )

    vuelos = conn.execute(
        sa.text("SELECT count(DISTINCT vuelo_gestionado_id) FROM ctot.vuelo_revision")
    ).scalar()
    logger.info(
        "Descomposicion terminada: %s nivel(es) en %s vuelo(s). Las 23 columnas "
        "originales quedan en su lugar para comparar la paridad.",
        total,
        vuelos,
    )


def downgrade() -> None:
    # Las 23 columnas originales nunca se tocaron, asi que volver atras es
    # descartar las filas: el dato de origen sigue intacto donde estaba.
    op.drop_table("vuelo_revision", schema="ctot")
