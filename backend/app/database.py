import os

from sqlalchemy import MetaData, create_engine
from sqlalchemy.engine import Engine, URL
from sqlalchemy.orm import DeclarativeBase, sessionmaker

DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
if not DATABASE_URL and all(os.environ.get(k) for k in ("POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_DB")):
    # Componentes en crudo: URL.create codifica @, /, %, etc. sin alterar
    # la contraseña que PostgreSQL recibió del mismo entorno de Compose.
    DATABASE_URL = URL.create(
        "postgresql+psycopg",
        username=os.environ["POSTGRES_USER"],
        password=os.environ["POSTGRES_PASSWORD"],
        host=os.environ.get("POSTGRES_HOST", "db"),
        port=int(os.environ.get("POSTGRES_PORT", "5432")),
        database=os.environ["POSTGRES_DB"],
    ).render_as_string(hide_password=False)
if not DATABASE_URL:
    raise RuntimeError("Falta DATABASE_URL: configura la conexion antes de iniciar el backend.")

# Todas las tablas viven en `public`, una sola base y un solo lugar.
#
# Hubo seis esquemas de dominio -- catalogos, cargas, itinerarios, ctot,
# informes, operaciones -- mas integracion. Separaban nombres, no privilegios:
# los permisos igual habia que darlos objeto por objeto, asi que lo unico que
# aportaban era agrupamiento visual, a cambio de calificar cada referencia y
# de que `search_path` decidiera en silencio a que tabla apuntaba un nombre
# sin calificar.
#
# El costo real aparecio al consolidar con el Portal ATFM: sus tablas tenian
# que entrar a alguno de esos esquemas o crear el suyo, y ninguna de las dos
# cosas describe lo que son. Con todo en `public`, el nombre de la tabla es la
# unica coordenada que hace falta.

engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention={
        "ix": "ix_%(table_name)s_%(column_0_name)s",
        "uq": "uq_%(table_name)s_%(column_0_name)s",
        "pk": "pk_%(table_name)s",
        "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    })


def crear_tablas(motor: Engine) -> None:
    """Crea las tablas para instalaciones nuevas. Las existentes usan Alembic."""
    with motor.begin() as conexion:
        Base.metadata.create_all(bind=conexion)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
