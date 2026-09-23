import logging
from datetime import date, datetime, time, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from starlette.concurrency import run_in_threadpool
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import clock
from app.auth import (
    INTENTOS_MAX_POR_ORIGEN,
    VENTANA_POR_ORIGEN,
    MAX_FAILED_ATTEMPTS,
    cerrar_turnos_vencidos,
    generate_session_token,
    revocar_sesiones,
    hash_pin,
    huella_token,
    limpiar_intentos,
    lockout_minutes_for,
    origen_de,
    pin_hash_needs_upgrade,
    registrar_intento,
    require_shift,
    require_viewable_date,
    shift_is_idle,
    verify_pin,
)
from app.database import get_db
from app.models import Controller, Position, Sesion, ShiftLog
from app.schemas import ShiftAuthOut, ShiftClockIn, ShiftClockOut, ShiftOut
from app.ws import manager

logger = logging.getLogger(__name__)

router = APIRouter(tags=["shifts"])


def _emitir_pase(db: Session, shift: ShiftLog, controller: Controller | None = None) -> str:
    """Abre una sesión nueva para ese turno y devuelve el pase en claro.

    Revoca antes las que estuvieran vigentes: volver a entrar con el PIN
    invalida cualquier copia que haya quedado en otra computadora. Las filas
    revocadas no se borran -- son el registro de que esa sesión existió y
    hasta cuándo valió.

    `turno.token_sesion` se sigue escribiendo mientras dure la transición,
    para que un rollback del código no deje a nadie afuera."""
    revocar_sesiones(db, shift.id, "rotacion")
    crudo = generate_session_token()
    ahora = clock.now_utc()
    db.add(
        Sesion(
            turno_id=shift.id,
            operador_id=controller.id if controller is not None else shift.operador_id,
            token_hash=huella_token(crudo),
            iniciada_en=ahora,
            ultima_actividad_en=ahora,
        )
    )
    shift.session_token = huella_token(crudo)
    shift.last_seen_at = ahora
    return crudo


def _con_pase(shift: ShiftLog, crudo: str) -> ShiftAuthOut:
    """Arma la respuesta con el pase en claro.

    En la base solo queda su huella, así que serializar el turno directo
    devolvería esa huella -- inútil para el cliente. El pase en claro existe
    únicamente durante esta petición y en el navegador del operador."""
    salida = ShiftAuthOut.model_validate(shift)
    salida.session_token = crudo
    return salida


