"""Siembra inicial de catálogos y perfiles de acceso.

Se corre una sola vez al iniciar la API (idempotente: si ya existe el
código, no lo duplica). A partir de ahí, todo lo nuevo se agrega vía
POST /reason-codes o PUT /airports/{iata} sin tocar este archivo.
"""

import os

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.airport_codes import SEED_AIRPORTS
from app.auth import generate_pin_salt, hash_pin
from app.services.ctot_calc import REV_REASONS, SEC_REASONS
from app.models import Aerodromo, CapacidadAerodromo, Controller, ReasonCategory, ReasonCode

# Nómina fija de operadores FMP (debe coincidir con CONTROLLERS en
# frontend/src/components/Login.tsx). La credencial inicial no vive en el
# repositorio: se recibe por INITIAL_OPERATOR_PIN al crear perfiles nuevos.
# Cambiar el PIN de alguien es un UPDATE directo:
#   UPDATE controllers SET pin_salt = '...', pin_hash = '...' WHERE name = '...'
# (ver app.auth.hash_pin/generate_pin_salt para generar el nuevo hash).
CONTROLLER_NAMES = [
    "SALCANTARA", "RCARDENAS", "OCRUZ", "GDELGADO", "GESTEPIA",
    "AFARIAS", "GGARAY", "JEGOMEZH", "CINFANTE", "GLAGO",
    "JMEZA", "GORTEGA", "SPADILLA", "MROMERO", "MSALAZARV",
    "DSAMANIEGO", "VITOR", "FRANVG",
]
# Perfiles de consulta de la DGAC (decisión del área, 2026-07-31). Ninguno
# puede escribir; se diferencian en qué alcanzan a ver y descargar:
#
#   DGAC1  -- consulta y descarga el día que tenga seleccionado.
#                       Sin ventana de semanas y sin límite de año.
#   DGAC2  -- solo consulta en pantalla, y únicamente el año en curso.
#            No descarga nada.
#
# Cambiar el alcance de uno es un UPDATE sobre `controllers` (can_export /
# export_max_weeks / view_year_only) -- la lógica que los aplica no distingue
# perfiles por nombre, así que no hay que tocar código.
DGAC_PROFILES = [
    # (nombre, variable del PIN, puede_descargar, semanas, solo año actual)
    ("DGAC1", "INITIAL_DGAC1_PIN", True, None, False),
    ("DGAC2", "INITIAL_DGAC2_PIN", False, None, True),
]


def _initial_pin(variable: str) -> str:
    value = os.environ.get(variable, "").strip()
    if not value:
        raise RuntimeError(
            f"Falta {variable}: es obligatoria para crear los perfiles iniciales. "
            "Definila en .env y reiniciá la API."
        )
    if os.environ.get("ENVIRONMENT") == "test":
        return value

    # Se valida que sea TIPEABLE, no solo que sea larga. Antes solo se miraba
    # el largo, y el marcador de posición del .env.example
    # ("cambiar-pin-operadores") lo cumplía: el sistema arrancaba sano, creaba
    # los veinte perfiles con esa credencial y NADIE podía entrar, porque el
    # campo de la pantalla de ingreso acepta solo dígitos y como máximo ocho.
    # El fallo era silencioso y el síntoma, desconcertante: "PIN incorrecto"
    # para todo el mundo, sin ningún error en el arranque.
    if not value.isdigit():
        raise RuntimeError(
            f"{variable} debe ser numérica: la pantalla de ingreso solo acepta dígitos. "
            f"Valor recibido: {len(value)} caracteres no numéricos."
        )
    if not 6 <= len(value) <= 8:
        raise RuntimeError(
            f"{variable} debe tener entre 6 y 8 dígitos (recibidos: {len(value)}). "
            "El campo de ingreso no admite más de 8."
        )
    return value

# Aeródromos con itinerario propio. Agregar una región es sumar una línea acá
# (o un INSERT en `estaciones`): no hay enum que migrar ni código que tocar.
#
# `capacidad` son las operaciones/hora que admite el aeródromo, arribos más
# despegues. La de Lima estuvo siempre fija en routers/forecast.py; las que no
# tengan cifra publicada por la DGAC van en None y /forecast cae al valor por
# omisión en vez de dibujar una línea inventada.
ESTACIONES = [
    # (código OACI, nombre, capacidad declarada)
    ("SPJC", "LIMA - JORGE CHÁVEZ", 49),
    ("SPZO", "CUSCO - ALEJANDRO VELASCO ASTETE", None),
]

