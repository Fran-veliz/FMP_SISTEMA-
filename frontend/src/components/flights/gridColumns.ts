import type { ColumnDef } from "./FlightRow";
import type { Flight } from "../../types";

/* Semántica de cada nivel (aclarada por el área, jul 2026 — ver DECISIONES.md):
   ETD n    = hora que SOLICITA el piloto
   CTOT n   = hora que ASIGNA el operador FMP
   SEC n    = motivo, solo si la hora asignada difiere de la solicitada
   H. REV n = hora de la NUEVA llamada pidiendo revisión (abre el nivel n+1)
   REV n    = motivo de esa revisión

   Agrupación visual (especificación del área, jul 2026): H. REV n / REV n
   ABREN el nivel n+1, así que se muestran y colorean como parte del bloque
   de ESE nivel siguiente (primeras dos columnas del grupo), no del nivel
   donde vive el campo internamente. Por eso el campo `h_rev1`/`rev1` se
   etiqueta "H.REV2"/"REV2" y va en el grupo "n2" -- la clave (key) sigue
   siendo la real de Flight, solo cambian la etiqueta y el grupo visual. El
   Nivel 1 nunca lleva REV: es la asignación inicial, no puede abrir nada. */
export const COLUMNS: ColumnDef[] = [
  { key: "hora", label: "HORA", width: 60, time: true, group: "id", hint: "Hora UTC de la comunicación (se registra sola)" },
  { key: "d_ats", label: "D. ATS", width: 60, group: "id", hint: "Dependencia ATS con la que se comunica" },
  // Ancha a propósito: además del call sign completo (que nunca se recorta),
  // esta celda lleva la insignia SIN ITIN., la de CANCELADO, el chip de
  // presencia y el botón de historial. El área prefiere ocupar más ancho
  // antes que esconder cualquiera de esas señales.
  { key: "vuelo", label: "VUELO", width: 190, group: "id", hint: "Call sign (inmutable una vez registrado)" },
  { key: "adep", label: "ADEP", width: 60, group: "id", hint: "Aeródromo de salida" },
  { key: "dep", label: "DEP", width: 60, time: true, group: "id", hint: "Hora real de despegue (último dato de la operación)" },
  { key: "eta_aircon", label: "ETA AIRCON", width: 95, time: true, group: "itin", hint: "Hora de llegada según el monitor AIRCON" },
  { key: "eta_aircon_calculado", label: "ETA CALC.", width: 82, group: "itin", hint: "ETA recalculado con el último CTOT aplicado (automático)" },
  { key: "slot_arr_dgac", label: "SLOT ARR DGAC", width: 112, group: "gac", hint: "Hora aprobada por la DGAC según el itinerario (automático)" },

  { key: "etd1", label: "ETD 1", width: 60, time: true, group: "n1", shade: "pastel", hint: "Hora que solicita el piloto" },
  { key: "ctot1", label: "CTOT 1", width: 60, time: true, group: "n1", shade: "pastel", hint: "Hora que asigna el operador FMP" },
  { key: "sec1", label: "SEC1", width: 90, group: "n1", shade: "pastel", hint: "Motivo, solo si la hora asignada difiere de la solicitada" },

  { key: "h_rev1", label: "H.REV2", width: 60, time: true, group: "n2", shade: "strong", hint: "Hora de la nueva llamada pidiendo revisión (se completa sola al elegir el motivo REV) — abre la Revisión 2" },
  { key: "rev1", label: "REV2", width: 90, group: "n2", shade: "strong", hint: "Motivo de la revisión que abre la Revisión 2" },
  { key: "etd2", label: "ETD 2", width: 60, time: true, group: "n2", shade: "pastel", hint: "Nueva hora que solicita el piloto" },
  { key: "ctot2", label: "CTOT 2", width: 60, time: true, group: "n2", shade: "pastel", hint: "Nueva hora que asigna el operador FMP" },
  { key: "sec2", label: "SEC2", width: 90, group: "n2", shade: "pastel", hint: "Motivo, solo si la hora asignada difiere de la solicitada" },

  { key: "h_rev2", label: "H.REV3", width: 60, time: true, group: "n3", shade: "strong", hint: "Hora de la nueva llamada pidiendo revisión (se completa sola al elegir el motivo REV) — abre la Revisión 3" },
  { key: "rev2", label: "REV3", width: 90, group: "n3", shade: "strong", hint: "Motivo de la revisión que abre la Revisión 3" },
  { key: "etd3", label: "ETD 3", width: 60, time: true, group: "n3", shade: "pastel", hint: "Nueva hora que solicita el piloto" },
  { key: "ctot3", label: "CTOT 3", width: 60, time: true, group: "n3", shade: "pastel", hint: "Nueva hora que asigna el operador FMP" },
  { key: "sec3", label: "SEC3", width: 90, group: "n3", shade: "pastel", hint: "Motivo, solo si la hora asignada difiere de la solicitada" },

  { key: "h_rev3", label: "H.REV4", width: 60, time: true, group: "n4", shade: "strong", hint: "Hora de la nueva llamada pidiendo revisión (se completa sola al elegir el motivo REV) — abre la Revisión 4" },
  { key: "rev3", label: "REV4", width: 90, group: "n4", shade: "strong", hint: "Motivo de la revisión que abre la Revisión 4" },
  { key: "etd4", label: "ETD 4", width: 60, time: true, group: "n4", shade: "pastel", hint: "Nueva hora que solicita el piloto" },
  { key: "ctot4", label: "CTOT 4", width: 60, time: true, group: "n4", shade: "pastel", hint: "Nueva hora que asigna el operador FMP" },
  { key: "sec4", label: "SEC4", width: 90, group: "n4", shade: "pastel", hint: "Motivo, solo si la hora asignada difiere de la solicitada" },

  { key: "h_rev4", label: "H.REV5", width: 60, time: true, group: "n5", shade: "strong", hint: "Hora de la nueva llamada pidiendo revisión (se completa sola al elegir el motivo REV) — abre la Revisión 5" },
  { key: "rev4", label: "REV5", width: 90, group: "n5", shade: "strong", hint: "Motivo de la revisión que abre la Revisión 5" },
  { key: "etd5", label: "ETD 5", width: 60, time: true, group: "n5", shade: "pastel", hint: "Nueva hora que solicita el piloto" },
  { key: "ctot5", label: "CTOT 5", width: 60, time: true, group: "n5", shade: "pastel", hint: "Nueva hora que asigna el operador FMP" },
  { key: "sec5", label: "SEC5", width: 90, group: "n5", shade: "pastel", hint: "Motivo, solo si la hora asignada difiere de la solicitada" },

  { key: "dla_minutos", label: "DLA (min)", width: 82, group: "result", hint: "Demora: CTOT confirmado menos ETD solicitado de la revisión activa (automático)" },
  // Texto libre: ningun ancho garantiza que entre siempre, pero 280px
  // cubre las observaciones reales del historico. Ademas la celda muestra
  // el texto completo al pasar el mouse (ver EditableText).
  { key: "observaciones", label: "OBSERVACIONES", width: 280, group: "result" },
];

