"""Que el esquema real coincida con los modelos.

Hay dos descripciones del esquema -- app/models.py y la cadena de alembic/ --
y nada las comparaba. Así fue como la tabla `estaciones` y las columnas
`estacion` del itinerario llegaron a estar declaradas en los modelos sin que
ninguna migración las creara.

No se notaba porque los dos caminos que crean una base son distintos
(ver scripts/init_db.py): una base NUEVA se arma con create_all desde los
modelos y se marca al día, así que siempre coincide; una base YA VERSIONADA
--producción, y la que se restaura desde un respaldo-- corre la cadena de
migraciones, y ahí lo que los modelos declaren de más simplemente no existe.
La suite entera usa el primer camino, que es justo el que no puede fallar.
"""
import os

import pytest
from sqlalchemy import create_engine

from app import database
from app.database import Base
from scripts.init_db import lo_que_le_falta_a_la_base

# Base Postgres donde probar la cadena de migraciones de verdad. SQLite no
# sirve para esto: no tiene ALTER COLUMN ni NULLS NOT DISTINCT, que es lo que
# usan las migraciones reales.
#
#   docker compose up -d db
#   TEST_POSTGRES_URL=postgresql+psycopg://ctot:ctot@127.0.0.1:5433/ctot_test \
#       python -m pytest tests/test_esquema.py
URL_POSTGRES = os.environ.get("TEST_POSTGRES_URL")


def _vaciar(engine) -> None:
    """Deja la base vacía de verdad, antes de cada prueba que use Postgres.

    `drop_all` no alcanza: deja los tipos enumerados en pie y cualquier resto
    de una corrida anterior impide borrarlos, así que la prueba fallaría por el
    estado previo y no por lo que viene a comprobar.
    """
    with engine.begin() as conexion:
        # Los esquemas de dominio se retiraron (migracion c7a4e91d3f60); se
        # sigue intentando borrarlos para que la prueba funcione tambien sobre
        # una base que quedo de antes.
        for esquema in ("catalogos", "cargas", "itinerarios", "ctot", "informes",
                        "operaciones", "integracion"):
            conexion.exec_driver_sql(f"DROP SCHEMA IF EXISTS {esquema} CASCADE")
        conexion.exec_driver_sql("DROP SCHEMA public CASCADE")
        conexion.exec_driver_sql("CREATE SCHEMA public")


def test_los_modelos_y_la_base_de_las_pruebas_coinciden():
    """Piso mínimo: sobre la base que arman las pruebas no falta nada."""
    assert lo_que_le_falta_a_la_base(database.engine, Base.metadata) == []


def test_la_comprobacion_nota_una_tabla_que_falta():
    """Si esto no detectara nada, la comprobación de init_db daría siempre por
    buena cualquier base y no serviría para nada."""
    engine = create_engine("sqlite://")
    # Se crean todas menos el maestro de aeródromos, y se comprueba que la
    # comprobación lo eche de menos. Antes se excluía `aeropuerto`, que era
    # uno de los tres catálogos que `aerodromo` unificó y que ya no existe.
    sin_catalogo = [
        t for t in Base.metadata.tables.values() if t.name != "aerodromo"
    ]
    Base.metadata.create_all(bind=engine, tables=sin_catalogo)

    faltantes = lo_que_le_falta_a_la_base(engine, Base.metadata)
    assert any("aerodromo" in f for f in faltantes), faltantes


