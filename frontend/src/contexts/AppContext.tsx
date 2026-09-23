
import { createContext, useCallback, useContext, useEffect, useState } from "react";
import type { ReactNode } from "react";
import { api } from "../services/api";
import type { ReasonCode, Sector } from "../types";

type Theme = "light" | "dark";

/* Escalones de ampliación de pantalla (100% a 150%). Pensado para
   operadores que necesitan ver la grilla más grande sin depender del
   zoom del navegador (Ctrl +/-), que se pierde entre sesiones. */
export const ZOOM_STEPS = [1, 1.15, 1.3, 1.5] as const;

interface AppContextValue {
  secReasons: ReasonCode[];
  revReasons: ReasonCode[];
  refreshReasonCodes: () => Promise<void>;
  presence: Record<number, { operator_name: string; sector: Sector }>;
  setPresence: (flightId: number, operator_name: string, sector: Sector) => void;
  clearPresenceFor: (operator_name: string) => void;
  theme: Theme;
  toggleTheme: () => void;
  zoom: number;
  setZoom: (z: number) => void;
}

const AppContext = createContext<AppContextValue | null>(null);

const THEME_KEY = "ctot_theme";
const ZOOM_KEY = "ctot_zoom";

function initialTheme(): Theme {
  const stored = localStorage.getItem(THEME_KEY);
  if (stored === "light" || stored === "dark") return stored;
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

function initialZoom(): number {
  const stored = Number(localStorage.getItem(ZOOM_KEY));
  return ZOOM_STEPS.includes(stored as (typeof ZOOM_STEPS)[number]) ? stored : 1;
}

export function AppProvider({ children }: { children: ReactNode }) {
  const [secReasons, setSecReasons] = useState<ReasonCode[]>([]);
  const [revReasons, setRevReasons] = useState<ReasonCode[]>([]);
  const [presence, setPresenceMap] = useState<Record<number, { operator_name: string; sector: Sector }>>({});
  const [theme, setTheme] = useState<Theme>(initialTheme);
  const [zoom, setZoomState] = useState<number>(initialZoom);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
  }, [theme]);

  useEffect(() => {
    // `zoom` amplía toda la interfaz; --ui-zoom permite compensar los
    // tamaños en vh del CSS (100vh / zoom) para que no aparezca scroll.
    (document.documentElement.style as CSSStyleDeclaration & { zoom: string }).zoom = String(zoom);
    document.documentElement.style.setProperty("--ui-zoom", String(zoom));
  }, [zoom]);

  const refreshReasonCodes = useCallback(async () => {
    const [sec, rev] = await Promise.all([api.reasonCodes("SEC"), api.reasonCodes("REV")]);
    setSecReasons(sec);
    setRevReasons(rev);
  }, []);

  // No se piden al montar: el catálogo de motivos dejó de ser público y en la
  // pantalla de login todavía no hay token, así que la llamada daría 401. Lo
  // dispara AppShell en cuanto hay turno abierto.

  function setPresence(flightId: number, operator_name: string, sector: Sector) {
    setPresenceMap((prev) => ({ ...prev, [flightId]: { operator_name, sector } }));
  }

  function clearPresenceFor(operator_name: string) {
    setPresenceMap((prev) => {
      const next = { ...prev };
      for (const [key, val] of Object.entries(next)) {
        if (val.operator_name === operator_name) delete next[Number(key)];
      }
      return next;
    });
  }

  function toggleTheme() {
    setTheme((prev) => {
      const next = prev === "dark" ? "light" : "dark";
      localStorage.setItem(THEME_KEY, next);
      return next;
    });
  }

  function setZoom(z: number) {
    localStorage.setItem(ZOOM_KEY, String(z));
    setZoomState(z);
  }

  return (
    <AppContext.Provider
      value={{
        secReasons, revReasons, refreshReasonCodes, presence, setPresence, clearPresenceFor,
        theme, toggleTheme, zoom, setZoom,
      }}
    >
      {children}
    </AppContext.Provider>
  );
}

export function useAppContext(): AppContextValue {
  const ctx = useContext(AppContext);
  if (!ctx) throw new Error("useAppContext debe usarse dentro de <AppProvider>");
  return ctx;
}
