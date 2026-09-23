import logging
import os
import uuid

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from app import clock, database, seed_data
from app.database import get_db
from app.routers import (
    airports, estaciones, flights, forecast, itinerary, reason_codes, shifts,
)

# Sin esto, lo único que quedaba de una jornada era el log de acceso de
# uvicorn: método, ruta, código y hora. Ante un incidente de madrugada no
# alcanzaba para reconstruir nada.
#
# No hace falta una plataforma de observabilidad para un sistema de este
# tamaño: con nivel configurable y las trazas de los errores no controlados
# alcanza para diagnosticar.
logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO").strip().upper(),
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger("ctot")

environment = os.environ.get("ENVIRONMENT", "production").strip().lower()
# Falla cerrada: solo "development" (o "test") publica /docs. Antes bastaba con
# que ENVIRONMENT no dijera exactamente "production" -- un typo como
# "produccion", o la variable sin definir en el servidor, dejaba el esquema
# completo de la API a la vista de cualquiera en la red.
expone_documentacion = environment in {"development", "test"}

app = FastAPI(
    title="FMP LIMA - GDP",
    docs_url="/docs" if expone_documentacion else None,
    redoc_url="/redoc" if expone_documentacion else None,
    openapi_url="/openapi.json" if expone_documentacion else None,
)

# Orígenes del frontend real; ALLOWED_ORIGINS permite agregar otros sin tocar
# código (ej. otro puerto/host en otra máquina de la FMP). "*" + credentials
# es además una combinación inválida según la spec CORS -- los navegadores la
# rechazan para requests con credenciales.
_default_origins = "http://localhost,http://localhost:80,http://localhost:5173"
allowed_origins = os.environ.get("ALLOWED_ORIGINS", _default_origins).split(",")

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.middleware("http")
async def registrar_errores(request: Request, call_next):
    """Un identificador por petición y traza completa de lo que se rompa.

    El identificador se devuelve en la respuesta y se escribe en el log: si un
    operador reporta "me salió un error", ese código lleva directo a la traza
    exacta, sin tener que adivinar por horario.

    Al cliente no se le manda el detalle interno -- diría más de la cuenta
    sobre el sistema y no le sirve de nada."""
    peticion_id = uuid.uuid4().hex[:12]
    try:
        respuesta = await call_next(request)
    except Exception:
        logger.exception(
            "error no controlado [%s] %s %s", peticion_id, request.method, request.url.path
        )
        return JSONResponse(
            status_code=500,
            content={
                "detail": "Error interno. Pasale este código a soporte para ubicarlo en el registro.",
                "codigo": peticion_id,
            },
            headers={"X-Request-Id": peticion_id},
        )
    respuesta.headers["X-Request-Id"] = peticion_id
    return respuesta


app.include_router(flights.router)
app.include_router(itinerary.router)
app.include_router(shifts.router)
app.include_router(forecast.router)
app.include_router(reason_codes.router)
app.include_router(airports.router)
app.include_router(estaciones.router)


@app.on_event("startup")
def on_startup() -> None:
    # El esquema real se versiona con Alembic (ver alembic/ y el CMD del
    # Dockerfile, que corre "alembic upgrade head" antes de levantar uvicorn).
    # create_all acá es solo un fallback para bases nuevas sin pasar por
    # Alembic (ej. un DATABASE_URL de desarrollo apuntando a un sqlite vacío);
    # es un no-op si las tablas ya existen.
    database.crear_tablas(database.engine)
    db = database.SessionLocal()
    try:
        seed_data.seed(db)
    finally:
        db.close()
    clock.start_background_resync()


@app.get("/health")
def health():
    """Vitalidad: ¿el proceso responde?

    A propósito NO consulta la base. El healthcheck de Docker marca el estado;
    no reinicia por sí solo un proceso vivo. Reiniciar la API no arregla una base
    caída: solo produciría un ciclo de reinicios mientras el problema está en
    otro lado. Lo que sí detecta es el caso real que preocupa -- un bucle de
    eventos bloqueado, con el proceso vivo pero sin responder."""
    return {"status": "ok"}


@app.get("/ready")
def ready(db: Session = Depends(get_db)):
    """Disponibilidad: ¿el sistema puede atender de verdad?

    Acá sí se consulta la base. Es el endpoint para monitoreo y para
    diagnosticar a mano; devuelve 503 si la base no responde, en vez del
    "ok" incondicional que daba /health y que ocultaba el problema."""
    try:
        db.execute(text("SELECT 1"))
    except Exception as exc:
        logger.error("la base de datos no responde: %s", exc)
        return JSONResponse(
            status_code=503,
            content={"status": "sin base de datos", "reloj": clock.sync_status()},
        )
    return {"status": "ok", "reloj": clock.sync_status()}


@app.get("/server-time")
def server_time():
    """Hora UTC del servidor, corregida contra NTP (ver app/clock.py) para
    que el reloj mostrado en el frontend no dependa del reloj de la laptop
    del operador."""
    # `reloj` dice si esa hora está verificada contra NTP o es simplemente la
    # del contenedor. Sin este dato, un NTP inalcanzable degrada en silencio
    # y nadie se entera de que la referencia horaria dejó de estar validada.
    return {"utc": clock.now_utc().isoformat() + "Z", "reloj": clock.sync_status()}
