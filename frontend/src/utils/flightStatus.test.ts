import { describe, expect, it } from "vitest";
import {
  applyQuickFilter,
  computeFlightStatus,
  computeQuickFilterCounts,
  flightHasAlert,
  isPending,
  isValidHHMM,
  lacksItinerary,
  needsReason,
} from "./flightStatus";
import type { Flight } from "../types";

function makeFlight(overrides: Partial<Flight> = {}): Flight {
  return {
    id: 1,
    sector: "SUR",
    flight_date: "2026-06-03",
    numero_fila: 1,
    hora: "2200",
    d_ats: null,
    vuelo: "LPE2026",
    adep: null,
    dep: null,
    eta_aircon: null,
    eta_aircon_calculado: null,
    slot_arr_dgac: "2200",
    etd1: null,
    ctot1: null,
    sec1: null,
    h_rev1: null,
    rev1: null,
    etd2: null,
    ctot2: null,
    sec2: null,
    h_rev2: null,
    rev2: null,
    etd3: null,
    ctot3: null,
    sec3: null,
    h_rev3: null,
    rev3: null,
    etd4: null,
    ctot4: null,
    sec4: null,
    h_rev4: null,
    rev4: null,
    etd5: null,
    ctot5: null,
    sec5: null,
    dla_minutos: null,
    observaciones: null,
    cancelado: false,
    updated_at: "2026-06-03T22:00:00",
    updated_by: null,
    ...overrides,
  };
}

describe("isValidHHMM", () => {
  it("acepta vacío/null como válido (incompleto, no inválido)", () => {
    expect(isValidHHMM(null)).toBe(true);
    expect(isValidHHMM(undefined)).toBe(true);
    expect(isValidHHMM("")).toBe(true);
  });

  it("acepta HHMM bien formado", () => {
    expect(isValidHHMM("2215")).toBe(true);
    expect(isValidHHMM("0000")).toBe(true);
    expect(isValidHHMM("2359")).toBe(true);
  });

  it("rechaza horas u minutos fuera de rango", () => {
    expect(isValidHHMM("2400")).toBe(false);
    expect(isValidHHMM("1060")).toBe(false);
    expect(isValidHHMM("99")).toBe(false);
    expect(isValidHHMM("abcd")).toBe(false);
  });
});

describe("needsReason", () => {
  it("nivel 1 nunca requiere motivo", () => {
    const flight = makeFlight({ etd1: "2200", ctot1: "2215" });
    expect(needsReason(flight, "sec1")).toBe(false);
  });

  it("false si el nivel no tiene datos cargados", () => {
    const flight = makeFlight();
    expect(needsReason(flight, "sec2")).toBe(false);
  });

  it("true si hay ETD/CTOT de nivel 2 pero falta SEC y REV", () => {
    const flight = makeFlight({ etd2: "2300", ctot2: "2315" });
    expect(needsReason(flight, "sec2")).toBe(true);
    expect(needsReason(flight, "rev2")).toBe(true);
  });

  it("false si ya hay SEC u REV cargado en ese nivel", () => {
    const flight = makeFlight({ etd2: "2300", ctot2: "2315", sec2: "SxTFC" });
    expect(needsReason(flight, "sec2")).toBe(false);
    expect(needsReason(flight, "rev2")).toBe(false);
  });

  it("nivel 4 no tiene REV (no aplica rev4)", () => {
    const flight = makeFlight({ etd4: "0100", ctot4: "0115" });
    expect(needsReason(flight, "sec4")).toBe(true);
  });
});

describe("flightHasAlert", () => {
  it("false para un vuelo nuevo sin datos", () => {
    expect(flightHasAlert(makeFlight())).toBe(false);
  });

  it("true si falta motivo en un nivel con datos", () => {
    expect(flightHasAlert(makeFlight({ etd2: "2300", ctot2: "2315" }))).toBe(true);
  });

  it("true si algún campo de hora es inválido", () => {
    expect(flightHasAlert(makeFlight({ etd1: "9999" }))).toBe(true);
  });
});

describe("computeFlightStatus", () => {
  it("cancelado gana sobre cualquier otro estado", () => {
    const flight = makeFlight({ cancelado: true, etd2: "2300", ctot2: "2315" });
    expect(computeFlightStatus(flight)).toBe("cancelado");
  });

  it("alerta si falta un motivo requerido", () => {
    const flight = makeFlight({ etd2: "2300", ctot2: "2315" });
    expect(computeFlightStatus(flight)).toBe("alerta");
  });

  it("nuevo sin CTOT1", () => {
    expect(computeFlightStatus(makeFlight())).toBe("nuevo");
  });

  it("en-gestion con CTOT1 pero sin ETA calculado", () => {
    const flight = makeFlight({ ctot1: "2215" });
    expect(computeFlightStatus(flight)).toBe("en-gestion");
  });

  it("confirmado con CTOT1 y ETA calculado", () => {
    const flight = makeFlight({ ctot1: "2215", eta_aircon_calculado: "0013" });
    expect(computeFlightStatus(flight)).toBe("confirmado");
  });
});

describe("lacksItinerary", () => {
  it("true si no hay slot y el vuelo no está cancelado", () => {
    expect(lacksItinerary(makeFlight({ slot_arr_dgac: null }))).toBe(true);
  });

  it("false si hay slot", () => {
    expect(lacksItinerary(makeFlight({ slot_arr_dgac: "2200" }))).toBe(false);
  });

  it("false si está cancelado (no se marca como \"sin itinerario\")", () => {
    expect(lacksItinerary(makeFlight({ slot_arr_dgac: null, cancelado: true }))).toBe(false);
  });
});

describe("isPending", () => {
  it("pendiente si no tiene hora de despegue", () => {
    expect(isPending(makeFlight())).toBe(true);
  });

  it("ya no está pendiente cuando se carga el DEP", () => {
    expect(isPending(makeFlight({ dep: "2304" }))).toBe(false);
  });

  it("un vuelo cancelado no está pendiente aunque no tenga DEP", () => {
    // No va a despegar: contarlo inflaría el trabajo que le queda al turno.
    expect(isPending(makeFlight({ cancelado: true }))).toBe(false);
  });

  it("el contador solo suma los que faltan", () => {
    const vuelos = [
      makeFlight(),
      makeFlight({ dep: "2304" }),
      makeFlight({ cancelado: true }),
      makeFlight(),
    ];
    expect(computeQuickFilterCounts(vuelos).pending).toBe(2);
    expect(applyQuickFilter(vuelos, "pending")).toHaveLength(2);
  });
});