@router.post("/shifts/clock-in", response_model=ShiftAuthOut)
async def clock_in(payload: ShiftClockIn, request: Request, db: Session = Depends(get_db)):
    # Freno por origen antes que nada: se aplica exista o no el perfil, así un
    # script no puede recorrer la nómina entera ni hacer que la API gaste un
    # hash Argon2 por cada intento (ver app.auth).
    origen = origen_de(request)
    if not registrar_intento(origen):
        raise HTTPException(
            status_code=429,
            detail=(
                f"Demasiados intentos desde esta red "
                f"({INTENTOS_MAX_POR_ORIGEN} en {int(VENTANA_POR_ORIGEN.total_seconds() // 60)} min). "
                "Esperá unos minutos antes de volver a intentar."
            ),
        )

    # Comparación insensible a mayúsculas/espacios: "fran", "FRAN" y "Fran "
    # son la misma persona -- sin esto, cada variante de tipeo abría un turno
    # nuevo en vez de reenganchar al que ya estaba activo.
    normalized_name = payload.operator_name.strip()

    controller = db.execute(
        select(Controller).where(
            func.lower(func.trim(Controller.usuario)) == normalized_name.lower()
        )
    ).scalars().first()

    # Un perfil desactivado no entra. Se comprueba antes del PIN y con el mismo
    # mensaje que un nombre inexistente: responder distinto delataría qué
    # cuentas existen. No se borra la fila porque sus turnos, cambios y cargas
    # siguen en la bitácora y tienen que poder atribuirse.
    if controller is not None and not controller.activo:
        controller = None

    # Perfil bloqueado por intentos fallidos: se rechaza antes de comparar el
    # PIN, así el bloqueo no se puede sortear siguiendo los intentos.
    ahora = clock.now_utc()
    if controller is not None and controller.locked_until and controller.locked_until > ahora:
        faltan = max(1, int((controller.locked_until - ahora).total_seconds() // 60) + 1)
        raise HTTPException(
            status_code=429,
            detail=f"Perfil bloqueado por intentos fallidos. Volvé a intentar en {faltan} min.",
        )

    # Argon2 con 19 MB tarda decenas de milisegundos. Este endpoint es async y
    # uvicorn corre un solo worker, así que llamarlo directo congelaba el bucle
    # de eventos -- y con él toda la API -- en cada intento de ingreso.
    pin_valido = controller is not None and await run_in_threadpool(
        verify_pin, payload.pin, controller.pin_salt, controller.pin_hash
    )
    if not pin_valido:
        # El contador vive en el perfil, no en la IP ni en la sesión: lo que se
        # protege es la cuenta. Si el nombre no existe no hay nada que contar
        # (y responder distinto delataría qué nombres son válidos, así que el
        # mensaje es el mismo).
        if controller is not None:
            controller.failed_attempts += 1
            if controller.failed_attempts >= MAX_FAILED_ATTEMPTS:
                controller.locked_until = ahora + timedelta(minutes=lockout_minutes_for(controller))
                controller.failed_attempts = 0
            db.commit()
        raise HTTPException(status_code=401, detail="Nombre o PIN incorrecto")

    limpiar_intentos(origen)

    # Ingreso correcto: se limpia el contador y cualquier bloqueo vencido.
    if controller.failed_attempts or controller.locked_until:
        controller.failed_attempts = 0
        controller.locked_until = None
        db.commit()

    # Migra credenciales antiguas sin interrumpir el servicio: el PIN en texto
    # solo existe durante esta petición y el hash SHA-256 se reemplaza al
    # primer ingreso correcto.
    if pin_hash_needs_upgrade(controller.pin_hash):
        controller.pin_hash = await run_in_threadpool(hash_pin, payload.pin)
        db.commit()

    active = db.execute(
        select(ShiftLog).where(
            func.lower(func.trim(ShiftLog.operator_name)) == normalized_name.lower(),
            ShiftLog.end_time.is_(None),
        )
    ).scalars().first()
    if active is not None:
        if active.position == payload.position:
            # la misma persona re-entra a la misma posición (ej. restauró sesión
            # desde otra pestaña): se reengancha al turno ya abierto en vez de duplicar.
            # Un turno que ya estaba abierto antes de que existiera session_token
            # (dato viejo) no tiene token todavía -- se genera acá en vez de
            # devolver uno nulo, que rompería la validación de ShiftAuthOut.
            # Se emite un pase NUEVO en cada reenganche, siempre. Antes se
            # conservaba el anterior si seguía vigente, pero desde que en la
            # base solo vive su huella ya no se puede devolver: el sistema no
            # conoce el pase original. Rotarlo es además lo más sano -- volver
            # a entrar con el PIN invalida cualquier copia que haya quedado en
            # otra computadora.
            crudo = _emitir_pase(db, active, controller)
            # Los límites se recopian del perfil en cada reenganche, no solo
            # al abrir el turno: si no, un turno que quedó abierto de antes
            # conserva los permisos viejos y una restricción nueva (ej. acotar
            # a la DGAC al año en curso) no tendría efecto hasta que esa
            # persona cierre turno.
            # Un turno abierto de antes de esta columna no tiene la
            # referencia: se completa al reenganchar, sin esperar a que la
            # persona cierre turno.
            active.operador_id = controller.id
            active.read_only = controller.read_only
            active.can_export = controller.can_export
            active.export_max_weeks = controller.export_max_weeks
            active.view_year_only = controller.view_year_only
            db.commit()
            db.refresh(active)
            return _con_pase(active, crudo)
        raise HTTPException(
            status_code=409,
            detail=f"{payload.operator_name} ya tiene un turno abierto en {active.position.value}. "
            "Cierra ese turno antes de abrir uno en otra posición.",
        )

    shift = ShiftLog(
        # El nombre canónico de la nómina, no el que vino en el payload: si no,
        # tipear "franvg" dejaba todo el historial de vuelos firmado en
        # minúscula y sin coincidir con el perfil que autorizó el turno.
        operador_id=controller.id,
        operator_name=controller.usuario,
        position=payload.position,
        start_time=clock.now_utc(),
        last_seen_at=clock.now_utc(),
        read_only=controller.read_only,
        can_export=controller.can_export,
        export_max_weeks=controller.export_max_weeks,
        view_year_only=controller.view_year_only,
    )
    db.add(shift)
    try:
        db.commit()
    except IntegrityError:
        # Otra petición de la misma persona ganó la carrera y creó el turno
        # entre nuestra consulta y este commit (doble clic, dos pestañas, un
        # reintento de red). La base lo rechaza gracias al índice parcial
        # uq_turno_abierto_por_operador; acá se recupera el turno que sí quedó
        # y se devuelve ese. Para el operador es indistinguible de haber
        # entrado a la primera, que es lo que corresponde: quería un turno
        # abierto y lo tiene.
        db.rollback()
        shift = db.execute(
            select(ShiftLog).where(
                ShiftLog.operator_name == controller.usuario,
                ShiftLog.end_time.is_(None),
            )
        ).scalars().first()
        if shift is None:
            raise
        # De ese turno solo queda la huella, así que se le emite un pase nuevo
        # para poder devolvérselo a quien está entrando.
        crudo_nuevo = _emitir_pase(db, shift, controller)
        db.commit()
        db.refresh(shift)
        return _con_pase(shift, crudo_nuevo)

    # La sesión se crea después del commit: necesita el id del turno.
    crudo_nuevo = _emitir_pase(db, shift, controller)
    db.commit()
    db.refresh(shift)
    await manager.broadcast({"type": "shift_started", "shift": ShiftOut.model_validate(shift).model_dump(mode="json")})
    return _con_pase(shift, crudo_nuevo)


@router.post("/shifts/{shift_id}/clock-out", response_model=ShiftOut)
async def clock_out(
    shift_id: int,
    payload: ShiftClockOut | None = None,
    db: Session = Depends(get_db),
    operator: ShiftLog = Depends(require_shift),
):
    if operator.id != shift_id:
        raise HTTPException(status_code=403, detail="No podés cerrar el turno de otro operador")
    shift = operator

    shift.end_time = clock.now_utc()
    if payload is not None and payload.handover_note and payload.handover_note.strip():
        shift.handover_note = payload.handover_note.strip()
    revocar_sesiones(db, shift.id, "cierre_turno")
    shift.session_token = None
    db.commit()
    db.refresh(shift)
    await manager.broadcast({"type": "shift_ended", "shift": ShiftOut.model_validate(shift).model_dump(mode="json")})
    return shift


@router.get("/shifts/last-handover", response_model=ShiftOut | None)
def last_handover(
    position: Position,
    db: Session = Depends(get_db),
    _: ShiftLog = Depends(require_shift),
):
    """La nota de relevo más reciente para la posición: el último turno
    cerrado con nota dentro de las últimas 24 horas. Más viejo que eso ya
    no es un relevo, es historia."""
    limite = clock.now_utc() - timedelta(hours=24)
    return db.execute(
        select(ShiftLog)
        .where(
            ShiftLog.position == position,
            ShiftLog.end_time.is_not(None),
            ShiftLog.end_time >= limite,
            ShiftLog.handover_note.is_not(None),
        )
        .order_by(ShiftLog.end_time.desc())
    ).scalars().first()


@router.get("/shifts/active", response_model=list[ShiftOut])
def active_shifts(db: Session = Depends(get_db), _: ShiftLog = Depends(require_shift)):
    # Antes de listar se cierran los que ya vencieron. No hace falta una tarea
    # de fondo: la FMP mira esta lista permanentemente, así que un turno
    # olvidado se limpia en cuanto alguien abre la pantalla.
    cerrados = cerrar_turnos_vencidos(db)
    if cerrados:
        logger.info("cerrados %d turno(s) vencidos por inactividad", cerrados)
    shifts = db.execute(select(ShiftLog).where(ShiftLog.end_time.is_(None))).scalars().all()
    return shifts


@router.get("/shifts", response_model=list[ShiftOut])
def list_shifts(
    shift_date: date,
    db: Session = Depends(get_db),
    shift: ShiftLog = Depends(require_shift),
):
    """Quién estuvo de turno ese día y en qué horario.

    Devuelve los turnos que **cubren** la jornada, no solo los que empezaron
    en ella. La diferencia importa de madrugada, que es cuando se pregunta:
    un relevo que entró a las 22:00 y cerró a las 02:00 cubrió las dos
    primeras horas del día siguiente, y con el filtro por hora de inicio ese
    día aparecía sin nadie a cargo. El turno se lista en los dos días, que es
    lo que refleja lo que pasó.

    Un turno todavía abierto se cuenta desde que empezó: no tiene fin con el
    que decidir hasta dónde llega.
    """
    require_viewable_date(shift, shift_date)

    inicio = datetime.combine(shift_date, time.min)
    fin = inicio + timedelta(days=1)

    shifts = db.execute(
        select(ShiftLog)
        .where(ShiftLog.start_time < fin)
        .where(or_(ShiftLog.end_time.is_(None), ShiftLog.end_time >= inicio))
        .order_by(ShiftLog.start_time)
    ).scalars().all()
    return shifts
