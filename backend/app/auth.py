"""Autenticación mínima por turno: al hacer clock-in se emite un token de
sesión (X-Shift-Token) que el frontend reenvía en cada escritura. Sin esto,
cualquiera con acceso de red al puerto de la API podía crear/editar/borrar
vuelos o firmar cambios con el nombre de cualquier operador FMP.

No es un sistema de usuarios/roles completo -- es intencionalmente lo más
simple que cierra el hueco real: mientras el turno esté abierto, las
escrituras quedan atadas a ese turno y al operator_name que quedó registrado
al abrirlo (nunca al que mande el cliente en el payload)."""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from datetime import date, datetime, timedelta

from fastapi import Depends, Header, HTTPException, Request
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import clock
from app.database import get_db
from app.models import Sesion, ShiftLog


def generate_session_token() -> str:
    """El pase que se le entrega al operador. Solo existe en su navegador."""
    return secrets.token_urlsafe(32)


def huella_token(token: str) -> str:
    """Lo que se guarda en la base: una huella del pase, no el pase.

    El PIN estaba protegido con Argon2, pero el token de sesión se guardaba tal
    cual. Quien consiguiera leer la base mientras hubiera turnos abiertos podía
    copiar esos tokens y actuar como esos operadores de inmediato, sin conocer
    ningún PIN.

    Acá alcanza SHA-256 y no hace falta Argon2: el token son 32 bytes al azar,
    así que no hay nada que adivinar por fuerza bruta -- lo que se busca es que
    la base no contenga el valor utilizable, y para eso un hash rápido sirve.
    Además se comprueba en cada petición, y Argon2 ahí costaría carísimo.

    Salen 64 caracteres hexadecimales, que es justo el largo de la columna."""
    return hashlib.sha256(token.encode()).hexdigest()


def generate_pin_salt() -> str:
    return secrets.token_hex(8)


_pin_hasher = (
    PasswordHasher(time_cost=1, memory_cost=1024, parallelism=1)
    if os.environ.get("ENVIRONMENT") == "test"
    else PasswordHasher(time_cost=2, memory_cost=19456, parallelism=1)
)


def hash_pin(pin: str, salt: str | None = None) -> str:
    """Genera un hash Argon2id lento y salteado para una credencial.

    ``salt`` se conserva en la firma para no romper scripts administrativos
    existentes, pero Argon2 genera e incluye su propia sal criptográfica.
    """
    return _pin_hasher.hash(pin)


def verify_pin(pin: str, salt: str, expected_hash: str) -> bool:
    if expected_hash.startswith("$argon2"):
        try:
            return _pin_hasher.verify(expected_hash, pin)
        except (VerificationError, InvalidHashError):
            return False

    # Compatibilidad temporal con los SHA-256 ya guardados. Tras un ingreso
    # correcto, shifts.clock_in los convierte inmediatamente a Argon2id.
    legacy_hash = hashlib.sha256((salt + pin).encode()).hexdigest()
    return hmac.compare_digest(legacy_hash, expected_hash)


def pin_hash_needs_upgrade(expected_hash: str) -> bool:
    if not expected_hash.startswith("$argon2"):
        return True
    try:
        return _pin_hasher.check_needs_rehash(expected_hash)
    except InvalidHashError:
        return True


def require_shift(
    x_shift_token: str | None = Header(None, alias="X-Shift-Token"),
    db: Session = Depends(get_db),
) -> ShiftLog:
    """Exige un turno abierto con ese token (401 si no existe, ya cerró, o
    falta la cabecera). La usan tanto endpoints de escritura como el propio
    clock-out -- un perfil de solo lectura (ver require_writable_shift) tiene
    que poder cerrar turno igual que cualquiera.

    La cabecera se declara opcional y se rechaza a mano para que su ausencia
    sea 401 y no el 422 de validación que devolvería FastAPI: al cliente le
    importa que no está autorizado, no que le falta un campo."""
    if not x_shift_token:
        raise HTTPException(status_code=401, detail="Falta el turno: iniciá sesión para consultar datos")
    shift = turno_del_pase(db, x_shift_token)
    if shift is None:
        raise HTTPException(status_code=401, detail="Turno no válido, expirado o ya cerrado")
    if shift_is_idle(shift):
        raise HTTPException(
            status_code=401,
            detail=f"Turno inactivo por más de {SHIFT_IDLE_TIMEOUT_HOURS} h: volvé a ingresar con tu PIN",
        )
    touch_shift(db, shift)
    return shift


