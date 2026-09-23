import os

os.environ["DATABASE_URL"] = "sqlite://"
os.environ["ENVIRONMENT"] = "test"
os.environ["INITIAL_OPERATOR_PIN"] = "1234"
os.environ["INITIAL_DGAC1_PIN"] = "2580"
os.environ["INITIAL_DGAC2_PIN"] = "2581"

from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from app import database

database.engine = create_engine(
    "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
)
database.SessionLocal.configure(bind=database.engine)


# Las pruebas trabajan sobre el 3 de junio de 2026. Antes eso daba igual, pero
# desde que la grilla solo se corrige hasta un día hacia atrás
# (auth.require_writable_date) una fecha fija se vuelve historia cerrada con el
# paso del tiempo y la suite empieza a fallar sola.
#
# En vez de reescribir 75 fechas literales, se ancla el reloj: las pruebas
# quedan deterministas y no caducan. Quien necesite el reloj real -- las del
# propio módulo clock -- puede pedir el marcador `reloj_real`.
FECHA_DE_PRUEBA = datetime(2026, 6, 3, 12, 0, 0)


@pytest.fixture(autouse=True)
def _reloj_anclado(request, monkeypatch):
    """El reloj arranca en la fecha de prueba y AVANZA con el reloj real.

    Congelarlo del todo parecía más simple, pero dejaba todos los timestamps
    idénticos y el historial de cambios --que se ordena por `changed_at`--
    quedaba en orden arbitrario. Con el desfase, el orden se conserva."""
    if "reloj_real" in request.keywords:
        return
    from app import clock

    inicio_real = datetime.utcnow()
    monkeypatch.setattr(
        clock, "now_utc", lambda: FECHA_DE_PRUEBA + (datetime.utcnow() - inicio_real)
    )


@pytest.fixture(autouse=True)
def _reset_schema():
    import app.models  # noqa: F401  (registra las tablas en Base.metadata)
    from app import seed_data

    database.Base.metadata.drop_all(bind=database.engine)
    database.Base.metadata.create_all(bind=database.engine)
    # Se siembra acá directo (no se depende del evento startup de FastAPI,
    # que solo corre una vez por proceso) para que la nómina de operadores FMP
    # -- necesaria para el PIN del clock-in -- exista siempre, sin importar
    # si el TestClient de un test en particular dispara el lifespan o no.
    db = database.SessionLocal()
    try:
        seed_data.seed(db)
    finally:
        db.close()
    yield
