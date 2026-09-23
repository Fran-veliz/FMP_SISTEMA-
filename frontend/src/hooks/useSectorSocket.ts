import { useEffect, useRef } from "react";
import { api } from "../services/api";
import type { Flight, Sector, Shift } from "../types";

export type SocketMessage =
  | { type: "flight_upserted"; flight: Flight }
  | { type: "flight_deleted"; id: number }
  | { type: "shift_started"; shift: Shift }
  | { type: "shift_ended"; shift: Shift }
  | { type: "presence"; operator_name: string; sector: Sector; flight_id: number | null };

export type SocketSend = (message: Record<string, unknown>) => void;

const RECONNECT_BASE_MS = 1000;
const RECONNECT_MAX_MS = 30_000;

/** Se conecta al WS del backend y reintenta si se cae la conexión, para que
 * dos computadoras (una en SUR, otra en NOR) vean los cambios de la otra
 * en tiempo real sin tener que refrescar la página. Devuelve `send` para
 * emitir eventos propios (ej. en qué vuelo tengo el foco ahora).
 *
 * Backoff exponencial (1s, 2s, 4s... hasta un techo de 30s) en vez de un
 * intervalo fijo: si el backend está caído por un buen rato, cada pestaña
 * abierta no debería seguir martillándolo cada 2s indefinidamente. Se
 * reinicia a la base apenas la conexión vuelve a abrir con éxito.
 *
 * `onReconnect` se dispara cada vez que la conexión abre de nuevo DESPUÉS de
 * haberse caído (no en la conexión inicial): mientras el WS estuvo caído
 * pudieron perderse mensajes (ej. un vuelo borrado por otra estación), así
 * que quien la use debería reconciliar el estado (recargar la grilla) en vez
 * de arrastrar filas que ya no existen en el servidor. */
export function useSectorSocket(
  sector: Sector,
  onMessage: (msg: SocketMessage) => void,
  onReconnect?: () => void,
  /* Sin turno abierto no hay token, y el servidor rechaza el handshake. Sin
     este freno el hook igual intentaba conectar desde la pantalla de login:
     tres handshakes fallidos por cada ingreso, y como el backoff ya venía
     crecido, la sincronización en vivo tardaba varios segundos en levantar
     después de entrar. */
  enabled: boolean = true,
): SocketSend {
  const onMessageRef = useRef(onMessage);
  onMessageRef.current = onMessage;
  const onReconnectRef = useRef(onReconnect);
  onReconnectRef.current = onReconnect;
  const socketRef = useRef<WebSocket | null>(null);

  useEffect(() => {
    if (!enabled) return;
    let closedByUs = false;
    let retryTimer: ReturnType<typeof setTimeout> | null = null;
    let attempt = 0;
    let everConnected = false;

    function connect() {
      // El token va como subprotocolo, no en la URL (ver api.wsProtocols).
      const socket = new WebSocket(api.wsUrl(sector), api.wsProtocols());
      socketRef.current = socket;
      socket.onopen = () => {
        attempt = 0;
        if (everConnected) onReconnectRef.current?.();
        everConnected = true;
      };
      socket.onmessage = (event) => {
        try {
          const msg = JSON.parse(event.data) as SocketMessage;
          onMessageRef.current(msg);
        } catch {
          // ignora mensajes que no sean JSON válido
        }
      };
      socket.onclose = () => {
        if (!closedByUs) {
          const delay = Math.min(RECONNECT_BASE_MS * 2 ** attempt, RECONNECT_MAX_MS);
          attempt += 1;
          retryTimer = setTimeout(connect, delay);
        }
      };
    }

    connect();

    return () => {
      closedByUs = true;
      if (retryTimer) clearTimeout(retryTimer);
      socketRef.current?.close();
    };
  }, [sector, enabled]);

  return (message) => {
    if (socketRef.current?.readyState === WebSocket.OPEN) {
      socketRef.current.send(JSON.stringify(message));
    }
  };
}