REV_DESCRIPTIONS = {
    "RxSEC": "REVISIÓN X SECUENCIA",
    "RxCIADEP": "REVISIÓN X DEMORA EMBARQUE PAX",
    "RxCIAADH": "REVISIÓN X ADELANTO DE HORA",
    "RxCIAyTFC": "REVISIÓN X DEMORA Y TRAFICO",
    "RxTMI": "REVISIÓN X TMI",
    "RxLIMCAP": "REVISIÓN X LIMITE DE CAPACIDAD",
    "RxTWR": "REVISIÓN X TORRE",
    "RxACC": "REVISIÓN X ACC",
    "RxTFC": "REVISIÓN X TRAFICO",
    "RxCIAROD": "REVISIÓN X DEMORA EN RODAJE",
    "RxCIADOC": "REVISIÓN X DEMORA EN DOCUMENTOS",
    "RxCIAPDR": "REVISIÓN X PASAJERO DISRUPTIVO",
    "RxWIND": "REVISIÓN X VIENTO",
    "RxBIRDS": "REVISIÓN X AVES",
    "RxMET": "REVISIÓN X METEOROLOGÍA",
    "RxNOTAM": "REVISIÓN X NOTAM",
}


def seed(db: Session) -> None:
    existing_reasons = {
        (r.category, r.code) for r in db.execute(select(ReasonCode.category, ReasonCode.code)).all()
    }
    for code in SEC_REASONS:
        if (ReasonCategory.SEC, code) not in existing_reasons:
            db.add(ReasonCode(category=ReasonCategory.SEC, code=code, description=None))
    for code in REV_REASONS:
        if (ReasonCategory.REV, code) not in existing_reasons:
            db.add(ReasonCode(category=ReasonCategory.REV, code=code, description=REV_DESCRIPTIONS.get(code)))

    # Maestro de aeródromos. Primero las estaciones con itinerario propio, con
    # su capacidad como declaración vigente; después las equivalencias IATA de
    # los extremos de rutas. Las dos cosas van a la misma tabla: son el mismo
    # hecho, que antes vivía en `aeropuerto` y `codigo_aeropuerto` por separado.
    existing_oaci = {
        o for (o,) in db.execute(select(Aerodromo.codigo_oaci)).all() if o
    }
    for codigo, nombre, capacidad in ESTACIONES:
        if codigo in existing_oaci:
            continue
        aerodromo = Aerodromo(
            codigo_oaci=codigo,
            nombre_operativo=nombre,
            es_estacion_ctot=True,
        )
        db.add(aerodromo)
        db.flush()
        existing_oaci.add(codigo)
        # `capacidad` nula significa que la DGAC no publicó la cifra de esa
        # estación. Se registra la declaración igual, con el valor en nulo:
        # "sin declarar" es un dato, y distinto de no tener declaración.
        db.add(
            CapacidadAerodromo(
                aerodromo_id=aerodromo.id,
                valor=capacidad,
                fuente="DGAC" if capacidad is not None else None,
            )
        )
    # Las estaciones tienen que existir antes que cualquier fila de itinerario
    # que las referencie por clave foránea.
    db.flush()

    existing_iata = {
        i for (i,) in db.execute(select(Aerodromo.codigo_iata)).all() if i
    }
    for iata, info in SEED_AIRPORTS.items():
        if iata in existing_iata:
            continue
        oaci = info["icao"] or None
        # Si el aeródromo ya está cargado por su OACI -- una estación CTOT, por
        # ejemplo -- lo que falta es la equivalencia, no una fila nueva.
        aerodromo = (
            db.execute(select(Aerodromo).where(Aerodromo.codigo_oaci == oaci))
            .scalars()
            .first()
            if oaci
            else None
        )
        if aerodromo is None:
            aerodromo = Aerodromo(codigo_oaci=oaci)
            db.add(aerodromo)
        aerodromo.codigo_iata = iata
        if not aerodromo.nombre_oficial:
            aerodromo.nombre_oficial = info["name"]
        if not aerodromo.ciudad:
            aerodromo.ciudad = info["city"]
        if not aerodromo.pais:
            aerodromo.pais = info["country"]

    existing_controllers = {c for (c,) in db.execute(select(Controller.usuario)).all()}
    missing_operators = [name for name in CONTROLLER_NAMES if name not in existing_controllers]
    operator_pin = _initial_pin("INITIAL_OPERATOR_PIN") if missing_operators else None
    for name in CONTROLLER_NAMES:
        if name not in existing_controllers:
            salt = generate_pin_salt()
            db.add(Controller(usuario=name, pin_salt=salt, pin_hash=hash_pin(operator_pin, salt)))
    for name, pin_variable, can_export, max_weeks, year_only in DGAC_PROFILES:
        if name not in existing_controllers:
            pin = _initial_pin(pin_variable)
            salt = generate_pin_salt()
            db.add(Controller(
                usuario=name,
                pin_salt=salt,
                pin_hash=hash_pin(pin, salt),
                read_only=True,
                can_export=can_export,
                export_max_weeks=max_weeks,
                view_year_only=year_only,
            ))

    db.commit()