# Un token deja de valer tras este tiempo sin usarse. Se cuenta desde la
# última petición, no desde el inicio del turno: un turno largo pero activo no
# se corta solo, y uno abandonado deja de servir. 12 h es holgado a propósito
# -- los relevos son cada ~2 h, así que ningún operador real llega a ese
# límite trabajando, pero un token olvidado deja de ser eterno, que era el
# problema real.
SHIFT_IDLE_TIMEOUT_HOURS = 12

# Duración máxima prevista de un turno en una posición. Los relevos son cada
# ~2 h, así que pasadas 3 h lo más probable es que el operador ya se haya ido
# y se haya olvidado de cerrar -- que es justo como quedaron turnos abiertos
# durante semanas, sosteniendo un token vivo.
#
# NO corta la sesión: expulsar a alguien de una posición en plena operación
# 24/7 es peor que el problema que evita (mismo criterio que el bloqueo corto
# por PIN, ver lockout_minutes_for). Solo marca el turno como excedido para
# que la interfaz lo avise, a él y al resto de la FMP.
SHIFT_MAX_HOURS = 3


def shift_hours_elapsed(shift: ShiftLog, ahora: datetime | None = None) -> float:
    """Horas que lleva abierto el turno. Se mide con el reloj corregido por
    NTP del servidor (app.clock), no con el de la computadora del operador."""
    fin = shift.end_time or (ahora or clock.now_utc())
    return (fin - shift.start_time).total_seconds() / 3600


def shift_exceeds_max(shift: ShiftLog, ahora: datetime | None = None) -> bool:
    return shift_hours_elapsed(shift, ahora) > SHIFT_MAX_HOURS


def turno_del_pase(db: Session, token: str) -> ShiftLog | None:
    """El turno abierto al que pertenece este pase, o None.

    Busca primero en `ctot.sesion`, que es donde vive la identidad de la
    sesión, y cae a `turno.token_sesion` para los turnos que ya estaban
    abiertos antes de esa tabla. Sin ese respaldo, desplegar el cambio habría
    invalidado en el acto los pases de todos los turnos en curso.

    Devuelve el TURNO y no la sesión: todo lo que viene después -- permisos,
    alcance de consulta, autoría de los cambios -- es del turno. La sesión es
    cómo se llegó hasta él."""
    huella = huella_token(token)

    sesion = db.execute(
        select(Sesion).where(Sesion.token_hash == huella, Sesion.revocada_en.is_(None))
    ).scalars().first()
    if sesion is not None:
        turno = db.get(ShiftLog, sesion.turno_id)
        return turno if turno is not None and turno.end_time is None else None

    return db.execute(
        select(ShiftLog).where(
            ShiftLog.session_token == huella, ShiftLog.end_time.is_(None)
        )
    ).scalars().first()


def revocar_sesiones(db: Session, turno_id: int, motivo: str) -> None:
    """Revoca las sesiones vigentes de un turno. No borra las filas: el
    registro de que esa sesión existió y hasta cuándo valió es lo que la hace
    auditable."""
    ahora = clock.now_utc()
    for sesion in db.execute(
        select(Sesion).where(Sesion.turno_id == turno_id, Sesion.revocada_en.is_(None))
    ).scalars().all():
        sesion.revocada_en = ahora
        sesion.motivo_revocacion = motivo


def shift_is_idle(shift: ShiftLog) -> bool:
    referencia = shift.last_seen_at or shift.start_time
    return clock.now_utc() - referencia > timedelta(hours=SHIFT_IDLE_TIMEOUT_HOURS)


def cerrar_turnos_vencidos(db: Session) -> int:
    """Cierra los turnos que ya vencieron por inactividad. Devuelve cuántos.

    El vencimiento dejaba de aceptar el pase a las SHIFT_IDLE_TIMEOUT_HOURS,
    pero NUNCA cerraba el turno: `end_time` quedaba en NULL para siempre y la
    bitácora seguía mostrando a esa persona trabajando. Cada vez que alguien se
    olvidaba de cerrar, se sumaba uno más, indefinidamente.

    La duración se calcula hasta la ÚLTIMA ACTIVIDAD REAL, no hasta el momento
    en que se ejecuta este cierre. Si no, un turno olvidado el viernes y cerrado
    el lunes figuraría con setenta y dos horas de trabajo que nadie hizo.

    Queda marcado como cierre automático: la bitácora es un registro operativo
    y no puede dar a entender que el operador cerró algo que no cerró.
    """
    vencidos = [
        turno
        for turno in db.execute(select(ShiftLog).where(ShiftLog.end_time.is_(None))).scalars().all()
        if shift_is_idle(turno)
    ]
    for turno in vencidos:
        fin = turno.last_seen_at or turno.start_time
        turno.end_time = fin
        turno.cerrado_automaticamente = True
        # El pase se invalida igual que en un cierre a mano. La duración ya no
        # se guarda: sale de iniciado_en y finalizado_en, y ese `fin` es la
        # última actividad real y no el instante del cierre, así que el
        # derivado da lo mismo que daba el valor grabado.
        revocar_sesiones(db, turno.id, "inactividad")
        turno.session_token = None
    if vencidos:
        db.commit()
    return len(vencidos)