@pytest.mark.skipif(not URL_POSTGRES, reason="requiere TEST_POSTGRES_URL (ver cabecera)")
def test_la_cadena_de_migraciones_deja_el_esquema_como_los_modelos(monkeypatch):
    """El camino de la base ya versionada, que es el que se rompió.

    Se arma el esquema de los modelos, se retrocede hasta donde estaba
    producción y se vuelve a aplicar. Si las migraciones no dejan exactamente
    lo que los modelos declaran, la comparación final lo dice.
    """
    from alembic import command
    from alembic.config import Config

    engine = create_engine(URL_POSTGRES)
    _vaciar(engine)

    # alembic/env.py toma la URL de app.database.DATABASE_URL y pisa lo que
    # diga la configuración, así que se apunta ahí: si no, las migraciones se
    # aplicarían sobre el SQLite de las pruebas y esto no probaría nada.
    monkeypatch.setattr(database, "DATABASE_URL", URL_POSTGRES)
    cfg = Config("alembic.ini")

    Base.metadata.create_all(bind=engine)

    # Una fila de itinerario ya cargada, para que el retroceso y la vuelta se
    # hagan sobre una base CON datos. Es lo que hace riesgosa a esta
    # migración: en producción hay 225.128 filas y la columna entra como
    # obligatoria, así que si el relleno no las cubriera, `NOT NULL` fallaría
    # y el arranque quedaría cortado.
    with engine.begin() as conexion:
        # Contra las tablas de HOY: create_all las arma como están en los
        # modelos. Antes esto insertaba en `catalogos.aeropuerto` e
        # `itinerarios.movimiento_programado`, que la normalización retiró
        # (migraciones c7a4e91d3f60 y f9d2b45e08a7) -- la prueba fallaba al
        # preparar sus datos y llevaba así desde entonces, sin que nadie lo
        # viera porque está detrás de TEST_POSTGRES_URL.
        conexion.exec_driver_sql(
            "INSERT INTO aerodromo "
            "(codigo_oaci, nombre_operativo, es_estacion_ctot, activo) "
            "VALUES ('SPJC', 'LIMA - JORGE CHÁVEZ', true, true)"
        )
        conexion.exec_driver_sql(
            "INSERT INTO capacidad_aerodromo (aerodromo_id, valor, fuente) "
            "SELECT id, 49, 'prueba' FROM aerodromo WHERE codigo_oaci = 'SPJC'"
        )
        conexion.exec_driver_sql(
            "INSERT INTO movimiento_programado "
            "(codigo_aeropuerto, fecha_itinerario, vigente_desde, indicativo, "
            " tipo_movimiento, hora_utc) "
            "VALUES ('SPJC', DATE '2026-06-03', DATE '2026-06-03', 'LPE2026', "
            "'ARR', '1545')"
        )

    command.stamp(cfg, "head")
    # Se retrocede a una revisión NOMBRADA y no a "-N": el punto es volver a
    # donde estaba producción antes de la traducción -- el catálogo de
    # aeropuertos con su columna obligatoria, el renombrado a esquemas de
    # dominio y el del tipo enumerado del itinerario. Con un número relativo,
    # cada migración nueva encima acorta el retroceso en silencio y el ciclo
    # deja de ejercitar justamente las que se quería probar.
    command.downgrade(cfg, "a2e9f1c7d340")
    assert lo_que_le_falta_a_la_base(engine, Base.metadata), (
        "downgrade no deshizo nada: la prueba no estaría probando la migración"
    )

    command.upgrade(cfg, "head")
    assert lo_que_le_falta_a_la_base(engine, Base.metadata) == []

    with engine.begin() as conexion:
        # La fila que ya estaba sobrevivió al viaje de ida y vuelta, con su
        # nombre de tabla nuevo, y quedó atribuida a Lima, que es de donde
        # viene todo lo cargado hasta 2026.
        fila = conexion.exec_driver_sql(
            "SELECT indicativo, codigo_aeropuerto FROM movimiento_programado"
        ).all()
        assert fila == [("LPE2026", "SPJC")], fila
        # Y el maestro quedó poblado por las propias migraciones: si dependiera
        # del arranque de la aplicación, la clave foránea de la columna recién
        # rellenada no tendría a qué apuntar. Se comprueba que estén, no cuáles
        # son todos: el maestro unificó tres catálogos y su contenido exacto
        # depende de cuántas equivalencias arrastre cada migración.
        catalogo = set(conexion.exec_driver_sql(
            "SELECT codigo_oaci FROM aerodromo WHERE codigo_oaci IS NOT NULL"
        ).scalars().all())
        assert {"SPJC", "SPZO"} <= catalogo, sorted(catalogo)


@pytest.mark.skipif(not URL_POSTGRES, reason="requiere TEST_POSTGRES_URL (ver cabecera)")
def test_la_comprobacion_nota_un_tipo_enumerado_con_otro_nombre():
    """La avería que tapó la comparación: el tipo, no la columna.

    c4e8b7a19d63 renombró la columna `direction` a `tipo_movimiento` y dejó el
    TIPO llamándose `direction`. Tablas y columnas coincidían con los modelos,
    así que la comprobación daba la base por buena; la carga del itinerario
    devolvía 500 porque SQLAlchemy escribe el parámetro como
    `::tipo_movimiento_enum` y PostgreSQL no acepta el INSERT contra una
    columna de otro tipo.

    Solo se puede probar contra Postgres: SQLite no tiene tipos enumerados
    propios -- SQLAlchemy los traduce a VARCHAR con un CHECK -- y no hay nombre
    que pueda diferir.
    """
    engine = create_engine(URL_POSTGRES)
    _vaciar(engine)
    Base.metadata.create_all(bind=engine)

    assert lo_que_le_falta_a_la_base(engine, Base.metadata) == []

    # El esquema donde vive el tipo depende de cómo se armó la base: `create_all`
    # no lo califica, así que cae en el primero del search_path.
    with engine.begin() as conexion:
        esquema = conexion.exec_driver_sql(
            "SELECT n.nspname FROM pg_type t "
            "JOIN pg_namespace n ON n.oid = t.typnamespace "
            "WHERE t.typname = 'tipo_movimiento_enum'"
        ).scalar()
        conexion.exec_driver_sql(
            f"ALTER TYPE {esquema}.tipo_movimiento_enum RENAME TO direction"
        )

    avisos = lo_que_le_falta_a_la_base(engine, Base.metadata)
    assert any(
        "movimiento_programado.tipo_movimiento" in aviso and "direction" in aviso
        for aviso in avisos
    ), avisos


