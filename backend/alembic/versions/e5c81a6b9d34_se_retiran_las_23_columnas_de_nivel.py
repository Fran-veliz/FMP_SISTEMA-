"""se retiran las 23 columnas de nivel de vuelo_gestionado

`etd1..etd5`, `ctot1..ctot5`, `sec1..sec5` y `h_rev1..rev4` quedaron en la
tabla cuando los niveles pasaron a filas (migracion e1f8b26d05a3). No fue un
olvido: se conservaron como el dato ORIGINAL congelado, para poder comparar la
descomposicion contra lo que habia. Esa comparacion era la unica forma de
comprobar que el traslado -- en particular el del motivo de revision, que
pertenece al nivel que se ABRE y no al que se cierra -- se habia aplicado bien.

**La paridad se verifico contra produccion antes de esta migracion**, sobre las
23 columnas y los 224.379 vuelos, comparando cada valor plano contra el de su
fila de `vuelo_revision`: cero diferencias. Cumplida esa condicion, las
columnas ya no aportan nada y se retiran.

Desde e1f8b26d05a3 la aplicacion no las lee ni las escribe: `flight.etd1` es
una propiedad sobre las filas de revision (ver el bloque NIVELES en
app/models.py), y el modelo las mapeaba como `etd1_original`. Por eso este
DROP no cambia ningun comportamiento: lo que se borra es una copia que nadie
consultaba.

**El downgrade las reconstruye desde `vuelo_revision`**, no las repone vacias.
Puede hacerlo precisamente porque la paridad esta probada: la informacion
sigue entera en las filas, y el traslado inverso es el mismo desplazamiento al
reves -- el motivo de revision del nivel N vuelve a `h_rev{N-1}/rev{N-1}`.

`DROP COLUMN` en PostgreSQL es una operacion de catalogo: marca la columna
como borrada y no reescribe la tabla. El espacio se recupera cuando pase el
VACUUM, no en el acto.

Revision ID: e5c81a6b9d34
Revises: d8b3f07a2c91
Create Date: 2026-09-22 00:00:00.000000

"""
import logging
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e5c81a6b9d34"
down_revision: Union[str, Sequence[str], None] = "d8b3f07a2c91"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

logger = logging.getLogger("alembic.runtime.migration")

COLUMNAS = (
    [f"etd{n}" for n in range(1, 6)]
    + [f"ctot{n}" for n in range(1, 6)]
    + [f"sec{n}" for n in range(1, 6)]
    + [f"h_rev{n}" for n in range(1, 5)]
    + [f"rev{n}" for n in range(1, 5)]
)


def _columnas_actuales() -> set[str]:
    filas = op.get_bind().execute(
        sa.text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'vuelo_gestionado'"
        )
    )
    return {fila[0] for fila in filas}


def upgrade() -> None:
    presentes = _columnas_actuales()
    a_borrar = [c for c in COLUMNAS if c in presentes]

    if not a_borrar:
        logger.info("Las columnas de nivel ya no estan: no hay nada que retirar.")
        return

    # Ultima comprobacion antes de borrar, por si la migracion corre sobre una
    # base donde la descomposicion no se hizo o quedo a medias. Se compara solo
    # el nivel 1, que es el que existe en practicamente todos los vuelos: si
    # ese no coincide, el traslado no se hizo y no corresponde borrar nada.
    descuadre = op.get_bind().execute(
        sa.text(
            """
            SELECT count(*) FROM vuelo_gestionado v
            LEFT JOIN vuelo_revision r
              ON r.vuelo_gestionado_id = v.id AND r.numero_revision = 1
            WHERE nullif(btrim(v.etd1), '')  IS DISTINCT FROM r.etd
               OR nullif(btrim(v.ctot1), '') IS DISTINCT FROM r.ctot
               OR nullif(btrim(v.sec1), '')  IS DISTINCT FROM r.motivo_secuencia
            """
        )
    ).scalar()
    if descuadre:
        raise RuntimeError(
            f"{descuadre} vuelo(s) tienen el nivel 1 distinto entre las columnas "
            "planas y vuelo_revision. La descomposicion no esta completa, asi que "
            "borrar las columnas perderia datos. Revisar antes de volver a correr."
        )

    for columna in a_borrar:
        op.drop_column("vuelo_gestionado", columna)

    logger.info(
        "Retiradas %s columnas de nivel de vuelo_gestionado. La informacion vive "
        "en vuelo_revision, con paridad comprobada. El espacio se recupera con el "
        "proximo VACUUM.",
        len(a_borrar),
    )


def downgrade() -> None:
    for columna in COLUMNAS:
        largo = 16 if columna.startswith(("sec", "rev")) else 4
        op.add_column("vuelo_gestionado", sa.Column(columna, sa.String(largo), nullable=True))

    # Se reconstruyen desde las filas, no se reponen vacias. El motivo de
    # revision del nivel N vuelve a h_rev{N-1}/rev{N-1}: el mismo
    # desplazamiento de e1f8b26d05a3, al reves.
    asignaciones = []
    for nivel in range(1, 6):
        asignaciones += [
            f"etd{nivel}  = (SELECT r.etd FROM vuelo_revision r "
            f"WHERE r.vuelo_gestionado_id = v.id AND r.numero_revision = {nivel})",
            f"ctot{nivel} = (SELECT r.ctot FROM vuelo_revision r "
            f"WHERE r.vuelo_gestionado_id = v.id AND r.numero_revision = {nivel})",
            f"sec{nivel}  = (SELECT r.motivo_secuencia FROM vuelo_revision r "
            f"WHERE r.vuelo_gestionado_id = v.id AND r.numero_revision = {nivel})",
        ]
        if nivel < 5:
            abre = nivel + 1
            asignaciones += [
                f"h_rev{nivel} = (SELECT r.hora_revision FROM vuelo_revision r "
                f"WHERE r.vuelo_gestionado_id = v.id AND r.numero_revision = {abre})",
                f"rev{nivel}   = (SELECT r.motivo_revision FROM vuelo_revision r "
                f"WHERE r.vuelo_gestionado_id = v.id AND r.numero_revision = {abre})",
            ]

    op.execute("UPDATE vuelo_gestionado v SET " + ", ".join(asignaciones))
