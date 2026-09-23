"""Cambia las credenciales de acceso de los perfiles.

Por qué hace falta: migrar los hashes a Argon2id NO cambió ningún PIN, solo la
forma de guardarlos. Los valores iniciales que tuvo el sistema quedaron en el
historial del repositorio, así que siguen sirviendo hasta que alguien los rote.
Y la aplicación no tiene pantalla para cambiarlos: sin este script, la única
vía es un UPDATE a mano contra Postgres.

Uso (dentro del contenedor de la API, que ya tiene DATABASE_URL):

    docker compose exec api python scripts/rotar_pin.py --listar
    docker compose exec api python scripts/rotar_pin.py --perfil FRANVG
    docker compose exec api python scripts/rotar_pin.py --perfil FRANVG --pin 481907
    docker compose exec api python scripts/rotar_pin.py --todos

Sin --pin se genera uno al azar. El PIN se imprime UNA sola vez, al rotarlo: no
queda guardado en ningún lado en texto legible, así que hay que anotarlo y
entregarlo por el canal que corresponda antes de cerrar la terminal.

Rotar un PIN no cierra el turno que esa persona tenga abierto: el token de
sesión sigue valiendo hasta que cierre turno o venza por inactividad. Para
cortar además las sesiones vivas, usar --cerrar-turnos.
"""

from __future__ import annotations

import argparse
import secrets
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from sqlalchemy import select  # noqa: E402

from app.auth import generate_pin_salt, hash_pin, revocar_sesiones  # noqa: E402
from app.database import SessionLocal  # noqa: E402
from app.models import Controller, ShiftLog  # noqa: E402

LARGO_PIN = 6


def pin_al_azar(largo: int = LARGO_PIN) -> str:
    """Dígitos con generador criptográfico. No se usa random.* a propósito:
    esa secuencia es predecible si alguien conoce la semilla."""
    return "".join(secrets.choice("0123456789") for _ in range(largo))


def rotar(db, controller: Controller, pin: str, cerrar_turnos: bool) -> None:
    controller.pin_salt = generate_pin_salt()
    controller.pin_hash = hash_pin(pin)
    # Un perfil bloqueado por intentos fallidos se libera al rotarle el PIN:
    # si no, TI entrega una credencial nueva que igual no deja entrar.
    controller.failed_attempts = 0
    controller.locked_until = None
    if cerrar_turnos:
        abiertos = db.execute(
            select(ShiftLog).where(
                ShiftLog.operator_name == controller.usuario, ShiftLog.end_time.is_(None)
            )
        ).scalars().all()
        for turno in abiertos:
            revocar_sesiones(db, turno.id, "rotacion_pin")
            turno.session_token = None


def main() -> int:
    parser = argparse.ArgumentParser(description="Rota credenciales de acceso")
    grupo = parser.add_mutually_exclusive_group(required=True)
    grupo.add_argument("--listar", action="store_true", help="muestra los perfiles, sin tocar nada")
    grupo.add_argument("--perfil", help="nombre del perfil a rotar (ej. FRANVG, DGAC1)")
    grupo.add_argument("--todos", action="store_true", help="rota todos los perfiles")
    parser.add_argument("--pin", help=f"PIN a fijar; si se omite, se genera uno de {LARGO_PIN} dígitos")
    parser.add_argument(
        "--cerrar-turnos",
        action="store_true",
        help="invalida además los turnos abiertos de ese perfil (corta sesiones vivas)",
    )
    args = parser.parse_args()

    if args.pin and args.todos:
        parser.error("--pin no se puede usar con --todos: cada perfil debe tener el suyo")
    if args.pin and (not args.pin.isascii() or not args.pin.isdigit()):
        parser.error("el PIN debe contener solo dígitos ASCII")
    if args.pin and not 6 <= len(args.pin) <= 8:
        parser.error("el PIN debe tener entre 6 y 8 dígitos")

    db = SessionLocal()
    try:
        if args.listar:
            perfiles = db.execute(select(Controller).order_by(Controller.usuario)).scalars().all()
            print(f"{'PERFIL':<14} {'TIPO':<12} {'HASH':<10} BLOQUEADO HASTA")
            for c in perfiles:
                tipo = "solo lectura" if c.read_only else "operador"
                algo = "argon2id" if c.pin_hash.startswith("$argon2") else "SHA-256!"
                print(f"{c.name:<14} {tipo:<12} {algo:<10} {c.locked_until or '-'}")
            print(
                "\nUn perfil que todavía diga SHA-256 conserva su credencial original;"
                "\nse convierte solo cuando esa persona ingresa, o al rotarlo acá."
            )
            return 0

        if args.todos:
            objetivos = db.execute(select(Controller).order_by(Controller.usuario)).scalars().all()
        else:
            objetivos = db.execute(
                select(Controller).where(Controller.usuario == args.perfil)
            ).scalars().all()
            if not objetivos:
                print(f"No existe el perfil {args.perfil!r}. Ver --listar.", file=sys.stderr)
                return 1

        print("Anotá estas credenciales AHORA: no se vuelven a mostrar.\n")
        print(f"{'PERFIL':<14} PIN")
        for c in objetivos:
            pin = args.pin or pin_al_azar()
            rotar(db, c, pin, args.cerrar_turnos)
            print(f"{c.name:<14} {pin}")
        db.commit()
        print(f"\n{len(objetivos)} perfil(es) actualizado(s).")
        if not args.cerrar_turnos:
            print("Los turnos que ya estaban abiertos siguen válidos (usar --cerrar-turnos para cortarlos).")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