def touch_shift(db: Session, shift: ShiftLog) -> None:
    """Marca actividad. Se escribe con granularidad de minuto para no hacer un
    UPDATE por cada request de una grilla que se refresca seguido."""
    ahora = clock.now_utc()
    if shift.last_seen_at is None or (ahora - shift.last_seen_at) > timedelta(minutes=1):
        shift.last_seen_at = ahora
        db.commit()


# Freno por origen, aparte del bloqueo por perfil. El bloqueo por perfil cuida
# una cuenta a la vez: sin esto, un script puede atacar las veinte en paralelo
# y de paso tumbar la API, porque cada intento cuesta un hash Argon2 de 19 MB.
#
# El límite es holgado a propósito: toda la FMP puede salir por la misma IP, y
# dejar sin abrir turno a una posición 24/7 es peor que el ataque que evita.
#
# La ventana es corta (5 min, no 15) justamente por eso: mientras el freno está
# puesto se rechaza también el PIN correcto -- verificarlo costaría el hash
# Argon2 que se quiere evitar --, así que quien quede atrapado sin culpa vuelve
# a entrar en minutos. Para un ataque no cambia nada: 60 intentos cada 5 min
# son 720 por hora contra un espacio de 10.000, o sea catorce horas por cuenta,
# con el bloqueo por perfil actuando encima.
INTENTOS_MAX_POR_ORIGEN = 60
VENTANA_POR_ORIGEN = timedelta(minutes=5)
_intentos_por_origen: dict[str, list[datetime]] = {}


def origen_de(request: Request) -> str:
    """Dirección real del cliente.

    nginx sobrescribe X-Real-IP con la dirección de la conexión (ver
    docker/nginx.conf), así que no se puede falsear desde afuera. El
    fallback cubre llegar directo a uvicorn en desarrollo."""
    return request.headers.get("X-Real-IP") or (request.client.host if request.client else "desconocido")


def registrar_intento(origen: str) -> bool:
    """Anota un intento de ingreso. Devuelve False si ese origen se pasó."""
    ahora = clock.now_utc()
    desde = ahora - VENTANA_POR_ORIGEN
    # Se purga todo el diccionario, no solo esta entrada: si no, las IP que
    # probaron una vez y no volvieron quedarían acumulándose para siempre.
    if len(_intentos_por_origen) > 512:
        for clave in [k for k, v in _intentos_por_origen.items() if not any(t > desde for t in v)]:
            del _intentos_por_origen[clave]
    intentos = [t for t in _intentos_por_origen.get(origen, []) if t > desde]
    intentos.append(ahora)
    _intentos_por_origen[origen] = intentos
    return len(intentos) <= INTENTOS_MAX_POR_ORIGEN


def limpiar_intentos(origen: str) -> None:
    """Un ingreso correcto borra la cuenta de ese origen: el freno es contra
    quien prueba a ciegas, no contra la posición que trabaja normalmente."""
    _intentos_por_origen.pop(origen, None)


MAX_FAILED_ATTEMPTS = 10
# Bloqueo distinto según el perfil: para un observador externo media hora es
# una barrera efectiva y sin costo. Para un operador FMP el costo es real --
# quedar fuera del sistema media hora en plena operación 24/7 es peor que el
# riesgo que evita -- así que se le aplica un bloqueo corto, suficiente para
# frenar un script pero no para dejar una posición sin registrar.
LOCKOUT_MINUTES_READ_ONLY = 30
LOCKOUT_MINUTES_OPERATOR = 5


def lockout_minutes_for(controller) -> int:
    return LOCKOUT_MINUTES_READ_ONLY if controller.read_only else LOCKOUT_MINUTES_OPERATOR


