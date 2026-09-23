import asyncio

from fastapi import WebSocket

SEND_TIMEOUT_SECONDS = 5


class ConnectionManager:
    """Difunde cada cambio a TODOS los clientes conectados, sin filtrar por
    sector: el requisito es que dos computadoras (una en SUR, otra en NOR)
    vean en vivo lo que la otra está trabajando."""

    def __init__(self) -> None:
        self._connections: list[WebSocket] = []
        # Última presencia ("estoy viendo el vuelo X") anunciada por cada
        # conexión, para poder limpiarla si el cliente se cae sin avisar
        # (cierre de laptop, corte de red) en vez de dejar a un operador
        # marcado como "viendo" un vuelo indefinidamente.
        self._presence: dict[WebSocket, tuple[str, str]] = {}

    async def connect(self, websocket: WebSocket, subprotocol: str | None = None) -> None:
        await websocket.accept(subprotocol=subprotocol)
        self._connections.append(websocket)

    def disconnect(self, websocket: WebSocket) -> None:
        if websocket in self._connections:
            self._connections.remove(websocket)
        self._presence.pop(websocket, None)

    def set_presence(self, websocket: WebSocket, operator_name: str, sector: str) -> None:
        self._presence[websocket] = (operator_name, sector)

    def pop_presence(self, websocket: WebSocket) -> tuple[str, str] | None:
        return self._presence.pop(websocket, None)

    async def broadcast(self, message: dict) -> None:
        stale: list[WebSocket] = []
        for connection in list(self._connections):
            try:
                await asyncio.wait_for(connection.send_json(message), timeout=SEND_TIMEOUT_SECONDS)
            except Exception:
                stale.append(connection)
        for connection in stale:
            self.disconnect(connection)
            try:
                await asyncio.wait_for(connection.close(code=1013), timeout=1)
            except Exception:
                pass


manager = ConnectionManager()
