import type {
  Estacion,
  Flight,
  FlightHistoryEntry,
  FlightHistoryImportEntry,
  FlightHistoryImportReport,
  FlightHistoryPreview,
  ForecastResponse,
  ItineraryLookup,
  ItineraryAlcance,
  ItineraryPreview,
  ItineraryUploadEntry,
  ItineraryUploadReport,
  Position,
  ReasonCategory,
  ReasonCode,
  Sector,
  Shift,
} from "../types";

import { API_BASE_URL, fetchResponse, request, uploadFile, wsProtocols } from "./http";

export { ApiError, setAuthToken } from "./http";

// El pronóstico lo piden dos componentes a la vez: el mini de la barra de
// pestañas (cada 20 s) y el gráfico completo (cada 15 s). Guardar la última
// respuesta permite que el gráfico pinte con datos apenas se monta, en vez de
// quedarse con los ejes vacíos esperando su propia petición -- que es la mitad
// del tiempo que tardaba en aparecer. Igual dispara la petición fresca detrás.
const FORECAST_TTL_MS = 25_000;
const forecastCache = new Map<string, { at: number; data: ForecastResponse }>();

export function forecastEnCache(flightDate: string, sector?: Sector): ForecastResponse | null {
  const hit = forecastCache.get(`${flightDate}|${sector ?? ""}`);
  if (!hit || Date.now() - hit.at > FORECAST_TTL_MS) return null;
  return hit.data;
}


export interface ItineraryUploadOpts {
  alcance: ItineraryAlcance;
  /** Hoja del libro a leer. Vacio = la elige el backend. */
  hoja?: string | null;
  /** Aerodromo (OACI) cuyo itinerario se reemplaza. */
  estacion: string;
}

// Los mismos parámetros para la vista previa y para la carga real: si
// difirieran, el operador estaría confirmando algo distinto de lo que revisó.
function itineraryParams(effectiveFrom: string, opts: ItineraryUploadOpts): string {
  const params = new URLSearchParams({
    effective_from: effectiveFrom,
    alcance: opts.alcance,
    // Sin esto el backend toma su valor por omision (Lima) y el itinerario de
    // cualquier otro aerodromo entraba cargado como si fuera de Lima.
    estacion: opts.estacion,
  });
  // Sin hoja elegida el backend toma la que mejor cubre la fecha; al confirmar
  // se manda la misma que se reviso, para no aplicar una distinta.
  if (opts.hoja) params.set("hoja", opts.hoja);
  return params.toString();
}

