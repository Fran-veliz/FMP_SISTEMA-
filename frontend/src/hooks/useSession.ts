import { useCallback, useEffect, useState } from "react";
import { api, ApiError, setAuthToken } from "../services/api";
import type { Position, Sector, Shift } from "../types";

export interface Session {
  shift: Shift;
  operatorName: string;
  sector: Sector;
}

const SESSION_KEY = "ctot_session";

function loadStoredSession(): Session | null {
  try {
    const raw = localStorage.getItem(SESSION_KEY);
    return raw ? (JSON.parse(raw) as Session) : null;
  } catch {
    return null;
  }
}

export interface LoginParams {
  operatorName: string;
  position: Position;
  sector: Sector;
  pin: string;
}

/** El turno abierto: se guarda en localStorage para sobrevivir un refresco de
 *  la página, y se revalida contra el servidor al arrancar. */
export function useSession() {
  const [session, setSession] = useState<Session | null>(null);
  const [checking, setChecking] = useState(true);
  const [loginBusy, setLoginBusy] = useState(false);
  const [loginError, setLoginError] = useState<string | null>(null);

  useEffect(() => {
    (async () => {
      const stored = loadStoredSession();
      if (stored) {
        try {
          // El token va ANTES de consultar: las lecturas dejaron de ser
          // anónimas, así que sin esto el propio chequeo de "¿sigue vivo mi
          // turno?" respondería 401 y cerraría la sesión de quien la tenía
          // perfectamente válida. Si el turno ya no existe, se limpia abajo.
          setAuthToken(stored.shift.session_token ?? null);
          const active = await api.activeShifts();
          if (active.some((s) => s.id === stored.shift.id)) {
            setSession(stored);
          } else {
            setAuthToken(null);
            localStorage.removeItem(SESSION_KEY);
          }
        } catch (e) {
          // 401 = el token guardado ya no vale (turno cerrado desde otra
          // máquina, o base reiniciada): al login. Cualquier otra falla es de
          // red y no debería expulsar a nadie de su turno.
          if (e instanceof ApiError && e.status === 401) {
            setAuthToken(null);
            localStorage.removeItem(SESSION_KEY);
          } else {
            setSession(stored);
          }
        }
      }
      setChecking(false);
    })();
  }, []);

  const login = useCallback(async (params: LoginParams) => {
    setLoginBusy(true);
    setLoginError(null);
    try {
      const shift = await api.clockIn(params.operatorName, params.position, params.pin);
      const nueva: Session = { shift, operatorName: params.operatorName, sector: params.sector };
      setAuthToken(shift.session_token ?? null);
      localStorage.setItem(SESSION_KEY, JSON.stringify(nueva));
      setSession(nueva);
    } catch (e) {
      setLoginError(e instanceof ApiError ? e.message : "No se pudo iniciar el turno");
    } finally {
      setLoginBusy(false);
    }
  }, []);

  /** Solo limpia la sesión local: el cierre del turno en el servidor lo hace
   *  quien llama (la bitácora, con su nota de relevo). */
  const logout = useCallback(() => {
    setAuthToken(null);
    localStorage.removeItem(SESSION_KEY);
    setSession(null);
  }, []);

  return { session, checking, loginBusy, loginError, login, logout };
}