@pytest.mark.skipif(not URL_POSTGRES, reason="requiere TEST_POSTGRES_URL (ver cabecera)")
def test_init_db_crea_una_base_vacia_y_puede_repetirse(monkeypatch):
    from sqlalchemy import inspect
    from sqlalchemy.engine import make_url
    from scripts.init_db import main

    engine = create_engine(URL_POSTGRES)
    _vaciar(engine)
    monkeypatch.setattr(database, "engine", engine)
    url = make_url(URL_POSTGRES).update_query_dict({"application_name": "ctot%bootstrap"})
    monkeypatch.setattr(database, "DATABASE_URL", url.render_as_string(hide_password=False))
    try:
        assert main() == 0
        assert lo_que_le_falta_a_la_base(engine, Base.metadata) == []
        assert inspect(engine).has_table("alembic_version", schema="public")
        assert main() == 0
    finally:
        engine.dispose()


@pytest.mark.skipif(not URL_POSTGRES, reason="requiere TEST_POSTGRES_URL (ver cabecera)")
def test_init_db_no_marca_una_base_sin_versionar_como_actualizada(monkeypatch):
    from sqlalchemy import inspect
    from scripts.init_db import main

    engine = create_engine(URL_POSTGRES)
    _vaciar(engine)
    with engine.begin() as conexion:
        conexion.exec_driver_sql("CREATE TABLE public.legacy_data (id integer PRIMARY KEY)")
        conexion.exec_driver_sql("INSERT INTO public.legacy_data VALUES (7)")
    monkeypatch.setattr(database, "engine", engine)
    monkeypatch.setattr(database, "DATABASE_URL", URL_POSTGRES)
    try:
        assert main() == 1
        assert not inspect(engine).has_table("alembic_version", schema="public")
        with engine.connect() as conexion:
            assert conexion.exec_driver_sql("SELECT id FROM public.legacy_data").scalar() == 7
    finally:
        engine.dispose()


# Lo que cada tipo enumerado tiene REALMENTE como etiquetas en Postgres.
#
# No se deduce del enum de Python a propósito: justamente lo que se comprueba
# es que los dos no se hayan separado. Si una migración cambia las etiquetas,
# acá hay que cambiarlas a mano, y ese cambio a mano es el punto.
ETIQUETAS_EN_POSTGRES = {
    "sector": {"SUR", "NOR"},
    "posicion_enum": {"FMP_SUR", "FMP_NOR", "FMP_CUSCO"},
    "tipo_movimiento_enum": {"ARR", "DEP"},
    "categoria_motivo_enum": {"SEC", "REV"},
    "tipo_importacion_enum": {"itinerario", "grilla_historica"},
}


def test_el_orm_escribe_valores_que_el_tipo_enumerado_acepta():
    """Lo que el ORM manda tiene que ser una etiqueta que el tipo admita.

    Esta es la avería que se llevó la carga de itinerario: el tipo
    `tipo_importacion_enum` lo creó la migración f3a8c41e7d92 con los VALORES
    del enum de Python (`itinerario`), y `Enum(TipoImportacion)` escribe por
    defecto el NOMBRE del miembro (`ITINERARIO`). Toda inserción por el ORM
    moría con `invalid input value for enum`, así que ni la carga de
    itinerario ni la de grilla histórica podían completarse. Se arregló con
    `values_callable` en la columna.

    No alcanza con exigir `values_callable` en todas partes: `posicion_enum`
    tiene nombre y valor distintos (`FMP_SUR` contra `"FMP SUR"`) y la base
    guarda los NOMBRES, que es lo que el comportamiento por defecto escribe.
    Las dos convenciones conviven, así que lo único que se puede comprobar es
    columna por columna contra lo que la base tiene de verdad.

    Esta prueba NO está detrás de `TEST_POSTGRES_URL` a propósito. La suite
    normal corre sobre SQLite, donde un enumerado es un VARCHAR sin valores
    permitidos: acepta cualquier cosa. Si esta comprobación dependiera de
    tener Postgres a mano, se saltaría en la corrida de siempre -- que es
    exactamente cómo el fallo llegó a producción.
    """
    from sqlalchemy import Enum as SAEnum

    desajustes = []
    comprobadas = 0
    for tabla in Base.metadata.tables.values():
        for columna in tabla.columns:
            if not isinstance(columna.type, SAEnum):
                continue
            if getattr(columna.type, "enum_class", None) is None:
                continue
            admite = ETIQUETAS_EN_POSTGRES.get(columna.type.name)
            if admite is None:
                desajustes.append(
                    f"{tabla.name}.{columna.name}: el tipo "
                    f"'{columna.type.name}' no está en ETIQUETAS_EN_POSTGRES; "
                    "agregalo con las etiquetas que crea su migración"
                )
                continue
            manda = {
                columna.type._db_value_for_elem(miembro)
                for miembro in columna.type.enum_class
            }
            comprobadas += 1
            if not manda <= admite:
                desajustes.append(
                    f"{tabla.name}.{columna.name} ({columna.type.name}): "
                    f"el ORM manda {sorted(manda - admite)}, "
                    f"el tipo admite {sorted(admite)}"
                )

    assert not desajustes, "\n".join(desajustes)
    assert comprobadas >= 7, f"solo se comprobaron {comprobadas} columnas"