export const api = {
  wsUrl(sector: Sector) {
    const wsBase = API_BASE_URL.replace(/^http/, "ws");
    return `${wsBase}/ws/${sector}`;
  },

  /* El token del WebSocket viaja como subprotocolo del handshake y ya no en
     la query string: las URLs quedan registradas en los logs del servidor, en
     el historial del navegador y en los de cualquier proxy, así que un token
     ahí adentro se filtra a lugares que nadie trata como secretos. El
     subprotocolo va en una cabecera del handshake, que no se registra. */
  wsProtocols,

  /* La exportación ya no puede ser un <a href> suelto: /flights/export exige
     el turno en la cabecera X-Shift-Token, y una navegación del navegador no
     la manda. Se baja con fetch y se entrega como archivo desde memoria. */
  async downloadExport(flightDate: string, sector?: Sector): Promise<void> {
    const q = sector ? `flight_date=${flightDate}&sector=${sector}` : `flight_date=${flightDate}`;
    const resp = await fetchResponse(`/flights/export?${q}`);
    const blob = await resp.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `ctot_${flightDate}${sector ? "_" + sector : ""}.csv`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  },

  listFlights(sector: Sector, flightDate: string) {
    return request<Flight[]>(`/flights?sector=${sector}&flight_date=${flightDate}`);
  },

  flightHistory(flightId: number) {
    return request<FlightHistoryEntry[]>(`/flights/${flightId}/history`);
  },

  createFlight(payload: Partial<Flight> & { sector: Sector; flight_date: string; vuelo: string }) {
    return request<Flight>("/flights", { method: "POST", body: JSON.stringify(payload) });
  },

  updateFlight(id: number, payload: Partial<Flight>) {
    return request<Flight>(`/flights/${id}`, { method: "PATCH", body: JSON.stringify(payload) });
  },

  deleteFlight(id: number, reason?: string) {
    const q = reason ? `?reason=${encodeURIComponent(reason)}` : "";
    return request<void>(`/flights/${id}${q}`, { method: "DELETE" });
  },

  clockIn(operator_name: string, position: Position, pin: string) {
    return request<Shift>("/shifts/clock-in", {
      method: "POST",
      body: JSON.stringify({ operator_name, position, pin }),
    });
  },

  clockOut(shiftId: number, handoverNote?: string) {
    return request<Shift>(`/shifts/${shiftId}/clock-out`, {
      method: "POST",
      body: JSON.stringify({ handover_note: handoverNote || null }),
    });
  },

  lastHandover(position: Position) {
    return request<Shift | null>(`/shifts/last-handover?position=${encodeURIComponent(position)}`);
  },

  activeShifts() {
    return request<Shift[]>("/shifts/active");
  },

  listShifts(shiftDate: string) {
    return request<Shift[]>(`/shifts?shift_date=${shiftDate}`);
  },

  serverTime() {
    return request<{ utc: string }>("/server-time");
  },

  forecast(flightDate: string, sector?: Sector) {
    const q = sector ? `flight_date=${flightDate}&sector=${sector}` : `flight_date=${flightDate}`;
    return request<ForecastResponse>(`/forecast?${q}`).then((data) => {
      forecastCache.set(`${flightDate}|${sector ?? ""}`, { at: Date.now(), data });
      return data;
    });
  },

  reasonCodes(category: ReasonCategory) {
    return request<ReasonCode[]>(`/reason-codes?category=${category}`);
  },

  addReasonCode(category: ReasonCategory, code: string, description?: string) {
    return request<ReasonCode>("/reason-codes", {
      method: "POST",
      body: JSON.stringify({ category, code, description: description || null }),
    });
  },

  lookupItinerary(flightDate: string, callSign: string) {
    return request<ItineraryLookup>(
      `/itinerary/lookup?flight_date=${flightDate}&call_sign=${encodeURIComponent(callSign)}`
    );
  },

  estaciones() {
    return request<Estacion[]>("/estaciones");
  },

  itineraryUploads() {
    return request<ItineraryUploadEntry[]>("/itinerary/uploads");
  },

  // Paso 1: valida y calcula el impacto sin tocar nada.
  previewItinerary(effectiveFrom: string, file: File, opts: ItineraryUploadOpts) {
    return uploadFile<ItineraryPreview>(
      `/itinerary/preview?${itineraryParams(effectiveFrom, opts)}`,
      file
    );
  },

  // Paso 2: aplica de verdad. `confirmado=true` es obligatorio del lado del
  // backend, así que no hay forma de aplicar sin haber pasado por el preview.
  uploadItinerary(effectiveFrom: string, file: File, opts: ItineraryUploadOpts) {
    return uploadFile<ItineraryUploadReport>(
      `/itinerary/upload?${itineraryParams(effectiveFrom, opts)}&confirmado=true`,
      file
    );
  },

  flightHistoryImports() {
    return request<FlightHistoryImportEntry[]>("/flights/import-history/uploads");
  },

  previewFlightHistory(sector: Sector, flightDate: string, file: File) {
    return uploadFile<FlightHistoryPreview>(
      `/flights/import-history/preview?sector=${sector}&flight_date=${flightDate}`,
      file
    );
  },

  importFlightHistory(
    sector: Sector,
    flightDate: string,
    file: File,
    reemplazar = false
  ) {
    return uploadFile<FlightHistoryImportReport>(
      `/flights/import-history?sector=${sector}&flight_date=${flightDate}` +
        (reemplazar ? "&reemplazar=true" : ""),
      file
    );
  },
};