/* Columnas que no vienen de COLUMNS: la de N° (fija a la izquierda) y la de
   acciones de fila (CNL / eliminar). Se declaran acá para que entren en el
   <colgroup> y en el ancho mínimo de la tabla. */
export const ROW_NUMBER_COL_WIDTH = 44;
/* 80 y no 72: con 72 los dos botones sumaban 64px contra 63px de espacio
   útil (72 menos el borde y el padding de la celda) y el tacho quedaba
   recortado 1px por el overflow:hidden de .flight-grid td. Medido en el
   navegador con los anchos reales del Nivel 1. */
export const ACTIONS_COL_WIDTH = 80;

export const GROUP_LABELS: Record<string, string> = {
  id: "Identificación", itin: "Itinerario", gac: "", n1: "CTOT", n2: "Revisión 2", n3: "Revisión 3", n4: "Revisión 4", n5: "Revisión 5", result: "Resultado",
};

export function groupHeaderCells(columns: ColumnDef[]) {
  const cells: { group: string; span: number }[] = [];
  for (const col of columns) {
    const last = cells[cells.length - 1];
    if (last && last.group === col.group) last.span += 1;
    else cells.push({ group: col.group, span: 1 });
  }
  return cells;
}

/* La operación normal usa solo el Nivel 1; los niveles 2–5 aparecen en casos
 * puntuales. La grilla arranca mostrando hasta el Nivel 1 y el operador
 * expande a demanda — pero si un vuelo ya trae datos en un nivel superior,
 * ese nivel se muestra siempre (nunca se oculta información cargada).
 * `h_rev(n-1)`/`rev(n-1)` cuentan para el nivel n: son los campos que lo
 * abren y ahora se agrupan/etiquetan visualmente como parte de ese nivel
 * (ver COLUMNS más arriba), así que también deben revelarlo si ya tienen
 * datos cargados. */
const LEVEL_KEYS: Record<number, string[]> = {
  2: ["etd2", "ctot2", "sec2", "h_rev1", "rev1"],
  3: ["etd3", "ctot3", "sec3", "h_rev2", "rev2"],
  4: ["etd4", "ctot4", "sec4", "h_rev3", "rev3"],
  5: ["etd5", "ctot5", "sec5", "h_rev4", "rev4"],
};

export function highestLevelWithData(flights: Flight[]): number {
  for (let lvl = 5; lvl >= 2; lvl--) {
    if (flights.some((f) => LEVEL_KEYS[lvl].some((k) => (f as any)[k]))) return lvl;
  }
  return 1;
}

export function levelOfGroup(group: string): number | null {
  const m = /^n([1-5])$/.exec(group);
  return m ? Number(m[1]) : null;
}

/* Motivo de revisión → campo de hora que se autocompleta al elegirlo */
export const REV_TO_HREV: Record<string, string> = { rev1: "h_rev1", rev2: "h_rev2", rev3: "h_rev3", rev4: "h_rev4" };
