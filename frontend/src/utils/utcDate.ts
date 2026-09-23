/* Formateo de las marcas de tiempo que devuelve el backend.
 *
 * Dos detalles se repetían en cinco archivos y son fáciles de olvidar:
 *
 *  1. El backend envía UTC *naive* (sin sufijo Z). Sin agregárselo, el
 *     navegador lo interpreta como hora local y la marca se corre las horas
 *     de diferencia con Lima.
 *  2. Toda la operación se muestra en UTC, así que el formateo lleva siempre
 *     `timeZone: "UTC"` y `hour12: false`.
 *
 * Omitir cualquiera de los dos no rompe nada visible: muestra una hora
 * equivocada. Por eso viven acá y no copiados en cada componente.
 */

const LOCALE = "es-PE";

function desdeUtcNaive(iso: string): Date {
  return new Date(iso + "Z");
}

/** Solo la hora: "14:32". */
export function horaUtc(iso: string | null | undefined): string {
  if (!iso) return "-";
  return desdeUtcNaive(iso).toLocaleTimeString(LOCALE, {
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
    timeZone: "UTC",
  });
}

/** Día, mes y hora: "22 sept, 14:32". Con `conAnio`, agrega el año. */
export function fechaHoraUtc(iso: string, conAnio = false): string {
  return desdeUtcNaive(iso).toLocaleString(LOCALE, {
    day: "2-digit",
    month: "short",
    ...(conAnio ? { year: "numeric" as const } : {}),
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
    timeZone: "UTC",
  });
}

/** Fecha y hora completas, para el historial de un vuelo. */
export function fechaHoraCompletaUtc(iso: string): string {
  return desdeUtcNaive(iso).toLocaleString(LOCALE, { timeZone: "UTC", hour12: false });
}

/** Fecha de hoy en formato ISO (YYYY-MM-DD), que es como viaja `flight_date`. */
export function hoyIso(): string {
  return new Date().toISOString().slice(0, 10);
}

/** Hora UTC actual como "HHMM", que es el formato en que la grilla guarda las horas. */
export function horaHhmmUtc(): string {
  const d = new Date();
  return String(d.getUTCHours()).padStart(2, "0") + String(d.getUTCMinutes()).padStart(2, "0");
}

/** Solo la fecha UTC de una marca del backend: "2026-09-22".
 *
 * Sirve para decidir a qué jornada pertenece un instante. No se puede usar
 * `iso.slice(0, 10)`: el backend manda UTC naive y esa marca ya viene en UTC,
 * pero comparar cadenas a mano se rompe en cuanto alguien pasa una marca con
 * sufijo Z o un desplazamiento. Acá se convierte y se vuelve a leer en UTC. */
export function fechaIsoUtc(iso: string | null | undefined): string | null {
  if (!iso) return null;
  return desdeUtcNaive(iso).toISOString().slice(0, 10);
}

/** Día y mes de una marca: "22 sept". Para decir en qué jornada cae algo. */
export function diaMesUtc(iso: string): string {
  return desdeUtcNaive(iso).toLocaleDateString(LOCALE, {
    day: "2-digit",
    month: "short",
    timeZone: "UTC",
  });
}

/** Corre una fecha ISO (YYYY-MM-DD) la cantidad de días indicada. */
export function sumarDiasIso(fechaIso: string, dias: number): string {
  const d = new Date(fechaIso + "T00:00:00Z");
  d.setUTCDate(d.getUTCDate() + dias);
  return d.toISOString().slice(0, 10);
}
