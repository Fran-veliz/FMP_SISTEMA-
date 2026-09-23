import type { Flight } from "../types";

export type FlightStatus = "cancelado" | "alerta" | "en-gestion" | "confirmado" | "nuevo";

const HHMM_RE = /^([01]\d|2[0-3])[0-5]\d$/;

export function isValidHHMM(value: string | null | undefined): boolean {
  if (!value) return true; // vacío no es inválido, solo incompleto
  return HHMM_RE.test(value.trim());
}

/** Construye una clave de Flight tipo "etd2"/"ctot3"/etc. a partir de un
 * prefijo y un nivel: único punto donde se afirma que la plantilla produce
 * una clave real de Flight, en vez de castear el objeto entero a `any` en
 * cada acceso dinámico. */
function levelKey(prefix: "etd" | "ctot" | "sec" | "rev", level: number): keyof Flight {
  return `${prefix}${level}` as keyof Flight;
}

/** Nivel 2+ requiere motivo (SEC y/o REV); nivel 1 es la asignación inicial
 * y no lo necesita (confirmado con el operador). */
const LEVELS_REQUIRING_REASON: Record<string, number> = {
  sec2: 2, rev2: 2, sec3: 3, rev3: 3, sec4: 4, rev4: 4, sec5: 5,
};

export function needsReason(flight: Flight, key: string): boolean {
  const level = LEVELS_REQUIRING_REASON[key];
  if (!level) return false;
  const etd = flight[levelKey("etd", level)];
  const ctot = flight[levelKey("ctot", level)];
  if (!etd && !ctot) return false;
  const sec = flight[levelKey("sec", level)];
  // El Nivel 5 es el último: no existe REV5 que lo justifique -- null en vez
  // de un string truthy, para que no bloquee siempre la alerta de SEC5
  // faltante.
  const rev = level <= 4 ? flight[levelKey("rev", level)] : null;
  return !sec && !rev;
}

const TIME_FIELDS: (keyof Flight)[] = [
  "hora", "eta_aircon", "etd1", "ctot1", "etd2", "ctot2", "etd3", "ctot3", "etd4", "ctot4",
  "etd5", "ctot5",
  "h_rev1", "h_rev2", "h_rev3", "h_rev4",
];

export function flightHasAlert(flight: Flight): boolean {
  if (needsReason(flight, "sec2") || needsReason(flight, "rev2")) return true;
  if (needsReason(flight, "sec3") || needsReason(flight, "rev3")) return true;
  if (needsReason(flight, "sec4") || needsReason(flight, "rev4")) return true;
  if (needsReason(flight, "sec5")) return true;
  return TIME_FIELDS.some((f) => !isValidHHMM(flight[f] as string | null));
}

export function computeFlightStatus(flight: Flight): FlightStatus {
  if (flight.cancelado) return "cancelado";
  if (flightHasAlert(flight)) return "alerta";
  if (flight.ctot1) return flight.eta_aircon_calculado ? "confirmado" : "en-gestion";
  return "nuevo";
}

/** Sin coincidencia en el itinerario DGAC: puede ser militar/policial, pero
 * el operador FMP debe verificar el permiso antes de autorizar el despegue. */
export function lacksItinerary(flight: Flight): boolean {
  return !flight.cancelado && !flight.slot_arr_dgac;
}

/** Todavía no despegó: la grilla no tiene su hora real de salida (DEP).
 *
 * Es el trabajo que le queda abierto al operador en la jornada. Un vuelo
 * cancelado no cuenta: no va a despegar, así que no está pendiente de nada.
 *
 * Reemplaza al filtro "Próxima hora", que miraba el ETA y marcaba los que
 * LLEGAN en la hora en curso o la siguiente. Era la pregunta equivocada: lo
 * que la FMP necesita ver de un vistazo es qué le falta cerrar, no qué
 * aterriza pronto. */
export function isPending(flight: Flight): boolean {
  return !flight.cancelado && !flight.dep;
}

export type QuickFilter = "all" | "alert" | "cancelled" | "pending" | "noItin";

export interface QuickFilterCounts {
  all: number;
  alert: number;
  cancelled: number;
  pending: number;
  noItin: number;
}

export function computeQuickFilterCounts(flights: Flight[]): QuickFilterCounts {
  return {
    all: flights.length,
    alert: flights.filter(flightHasAlert).length,
    cancelled: flights.filter((f) => f.cancelado).length,
    pending: flights.filter(isPending).length,
    noItin: flights.filter(lacksItinerary).length,
  };
}

export function applyQuickFilter(flights: Flight[], filter: QuickFilter): Flight[] {
  if (filter === "alert") return flights.filter(flightHasAlert);
  if (filter === "cancelled") return flights.filter((f) => f.cancelado);
  if (filter === "pending") return flights.filter(isPending);
  if (filter === "noItin") return flights.filter(lacksItinerary);
  return flights;
}
