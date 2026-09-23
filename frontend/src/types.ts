export type Sector = "SUR" | "NOR";
export type Position = "FMP SUR" | "FMP NOR" | "FMP CUSCO";
export type ReasonCategory = "SEC" | "REV";

export interface Flight {
  id: number;
  sector: Sector;
  flight_date: string;
  /** Correlativo de la fila en la grilla de ese sector y ese dia. No es el
   *  numero del vuelo. */
  numero_fila: number;
  hora: string | null;
  d_ats: string | null;
  vuelo: string;
  adep: string | null;
  dep: string | null;
  eta_aircon: string | null;
  eta_aircon_calculado: string | null;
  slot_arr_dgac: string | null;
  etd1: string | null;
  ctot1: string | null;
  sec1: string | null;
  h_rev1: string | null;
  rev1: string | null;
  etd2: string | null;
  ctot2: string | null;
  sec2: string | null;
  h_rev2: string | null;
  rev2: string | null;
  etd3: string | null;
  ctot3: string | null;
  sec3: string | null;
  h_rev3: string | null;
  rev3: string | null;
  etd4: string | null;
  ctot4: string | null;
  sec4: string | null;
  h_rev4: string | null;
  rev4: string | null;
  etd5: string | null;
  ctot5: string | null;
  sec5: string | null;
  dla_minutos: number | null;
  observaciones: string | null;
  cancelado: boolean;
  updated_at: string;
  updated_by: string | null;
}

export interface FlightHistoryEntry {
  id: number;
  field_name: string;
  old_value: string | null;
  new_value: string | null;
  operator_name: string | null;
  changed_at: string;
}

export interface Shift {
  id: number;
  operator_name: string;
  position: Position;
  start_time: string;
  end_time: string | null;
  duration_minutes: number | null;
  handover_note: string | null;
  // Perfil genérico de solo lectura (ej. DGAC): puede ver todo en vivo pero
  // ningún endpoint de escritura acepta su token (ver require_writable_shift
  // en el backend). El frontend además oculta los controles de edición.
  read_only: boolean;
  can_export: boolean;
  export_max_weeks: number | null;
  /** Horas que lleva abierto el turno, medidas con el reloj del servidor
   *  (corregido por NTP), no con el del navegador. */
  horas_en_turno: number;
  /** Pasó el máximo previsto para una posición. Es un aviso: la sesión sigue
   *  siendo válida, no se corta a nadie en plena operación. */
  excede_maximo: boolean;
  /** El umbral en horas, para no hardcodearlo del lado del frontend. */
  maximo_horas: number;
  view_year_only: boolean;
  // Solo presente en la respuesta de /shifts/clock-in (ver ShiftAuthOut en
  // el backend) -- nunca en /shifts/active o /shifts (no se filtra el token
  // a quien solo está mirando la bitácora).
  session_token?: string;
}

/** Aerodromo del que la FMP recibe itinerario. No es lo mismo que un
 *  AirportCode: ese es el mapeo IATA-OACI de cualquier aeropuerto que aparezca
 *  como origen o destino; esto es el catalogo corto de los que tienen
 *  itinerario propio. */
export interface Estacion {
  codigo_oaci: string;
  nombre: string;
  capacidad_declarada: number | null;
}

export interface ItineraryUploadEntry {
  id: number;
  uploaded_at: string;
  uploaded_by: string | null;
  filename: string | null;
  /** Aerodromo (OACI) cuyo itinerario se cargo. */
  estacion: string;
  effective_from: string;
  alcance: ItineraryAlcance;
  filas_aceptadas: number;
  filas_rechazadas: number;
}

export interface ItineraryLookup {
  found: boolean;
  fuente: "hoy" | "dia_anterior" | null;
  hora_utc: string | null;
  aerodromo: string | null;
}

export interface ForecastBucket {
  rango_hora: string;
  pronosticado: number;
  /** Subconjunto de `pronosticado`: arribos con origen nacional (OACI SP..). */
  pronosticado_llegadas_nacionales: number;
  /** Subconjunto de las llegadas nacionales: arribos que vienen de Cusco. */
  pronosticado_llegadas_cusco: number;
  /** Las nacionales que no son de Cusco: el complemento exacto, para apilar. */
  pronosticado_llegadas_nacionales_otras: number;
  /** Subconjunto de `pronosticado`: los despegues del itinerario. */
  pronosticado_despegues: number;
  trabajado: number;
  capacidad_maxima: number;
}

export interface ForecastResponse {
  sector: Sector | null;
  fecha: string;
  buckets: ForecastBucket[];
}

