export const API_BASE_URL = (import.meta.env.VITE_API_URL || "/api").replace(/\/+$/, "");

let authToken: string | null = null;

export function setAuthToken(token: string | null): void {
  authToken = token;
}

export function wsProtocols(): string[] | undefined {
  return authToken ? ["ctot-shift-token", authToken] : undefined;
}

export class ApiError extends Error {
  readonly status: number;
  readonly requestId: string | null;

  constructor(status: number, message: string, requestId: string | null = null) {
    super(requestId ? `${message} [${requestId}]` : message);
    this.name = "ApiError";
    this.status = status;
    this.requestId = requestId;
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

async function responseError(response: Response): Promise<ApiError> {
  const body = await response.text();
  let message = `Error HTTP ${response.status}`;
  let requestId = response.headers.get("X-Request-Id");
  let payload: unknown;
  try {
    payload = JSON.parse(body);
  } catch {
    if (response.headers.get("Content-Type")?.startsWith("text/plain") && body.trim()) {
      message = body.trim();
    }
  }
  if (isRecord(payload)) {
    if (typeof payload.codigo === "string") requestId ??= payload.codigo;
    if (typeof payload.detail === "string") {
      message = payload.detail;
    } else if (Array.isArray(payload.detail)) {
      // Los errores de Pydantic incluyen el input: puede contener el PIN.
      // Solo se muestran la ubicacion y el mensaje de validacion.
      const details = payload.detail.filter(isRecord).flatMap((detail) => {
        if (typeof detail.msg !== "string") return [];
        const location = Array.isArray(detail.loc)
          ? detail.loc.filter((part) => typeof part === "string" || typeof part === "number").join(".")
          : "";
        return [location ? `${location}: ${detail.msg}` : detail.msg];
      });
      if (details.length) message = details.join("; ");
    }
  }
  return new ApiError(response.status, message, requestId);
}

export async function fetchResponse(path: string, init?: RequestInit): Promise<Response> {
  const headers = new Headers(init?.headers);
  if (authToken) headers.set("X-Shift-Token", authToken);
  if (typeof init?.body === "string" && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  const response = await fetch(`${API_BASE_URL}${path}`, { ...init, headers });
  if (!response.ok) throw await responseError(response);
  return response;
}

export async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetchResponse(path, init);
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

export function uploadFile<T>(path: string, file: File): Promise<T> {
  const form = new FormData();
  form.append("file", file);
  return request<T>(path, { method: "POST", body: form });
}
