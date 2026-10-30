import type {
  Booking, CallDetail, CallRow, Health, IncomingCall, Metrics, Service, Slot, Sms, StaffMember, TurnOutcome,
} from "./types";

/**
 * Base URL of the FastAPI backend. Empty in development (Vite proxies /api, including the voice WebSocket).
 * In production set VITE_API_BASE_URL to the deployed API origin. The Gemini key lives on the server only.
 */
export const API_BASE = (import.meta.env.VITE_API_BASE_URL ?? "").replace(/\/$/, "");
const ACCESS_KEY = "phone-receptionist.access-code";

export class ApiError extends Error {
  status: number;
  code: string;
  details: Record<string, unknown> | null;

  constructor(status: number, code: string, message: string, details: Record<string, unknown> | null = null) {
    super(message);
    this.status = status;
    this.code = code;
    this.details = details;
  }
}

export function getAccessCode(): string | null {
  try {
    return localStorage.getItem(ACCESS_KEY);
  } catch {
    return null;
  }
}

export function setAccessCode(code: string | null) {
  try {
    if (code) localStorage.setItem(ACCESS_KEY, code);
    else localStorage.removeItem(ACCESS_KEY);
  } catch {
    /* storage unavailable: the code is simply not remembered */
  }
}

function headers(extra?: HeadersInit): Headers {
  const h = new Headers(extra);
  const code = getAccessCode();
  if (code) h.set("X-Access-Code", code);
  return h;
}

async function toError(res: Response): Promise<ApiError> {
  try {
    const body = await res.json();
    const err = body?.error;
    if (err?.code) return new ApiError(res.status, err.code, err.message ?? "Request failed.", err.details ?? null);
  } catch {
    /* non-JSON error body */
  }
  const fallback = res.status >= 500 ? "The server had a problem. Try again shortly." : `Request failed (${res.status}).`;
  return new ApiError(res.status, "http_error", fallback);
}

export async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${API_BASE}${path}`, { ...init, headers: headers(init.headers) });
  } catch {
    throw new ApiError(0, "network_error", "Cannot reach the server. Check your connection or the API URL.");
  }
  if (!res.ok) throw await toError(res);
  return (await res.json()) as T;
}

function json(method: string, body: unknown): RequestInit {
  return { method, body: JSON.stringify(body), headers: { "Content-Type": "application/json" } };
}

/** ws(s):// URL for the voice stream. Browsers cannot set headers on a WebSocket, so the access code is a query param. */
export function streamUrl(streamPath: string): string {
  const base = API_BASE || window.location.origin;
  const url = new URL(streamPath, base);
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  const code = getAccessCode();
  if (code) url.searchParams.set("code", code);
  return url.toString();
}

export const api = {
  health: () => request<Health>("/api/health"),
  services: () => request<Service[]>("/api/services"),
  staff: () => request<StaffMember[]>("/api/staff"),
  availability: (service: string, dateFrom: string, dateTo: string) =>
    request<Slot[]>(`/api/availability?${new URLSearchParams({ service, date_from: dateFrom, date_to: dateTo, limit: "200" })}`),
  bookings: (dateFrom: string, dateTo: string, includeCancelled = false) =>
    request<Booking[]>(`/api/bookings?${new URLSearchParams({ date_from: dateFrom, date_to: dateTo,
      include_cancelled: String(includeCancelled) })}`),
  createBooking: (body: { slot_id: string; name: string; phone_e164: string }, idempotencyKey: string) =>
    request<Booking>("/api/bookings", { ...json("POST", body), headers: { "Content-Type": "application/json",
      "Idempotency-Key": idempotencyKey } }),
  cancelBooking: (id: string, reason: string) => request<Booking>(`/api/bookings/${id}`, json("PATCH", { action: "cancel", reason })),
  rescheduleBooking: (id: string, newSlotId: string) =>
    request<Booking>(`/api/bookings/${id}`, json("PATCH", { action: "reschedule", new_slot_id: newSlotId })),
  startCall: (callerPhone: string | null, channel: "browser" | "text") =>
    request<IncomingCall>("/api/voice/incoming", json("POST", { caller_phone: callerPhone, channel })),
  sendTurn: (callId: string, text: string) => request<TurnOutcome>(`/api/calls/${callId}/turns`, json("POST", { text })),
  handoff: (callId: string) => request<{ state: string; summary: string }>(`/api/calls/${callId}/handoff`,
    json("POST", { reason: "caller_request" })),
  endCall: (callId: string) => request<{ outcome: string }>(`/api/calls/${callId}/end`, { method: "POST" }),
  calls: (limit = 50) => request<CallRow[]>(`/api/calls?limit=${limit}`),
  call: (id: string) => request<CallDetail>(`/api/calls/${id}`),
  handoffs: () => request<CallRow[]>("/api/handoffs"),
  updateHandoff: (id: string, status: "claimed" | "resolved") =>
    request<CallRow>(`/api/calls/${id}/handoff`, json("PATCH", { status })),
  sms: () => request<Sms[]>("/api/sms"),
  metrics: (days = 7) => request<Metrics>(`/api/metrics?days=${days}`),
};