export interface ItineraryRowError {
  row: number;
  motivo: string;
}

export interface ItineraryUploadReport {
  filas_aceptadas: number;
  filas_rechazadas: number;
  errores: ItineraryRowError[];
}

// "dia" = reemplaza solo esa fecha (carga diaria suelta).
// "desde" = de esa fecha en adelante (actualización de temporada DGAC).
export type ItineraryAlcance = "dia" | "desde";

export interface ItineraryPreviewDay {
  fecha: string;
  arribos: number;
  salidas: number;
}

/** Una hoja del libro que podria leerse como itinerario. El archivo de la
 *  DGAC trae varias (la de la temporada anterior, la cruda sin convertir, la
 *  que arma el FMP), asi que el operador ve cual se leyo y puede cambiarla. */
export interface ItinerarySheet {
  nombre: string;
  filas: number;
  fecha_min: string | null;
  fecha_max: string | null;
  elegida: boolean;
}

export interface ItineraryPreview {
  /** Aerodromo cuyo itinerario se reemplazaria. */
  estacion: string;
  formato: "season" | "legacy" | "csv";
  hoja: string | null;
  hojas: ItinerarySheet[];
  alcance: ItineraryAlcance;
  filas_aceptadas: number;
  filas_rechazadas: number;
  errores: ItineraryRowError[];
  fecha_min: string | null;
  fecha_max: string | null;
  por_fecha: ItineraryPreviewDay[];
  filas_reemplazadas: number;
  dias_reemplazados: number;
  /** Vuelos de la grilla que quedan sin itinerario. Informativo: la carga no
   *  los modifica. */
  vuelos_sin_itinerario: string[];
  advertencias: string[];
}

export interface FlightHistoryImportRowError {
  row: number;
  motivo: string;
}

export interface FlightHistoryImportReport {
  filas_aceptadas: number;
  filas_rechazadas: number;
  /** Vuelos que se borraron para dejar lugar a esta carga. Cero si el día
   *  estaba vacío. Queda constancia de cada uno en el registro de
   *  eliminaciones. */
  vuelos_reemplazados: number;
  errores: FlightHistoryImportRowError[];
}

export interface FlightHistoryPreviewRow {
  vuelo: string;
  hora: string | null;
  adep: string | null;
  dep: string | null;
  eta_aircon: string | null;
  eta_aircon_calculado: string | null;
  slot_arr_dgac: string | null;
  etd1: string | null;
  ctot1: string | null;
  sec1: string | null;
  h_rev1: string | null;
  rev1: string | null;
  etd2: string | null;
  ctot2: string | null;
  sec2: string | null;
  etd3: string | null;
  ctot3: string | null;
  sec3: string | null;
  etd4: string | null;
  ctot4: string | null;
  sec4: string | null;
  etd5: string | null;
  ctot5: string | null;
  sec5: string | null;
  dla_minutos: number | null;
  observaciones: string | null;
  cancelado: boolean;
}

export interface FlightHistoryPreview {
  filas_aceptadas: number;
  filas_rechazadas: number;
  errores: FlightHistoryImportRowError[];
  rows: FlightHistoryPreviewRow[];
  vuelos_existentes: number;
}

export interface FlightHistoryImportEntry {
  id: number;
  sector: Sector;
  flight_date: string;
  uploaded_at: string;
  uploaded_by: string | null;
  filename: string | null;
  filas_aceptadas: number;
  filas_rechazadas: number;
}

export interface ReasonCode {
  id: number;
  category: ReasonCategory;
  code: string;
  description: string | null;
}

// Etiquetas de las columnas cuyo nombre no es autoexplicativo en la grilla.
export const FIELD_LABELS: Record<string, string> = {
  hora: "HORA",
  d_ats: "D. ATS",
  vuelo: "VUELO",
  adep: "ADEP",
  dep: "DEP",
  eta_aircon: "ETA AIRCON",
  etd1: "ETD 1", ctot1: "CTOT 1", sec1: "SEC1", h_rev1: "H. REV1", rev1: "REV1",
  etd2: "ETD 2", ctot2: "CTOT 2", sec2: "SEC2", h_rev2: "H. REV2", rev2: "REV2",
  etd3: "ETD 3", ctot3: "CTOT 3", sec3: "SEC3", h_rev3: "H. REV3", rev3: "REV3",
  etd4: "ETD 4", ctot4: "CTOT 4", sec4: "SEC4", h_rev4: "H. REV4", rev4: "REV4",
  etd5: "ETD 5", ctot5: "CTOT 5", sec5: "SEC5",
  observaciones: "OBSERVACIONES",
  cancelado: "CANCELADO",
};
