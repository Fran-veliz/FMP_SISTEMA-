import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "./api";
import { ApiError, fetchResponse, request, setAuthToken } from "./http";

afterEach(() => {
  setAuthToken(null);
  vi.unstubAllGlobals();
});

function mockResponse(body: BodyInit | null, status = 200, headers?: HeadersInit) {
  const fetchMock = vi.fn().mockResolvedValue(new Response(body, { status, headers }));
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

describe("transporte HTTP", () => {
  it("conserva las cabeceras del cliente y agrega el token de sesion", async () => {
    const fetchMock = mockResponse('{"id":1}');
    setAuthToken("test-token");
    await request("/flights", {
      method: "POST", body: '{"vuelo":"LPE123"}', headers: { "X-Trace": "test" },
    });
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/flights");
    expect(init.headers.get("X-Shift-Token")).toBe("test-token");
    expect(init.headers.get("X-Trace")).toBe("test");
    expect(init.headers.get("Content-Type")).toBe("application/json");
  });

  it("deja de enviar el token al cerrar la sesion", async () => {
    const fetchMock = mockResponse("{}");
    setAuthToken("old-token");
    setAuthToken(null);
    await api.activeShifts();
    expect(fetchMock.mock.calls[0][1].headers.has("X-Shift-Token")).toBe(false);
    expect(api.wsProtocols()).toBeUndefined();
  });

  it.each(["itinerary", "history"])("sube %s como multipart autenticado", async (kind) => {
    const fetchMock = mockResponse('{"filas_aceptadas":1}');
    setAuthToken("upload-token");
    const file = new File(["example"], "itinerary.csv", { type: "text/csv" });
    if (kind === "itinerary") {
      await api.previewItinerary("2026-06-03", file, {
        alcance: "dia", estacion: "SPZO", hoja: "Hoja 1",
      });
    } else {
      await api.previewFlightHistory("SUR", "2026-06-03", file);
    }
    const [url, init] = fetchMock.mock.calls[0];
    expect(init.headers.get("X-Shift-Token")).toBe("upload-token");
    expect(init.headers.has("Content-Type")).toBe(false);
    expect(init.body).toBeInstanceOf(FormData);
    expect((init.body.get("file") as File).name).toBe("itinerary.csv");
    if (kind === "itinerary") {
      const params = new URL(url, "http://localhost").searchParams;
      expect(params.get("estacion")).toBe("SPZO");
      expect(params.get("hoja")).toBe("Hoja 1");
    }
  });

  it("muestra el detalle de una carga rechazada sin envolverlo en JSON", async () => {
    mockResponse('{"detail":"Estacion incorrecta"}', 422);
    await expect(api.uploadItinerary("2026-06-03", new File(["x"], "x.csv"), {
      alcance: "dia", estacion: "SPZO",
    })).rejects.toMatchObject({ status: 422, message: "Estacion incorrecta" });
  });

  it("no muestra el input sensible incluido en los errores de validacion", async () => {
    mockResponse(JSON.stringify({
      detail: [{ loc: ["body", "pin"], msg: "PIN demasiado corto", input: "secret-pin" }],
    }), 422);
    const error = await request("/shifts/clock-in").catch((error: unknown) => error);
    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ message: "body.pin: PIN demasiado corto", status: 422 });
    expect(String(error)).not.toContain("secret-pin");
  });

  it("conserva el identificador que soporte necesita para ubicar el error", async () => {
    mockResponse('{"detail":"Error interno","codigo":"body-id"}', 500, {
      "X-Request-Id": "request-id",
    });
    await expect(request("/flights")).rejects.toMatchObject({
      requestId: "request-id", message: "Error interno [request-id]", status: 500,
    });
  });

  it("usa el codigo del cuerpo cuando el proxy no expone la cabecera", async () => {
    mockResponse('{"detail":"Error interno","codigo":"body-id"}', 500);
    await expect(request("/flights")).rejects.toMatchObject({ requestId: "body-id" });
  });

  it("maneja errores HTML del proxy sin mostrarlos como contenido de la app", async () => {
    mockResponse("<html>Bad gateway</html>", 502, { "Content-Type": "text/html" });
    await expect(fetchResponse("/flights/export")).rejects.toMatchObject({
      status: 502, message: "Error HTTP 502",
    });
  });

  it("conserva los mensajes de error en texto plano", async () => {
    mockResponse("Servicio no disponible", 503, { "Content-Type": "text/plain" });
    await expect(request("/flights")).rejects.toThrow("Servicio no disponible");
  });

  it("acepta una eliminacion 204 sin intentar leer JSON", async () => {
    mockResponse(null, 204);
    await expect(api.deleteFlight(1)).resolves.toBeUndefined();
  });

  it("mantiene distinguibles los errores de red y los errores de la API", async () => {
    const networkError = new TypeError("Failed to fetch");
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(networkError));
    await expect(request("/flights")).rejects.toBe(networkError);
  });
});