def require_viewable_date(shift: ShiftLog, fecha: date) -> None:
    """Un perfil con `view_year_only` (los de la DGAC) solo consulta el año en
    curso. Se valida acá y no en el frontend porque el límite tiene que valer
    también si alguien llama la API directamente."""
    if shift.view_year_only and fecha.year != clock.now_utc().year:
        raise HTTPException(
            status_code=403,
            detail=f"Este perfil solo puede consultar datos del año {clock.now_utc().year}",
        )


def require_export_allowed(shift: ShiftLog, fecha: date) -> None:
    """Descarga: primero si el perfil puede descargar, después hasta cuántas
    semanas hacia atrás."""
    if not shift.can_export:
        raise HTTPException(status_code=403, detail="Este perfil puede consultar en pantalla, pero no descargar datos")
    require_viewable_date(shift, fecha)
    if shift.export_max_weeks is not None:
        limite = clock.now_utc().date() - timedelta(weeks=shift.export_max_weeks)
        if fecha < limite:
            raise HTTPException(
                status_code=403,
                detail=(
                    f"Este perfil puede descargar hasta {shift.export_max_weeks} semanas hacia atrás "
                    f"(desde el {limite.isoformat()})"
                ),
            )


# La FMP corrige hasta UN día hacia atrás: la jornada en curso y la anterior.
# Más atrás que eso ya es historia cerrada y no se toca desde la grilla.
#
# Hasta ahora esta regla vivía SOLO en el frontend, que deshabilitaba los
# controles. Una llamada directa a la API creaba, editaba y borraba vuelos de
# 2021 sin ningún reparo: comprobado. Una regla operativa no puede depender de
# un botón gris.
DIAS_EDITABLES_HACIA_ATRAS = 1


def fecha_editable(fecha: date) -> bool:
    return fecha >= clock.now_utc().date() - timedelta(days=DIAS_EDITABLES_HACIA_ATRAS)


def es_correccion_de_dia_pasado(fecha: date) -> bool:
    """Editable pero no de hoy: hay que dejar constancia (ver los avisos que
    emiten los endpoints de vuelos)."""
    return fecha_editable(fecha) and fecha < clock.now_utc().date()


def require_writable_date(fecha: date) -> None:
    """Rechaza escrituras sobre jornadas ya cerradas.

    No limita hacia adelante: cargar el itinerario del día siguiente es parte
    del trabajo normal."""
    if not fecha_editable(fecha):
        limite = clock.now_utc().date() - timedelta(days=DIAS_EDITABLES_HACIA_ATRAS)
        raise HTTPException(
            status_code=403,
            detail=(
                f"No se puede modificar el {fecha.isoformat()}: la grilla se corrige hasta "
                f"un día hacia atrás (desde el {limite.isoformat()})."
            ),
        )


def require_writable_shift(shift: ShiftLog = Depends(require_shift)) -> ShiftLog:
    """Para endpoints que además modifican datos (crear/editar/borrar vuelos,
    cargar itinerario, motivos, aeródromos): un perfil de solo lectura (ej.
    DGAC) tiene un turno válido pero no puede escribir nada, sin importar qué
    endpoint lo pida ni qué controles muestre el frontend."""
    if shift.read_only:
        raise HTTPException(status_code=403, detail="Perfil de solo lectura: no puede modificar datos")
    return shift


# Cada cuánto el WebSocket vuelve a comprobar que el turno sigue en pie. El
# canal autenticaba SOLO al abrirse: quien cerraba turno y dejaba la pestaña
# abierta seguía recibiendo toda la operación en vivo, sin turno detrás.
SEGUNDOS_REVALIDACION_WS = 30


def turno_sigue_vigente(db: Session, token: str | None) -> bool:
    """Comprueba el turno SIN marcar actividad.

    A diferencia de verify_ws_token, no llama a touch_shift a propósito: si el
    control periódico del WebSocket marcara actividad, una pestaña olvidada
    mantendría el turno vivo para siempre y el vencimiento por inactividad no
    se cumpliría nunca -- justo lo contrario de lo que se busca."""
    if not token:
        return False
    shift = turno_del_pase(db, token)
    return shift is not None and not shift_is_idle(shift)


def verify_ws_token(db: Session, token: str | None) -> ShiftLog | None:
    """Misma verificación que require_shift, pero para el handshake del
    WebSocket (que no puede mandar cabeceras custom desde el navegador)."""
    if not token:
        return None
    shift = turno_del_pase(db, token)
    if shift is None or shift_is_idle(shift):
        return None
    touch_shift(db, shift)
    return shift
