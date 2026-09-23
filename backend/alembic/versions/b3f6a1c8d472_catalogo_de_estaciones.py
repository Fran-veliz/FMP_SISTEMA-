"""catálogo de estaciones y su columna en el itinerario

El modelo (app/models.py) declaraba la tabla `estaciones` y la columna
`estacion` en `itinerary_entries` e `itinerary_uploads`, pero ninguna
migración las creaba. Eso no se notaba en una instalación nueva porque
scripts/init_db.py crea el esquema con create_all y marca la cadena al día
(`stamp head`): ahí el modelo se aplica entero y la base queda bien.

El que se rompía era el otro camino, el de la base que YA está versionada --
es decir la de producción, y la que se restaura desde un respaldo en el
servidor: ahí init_db corre `alembic upgrade head`, la cadena no crea nada de
esto, y el esquema real queda sin las tres cosas. Cualquier consulta al
itinerario falla, porque todas filtran por `estacion` desde que entró Cusco.

Orden: primero el catálogo con sus filas, recién después las columnas que lo
referencian por clave foránea.

Las columnas entran directamente como NOT NULL con un DEFAULT constante. No es
un atajo: desde Postgres 11 eso NO reescribe la tabla -- el valor queda
guardado como metadato y las filas existentes lo devuelven sin haberse tocado.
El DEFAULT se quita enseguida, porque el modelo no lo declara y de ahí en más
la aplicación siempre escribe la estación explícitamente.

Medido sobre una copia de la base real (808.549 filas de itinerario): así
tarda 7 ms. Haciéndolo del modo evidente --columna nula, UPDATE de relleno y
después NOT NULL-- ese mismo paso tardaba 49 segundos, con la tabla bloqueada
todo ese rato. El contenedor encadena `init_db.py && uvicorn`, así que ese
tiempo es la API sin atender durante el despliegue, en un sistema que opera
24/7.

Todo lo que ya está cargado es de Lima: el sistema fue mono-aeródromo hasta
2026, así que SPJC es el relleno correcto para el histórico, no una
suposición de conveniencia.

Revision ID: b3f6a1c8d472
Revises: a2e9f1c7d340
Create Date: 2026-09-10 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b3f6a1c8d472'
down_revision: Union[str, Sequence[str], None] = 'a2e9f1c7d340'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Estación con la que se rellena todo lo ya cargado. Se escribe acá y no se
# importa de app.seed_data a propósito: una migración describe un momento del
# esquema y no puede cambiar de significado porque alguien edite el catálogo
# meses después.
ESTACION_HISTORICA = "SPJC"


def upgrade() -> None:
    """Upgrade schema."""
    # El search_path por omisión es «"$user", public» y el usuario de la base se
    # llama `ctot`. En cuanto existe un esquema con ese mismo nombre --lo crea la
    # migración siguiente-- cada CREATE TABLE sin calificar aterriza en `ctot` en
    # vez de `public`, sin error y sin aviso. Acá se fija explícitamente para que
    # esta migración haga lo mismo la corra quien la corra y esté como esté la
    # base.
    op.execute("SET search_path TO public")
    # Un ALTER necesita ACCESS EXCLUSIVE. Si algo está tocando la tabla -- un
    # pg_dump nocturno la retiene toda su duración -- el ALTER espera, y mientras
    # espera encola detrás de sí a todas las consultas nuevas, aunque sean
    # lecturas. Mejor cortar en cinco segundos con un error legible que dejar la
    # base entera parada sin explicación.
    op.execute("SET lock_timeout = '5s'")

    # 1) El catálogo, antes que nada: las claves foráneas de abajo lo necesitan.
    op.create_table(
        "estaciones",
        sa.Column("codigo_oaci", sa.String(length=4), nullable=False),
        sa.Column("nombre", sa.String(length=100), nullable=False),
        sa.Column("capacidad_declarada", sa.Integer(), nullable=True),
        # Con server_default, sumar un aeródromo es el INSERT que promete el
        # modelo ("sumar una región es un INSERT"): sin él, un alta a mano desde
        # psql que no nombre `activa` falla por NOT NULL.
        sa.Column("activa", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.PrimaryKeyConstraint("codigo_oaci"),
    )

    # 2) Las dos estaciones que el sistema conoce hoy. seed_data.seed() las
    #    vuelve a insertar al arrancar si faltan, pero no puede correr antes
    #    que esta migración: la clave foránea de más abajo exige que la fila
    #    de SPJC exista ya mismo.
    #
    #    Cusco no tiene cifra de capacidad publicada por la DGAC, así que va
    #    en NULL y /forecast cae al valor por omisión en vez de dibujar una
    #    línea inventada.
    op.bulk_insert(
        sa.table(
            "estaciones",
            sa.column("codigo_oaci", sa.String),
            sa.column("nombre", sa.String),
            sa.column("capacidad_declarada", sa.Integer),
            sa.column("activa", sa.Boolean),
        ),
        [
            {"codigo_oaci": "SPJC", "nombre": "LIMA - JORGE CHÁVEZ",
             "capacidad_declarada": 49, "activa": True},
            {"codigo_oaci": "SPZO", "nombre": "CUSCO - ALEJANDRO VELASCO ASTETE",
             "capacidad_declarada": None, "activa": True},
        ],
    )

    # 3) itinerary_entries.estacion. El DEFAULT hace que las 808.549 filas ya
    #    cargadas queden con valor sin reescribir la tabla; se quita acto
    #    seguido para que el esquema diga lo mismo que el modelo.
    op.add_column(
        "itinerary_entries",
        sa.Column("estacion", sa.String(length=4), nullable=False,
                  server_default=ESTACION_HISTORICA),
    )
    op.execute("ALTER TABLE itinerary_entries ALTER COLUMN estacion DROP DEFAULT")
    op.create_foreign_key(
        "fk_itinerary_entries_estacion", "itinerary_entries", "estaciones",
        ["estacion"], ["codigo_oaci"],
    )
    # Sin índice propio sobre `estacion`: dos valores posibles y hoy todas las
    # filas con el mismo, así que el planificador no lo elegiría nunca. La
    # comprobación de integridad referencial tampoco lo necesita --
    # ix_itinerary_lookup ya lleva la estación como primera columna y sirve para
    # eso. Lo que sí costaría es una entrada de índice más en cada INSERT, y las
    # cargas de itinerario entran de a miles de filas.

    # 4) Los dos índices del itinerario se rehacen con la estación adelante.
    #    Sin ella, la búsqueda de un call sign sigue mirando el itinerario de
    #    todos los aeródromos y la unicidad sigue confundiendo el mismo vuelo
    #    de dos estaciones distintas.
    op.drop_index("ix_itinerary_lookup", table_name="itinerary_entries")
    op.create_index(
        "ix_itinerary_lookup", "itinerary_entries",
        ["estacion", "flight_date", "call_sign", "direction"],
    )

    #    Agregar una columna a un índice único solo lo vuelve MENOS restrictivo,
    #    y todas las filas quedaron con la misma estación, así que ninguna
    #    combinación que hoy es válida pasa a chocar.
    op.drop_index("uq_itinerary_dedup", table_name="itinerary_entries")
    op.execute(
        """
        CREATE UNIQUE INDEX uq_itinerary_dedup ON itinerary_entries
            (estacion, flight_date, effective_from, call_sign, direction,
             hora_utc, aerodromo, tipo_aeronave, tipo_servicio)
            NULLS NOT DISTINCT
        """
    )

    # 5) itinerary_uploads.estacion -- mismo procedimiento. Son 35 filas y el
    #    costo daría igual, pero conviene que las dos digan lo mismo.
    op.add_column(
        "itinerary_uploads",
        sa.Column("estacion", sa.String(length=4), nullable=False,
                  server_default=ESTACION_HISTORICA),
    )
    op.execute("ALTER TABLE itinerary_uploads ALTER COLUMN estacion DROP DEFAULT")
    op.create_foreign_key(
        "fk_itinerary_uploads_estacion", "itinerary_uploads", "estaciones",
        ["estacion"], ["codigo_oaci"],
    )


def downgrade() -> None:
    """Downgrade schema.

    Vuelve a un itinerario sin estación, que solo tiene sentido mientras haya
    un único aeródromo cargado. Con dos, esto no se puede deshacer: el índice
    único de abajo no lleva la estación, y el mismo indicativo del mismo día en
    Lima y en Cusco -- que es el caso exacto que motivó toda esta migración --
    choca. Se comprueba antes y se corta con un mensaje, en vez de fallar a
    mitad con un error de clave duplicada.
    """
    op.execute("SET search_path TO public")
    op.execute("SET lock_timeout = '5s'")

    estaciones = op.get_bind().execute(
        sa.text("SELECT COUNT(DISTINCT estacion) FROM itinerary_entries")
    ).scalar()
    if estaciones and estaciones > 1:
        raise RuntimeError(
            f"Hay itinerario cargado de {estaciones} aeródromos. Deshacer esta "
            "migración borraría la columna que dice de cuál es cada fila, y el "
            "índice único sin estación rechazaría los indicativos repetidos "
            "entre aeródromos. Dejá un solo aeródromo antes de retroceder."
        )

    op.drop_constraint("fk_itinerary_uploads_estacion", "itinerary_uploads", type_="foreignkey")
    op.drop_column("itinerary_uploads", "estacion")

    op.drop_index("uq_itinerary_dedup", table_name="itinerary_entries")
    op.execute(
        """
        CREATE UNIQUE INDEX uq_itinerary_dedup ON itinerary_entries
            (flight_date, effective_from, call_sign, direction,
             hora_utc, aerodromo, tipo_aeronave, tipo_servicio)
            NULLS NOT DISTINCT
        """
    )
    op.drop_index("ix_itinerary_lookup", table_name="itinerary_entries")
    op.create_index(
        "ix_itinerary_lookup", "itinerary_entries",
        ["flight_date", "call_sign", "direction"],
    )

    op.drop_constraint("fk_itinerary_entries_estacion", "itinerary_entries", type_="foreignkey")
    op.drop_column("itinerary_entries", "estacion")

    op.drop_table("estaciones")
