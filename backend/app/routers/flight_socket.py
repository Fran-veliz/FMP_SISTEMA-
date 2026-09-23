"""Canal de eventos y presencia, autenticado por turno."""
import asyncio
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from starlette.concurrency import run_in_threadpool

from app.auth import SEGUNDOS_REVALIDACION_WS, turno_sigue_vigente, verify_ws_token
from app.database import SessionLocal
from app.ws import manager

logger = logging.getLogger(__name__)
router = APIRouter()

# Nombre del subprotocolo con el que el navegador anuncia que el segundo
# valor de la lista es el token de sesión.
WS_TOKEN_PROTOCOL = "ctot-shift-token"


def _token_del_handshake(websocket: WebSocket) -> str | None:
    """Lee el token del encabezado Sec-WebSocket-Protocol.

    El navegador no deja poner cabeceras propias al abrir un WebSocket, así
    que la práctica habitual era mandarlo en la query string -- pero las URLs
    terminan escritas en los logs del servidor, en el historial del navegador
    y en los de cualquier proxy intermedio. El subprotocolo viaja en una
    cabecera del handshake, que no se registra en esos lugares.

    Formato esperado: `ctot-shift-token, <token>`."""
    crudo = websocket.headers.get("sec-websocket-protocol")
    if not crudo:
        return None
    partes = [p.strip() for p in crudo.split(",")]
    if len(partes) >= 2 and partes[0] == WS_TOKEN_PROTOCOL:
        return partes[1]
    return None


async def _vigilar_vigencia_del_turno(websocket: WebSocket, token: str | None) -> None:
    """Cierra el canal cuando el turno deja de valer.

    El WebSocket comprobaba las credenciales una sola vez, al abrirse. Después
    nunca más: quien cerraba turno y dejaba la pestaña abierta seguía viendo la
    operación completa en vivo, sin turno detrás. Esto lo revisa cada
    SEGUNDOS_REVALIDACION_WS y corta si el turno se cerró, venció por
    inactividad o le invalidaron el token.

    La consulta va a un hilo aparte: con un solo worker, hacerla en el bucle de
    eventos frenaría a todos los demás."""
    while True:
        await asyncio.sleep(SEGUNDOS_REVALIDACION_WS)

        def _comprobar() -> bool:
            db = SessionLocal()
            try:
                return turno_sigue_vigente(db, token)
            finally:
                db.close()

        try:
            vigente = await run_in_threadpool(_comprobar)
        except Exception:
            logger.exception("WebSocket cerrado: no se pudo revalidar el turno")
            await websocket.close(code=1011)
            return
        if not vigente:
            logger.info("WebSocket cerrado: el turno dejó de ser válido")
            await websocket.close(code=4401)
            return


@router.websocket("/ws/{sector}")
async def flights_ws(websocket: WebSocket, sector: str):
    token = _token_del_handshake(websocket)
    db = SessionLocal()
    try:
        operator = verify_ws_token(db, token)
    finally:
        db.close()
    if operator is None:
        await websocket.close(code=4401)
        return

    # Hay que confirmar cuál de los subprotocolos ofrecidos se acepta, si no
    # el navegador aborta la conexión.
    await manager.connect(websocket, subprotocol=WS_TOKEN_PROTOCOL)
    vigilante = asyncio.create_task(_vigilar_vigencia_del_turno(websocket, token))
    try:
        while True:
            # Único mensaje que los clientes envían: en qué vuelo tienen el foco
            # (para que otras estaciones vean quién está editando cada fila).
            try:
                data = await websocket.receive_json()
            except ValueError:
                continue
            if isinstance(data, dict) and data.get("type") == "focus":
                # El nombre sale del turno que autorizó el WebSocket, no del
                # mensaje: si no, cualquier sesión válida -- incluida una de
                # solo lectura -- podía anunciarse como otro operador y
                # aparecer en las demás estaciones editando un vuelo.
                operator_name = operator.operator_name
                msg_sector = data.get("sector")
                flight_id = data.get("flight_id")
                if flight_id is not None and operator_name and msg_sector:
                    manager.set_presence(websocket, operator_name, msg_sector)
                else:
                    manager.pop_presence(websocket)
                await manager.broadcast(
                    {
                        "type": "presence",
                        "operator_name": operator_name,
                        "sector": msg_sector,
                        "flight_id": flight_id,
                    }
                )
    except WebSocketDisconnect:
        pass
    finally:
        vigilante.cancel()
        # Si el cliente se fue sin avisar (cerró la laptop, se cortó la red)
        # mientras tenía el foco en un vuelo, no debe quedar marcado como
        # "viendo" ese vuelo para siempre en las demás estaciones.
        last_presence = manager.pop_presence(websocket)
        manager.disconnect(websocket)
        if last_presence is not None:
            operator_name, msg_sector = last_presence
            await manager.broadcast(
                {"type": "presence", "operator_name": operator_name, "sector": msg_sector, "flight_id": None}
            )
