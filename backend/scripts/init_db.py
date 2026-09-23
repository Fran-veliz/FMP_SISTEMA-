"""Deja la base lista antes de arrancar la API.

Por qué existe: una base vacía NO puede recorrer la cadena de migraciones. La
primera de todas (ff29dcbcea20, "baseline") da por sentado que las tablas ya
existen -- nacieron de un create_all cuando el proyecto todavía no usaba
Alembic -- y arranca haciendo ALTER TABLE flights. En un servidor nuevo eso
falla con 'relation "flights" does not exist', alembic corta con error, y como
el contenedor encadena `alembic upgrade head && uvicorn`, la API nunca levanta.

Así que hay dos caminos:

  - La base ya está bajo control de versiones (existe alembic_version):
    se corre la cadena normal, `upgrade head`.

  - Base nueva: se crea el esquema completo desde los modelos y se marca la
    cadena entera como aplicada (`stamp head`). Es la instalación limpia; no
    hay nada viejo que migrar.

Una base con tablas pero sin alembic_version se rechaza sin modificarla:
create_all no agrega columnas faltantes y stamp no ejecuta migraciones.
Ese caso requiere identificar y migrar su version real.

Al final se comprueba que el esquema real coincida con los modelos. Hay dos
descripciones del esquema -- app/models.py y la cadena de alembic/ -- y nada
las comparaba: la tabla `estaciones` y las columnas `estacion` del itinerario
llegaron a estar declaradas en los modelos sin que ninguna migración las
creara. En una instalación nueva no se veía, porque ese camino aplica los
modelos enteros; en una base ya versionada el esquema quedaba incompleto y el
sistema fallaba recién al consultar el itinerario, en operación.

Si falta algo, esto corta el arranque. El contenedor encadena
`init_db.py && uvicorn`, así que la API no levanta y el problema aparece en el
despliegue, mirando los logs, en vez de a las tres de la mañana en la primera
consulta de un operador.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import Enum, inspect, text

# El script se invoca desde /app en el contenedor, donde viven alembic.ini y el
# paquete app.
RAIZ = Path(__file__).resolve().parent.parent
logger = logging.getLogger("init_db")


def _enumerados_con_otro_nombre(conexion, metadata) -> list[str]:
    """Columnas cuyo tipo enumerado no se llama como el modelo dice.

    compare_metadata no lo mira, y es una diferencia que no se nota hasta que
    alguien escribe: SQLAlchemy manda el parámetro calificado con el nombre que
    declara el modelo (`... ::tipo_movimiento_enum`), y si la columna es de otro
    tipo PostgreSQL rechaza el INSERT entero con DatatypeMismatch. Leer sigue
    funcionando, así que el sistema parece sano hasta la primera carga.

    Pasó de verdad: c4e8b7a19d63 tradujo la columna `direction` a
    `tipo_movimiento` sin renombrar el tipo, y la carga del itinerario devolvía
    500 en producción mientras la base "coincidía" con los modelos.

    Solo el nombre. Las etiquetas no: agregar un valor al enumerado de Python
    sin migrar es otro problema, y mezclarlo acá haría saltar el aviso en
    despliegues donde lo que sobra en la base es inofensivo.
    """
    reales = {
        (fila[0], fila[1], fila[2]): fila[3]
        for fila in conexion.execute(
            text(
                "SELECT table_schema, table_name, column_name, udt_name "
                "FROM information_schema.columns WHERE data_type = 'USER-DEFINED'"
            )
        )
    }

    distintos: list[str] = []
    for tabla in metadata.tables.values():
        esquema = tabla.schema or "public"
        for columna in tabla.columns:
            if not isinstance(columna.type, Enum):
                continue
            real = reales.get((esquema, tabla.name, columna.name))
            # None = la columna no existe todavía, o no es de un tipo propio.
            # Lo primero ya lo reporta compare_metadata; lo segundo es una base
            # a medio migrar y no hay nombre que comparar.
            if real is not None and real != columna.type.name:
                distintos.append(
                    f"la columna '{esquema}.{tabla.name}.{columna.name}' es del "
                    f"tipo '{real}' y el modelo declara '{columna.type.name}'"
                )
    return distintos


def lo_que_le_falta_a_la_base(engine, metadata) -> list[str]:
    """Qué espera el modelo que la base no tenga: tablas y columnas ausentes.

    Se mira SOLO lo que falta, no lo que sobra. Una columna de más es inofensiva
    (queda ahí sin que nadie la lea) y aparecería en cada base que conserve algo
    viejo; una columna de menos rompe toda consulta que la nombre.

    De los tipos enumerados se compara SOLO el nombre, y de los índices nada.
    Valores por omisión, etiquetas y detalles de índices quedan fuera: ahí las
    diferencias entre lo que declara SQLAlchemy y lo que devuelve el motor son
    habituales y no rompen nada. Un aviso que salta sin motivo se termina
    ignorando, y entonces no avisa de lo que importa.
    """
    if engine.dialect.name == "postgresql":
        # El camino que importa: se compara con esquemas, que es como está la
        # base real.
        with engine.connect() as conexion:
            contexto = MigrationContext.configure(
                conexion, opts={"include_schemas": True}
            )
            diferencias = compare_metadata(contexto, metadata)
            faltantes: list[str] = _enumerados_con_otro_nombre(conexion, metadata)

        for diferencia in diferencias:
            # compare_metadata devuelve tuplas sueltas y, para algunas
            # restricciones, listas de tuplas.
            for entrada in diferencia if isinstance(diferencia, list) else [diferencia]:
                if not isinstance(entrada, tuple) or not entrada:
                    continue
                if entrada[0] == "add_table":
                    tabla = entrada[1]
                    donde = f"{tabla.schema}.{tabla.name}" if tabla.schema else tabla.name
                    faltantes.append(f"falta la tabla '{donde}'")
                elif entrada[0] == "add_column":
                    esquema = f"{entrada[1]}." if entrada[1] else ""
                    faltantes.append(
                        f"falta la columna '{esquema}{entrada[2]}.{entrada[3].name}'"
                    )
        return faltantes

    # SQLite no tiene esquemas: database.py los traduce a nada al conectar, así
    # que las tablas conviven en un solo espacio de nombres. Se comparan por
    # nombre, sin esquema. Es menos fino que compare_metadata, pero detecta lo
    # mismo que importa -- una tabla o una columna que el modelo declara y la
    # base no tiene -- y no depende de que el motor entienda esquemas.
    inspector = inspect(engine)
    presentes = set(inspector.get_table_names())
    faltantes = []
    for tabla in metadata.tables.values():
        if tabla.name not in presentes:
            faltantes.append(f"falta la tabla '{tabla.name}'")
            continue
        columnas = {c["name"] for c in inspector.get_columns(tabla.name)}
        for columna in tabla.columns:
            if columna.name not in columnas:
                faltantes.append(f"falta la columna '{tabla.name}.{columna.name}'")
    return faltantes


def main() -> int:
    sys.path.insert(0, str(RAIZ))
    from app import models  # noqa: F401  -- registra las tablas en Base
    from app.database import Base, crear_tablas, engine

    cfg = Config(str(RAIZ / "alembic.ini"))
    inspector = inspect(engine)
    es_postgres = engine.dialect.name == "postgresql"
    versionada = inspector.has_table("alembic_version", schema="public" if es_postgres else None)

    if versionada:
        logger.info("Base versionada: aplicando migraciones")
        command.upgrade(cfg, "head")
    else:
        if inspector.get_table_names(schema="public" if es_postgres else None):
            logger.error(
                "Base con tablas y sin version de Alembic: requiere revision antes de migrar. "
                "No se modifico ni se marco como actualizada."
            )
            return 1
        logger.info("Base nueva: creando tablas")
        crear_tablas(engine)
        command.stamp(cfg, "head")

    faltantes = lo_que_le_falta_a_la_base(engine, Base.metadata)
    if faltantes:
        logger.error("El esquema de la base no coincide con los modelos")
        for falta in faltantes:
            logger.error("%s", falta)
        logger.error("Falta aplicar la migracion correspondiente; se cancela el arranque de la API")
        return 1

    logger.info("Base lista")
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    raise SystemExit(main())
