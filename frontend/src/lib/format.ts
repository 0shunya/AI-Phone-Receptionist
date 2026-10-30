import type { AgentState, Outcome } from "./types";

export const STATES: AgentState[] = ["GREET", "INTENT", "COLLECT", "CHECK_SLOTS", "CONFIRM", "BOOKED", "WRAP_UP"];

export const STATE_LABEL: Record<AgentState, string> = {
  GREET: "Greet", INTENT: "Intent", COLLECT: "Collect", CHECK_SLOTS: "Check slots", CONFIRM: "Confirm",
  BOOKED: "Booked", WRAP_UP: "Wrap up", HANDOFF: "Handoff",
};

export const OUTCOME_LABEL: Record<Outcome, string> = {
  in_progress: "In progress", booked: "Booked", rescheduled: "Rescheduled", cancelled: "Cancelled",
  answered: "Answered", handoff: "Handed off", abandoned: "Abandoned",
};

export const REASON_LABEL: Record<string, string> = {
  caller_request: "Caller asked for a person", angry_caller: "Caller upset", repeated_failure: "Two failed turns",
  unknown_request: "Request the agent cannot handle", model_unavailable: "Model unavailable",
};

/** Business timezone for display. Bookings are stored in UTC and always shown in the salon's local time. */
export const BUSINESS_TZ = "Asia/Kolkata";

export function localTime(iso: string, tz = BUSINESS_TZ): string {
  return new Intl.DateTimeFormat("en-IN", { hour: "numeric", minute: "2-digit", hour12: true, timeZone: tz })
    .format(new Date(iso)).replace(" ", " ").toUpperCase();
}

export function localDate(iso: string, tz = BUSINESS_TZ): string {
  return new Intl.DateTimeFormat("en-IN", { weekday: "short", day: "numeric", month: "short", timeZone: tz })
    .format(new Date(iso));
}

export function localDateTime(iso: string, tz = BUSINESS_TZ): string {
  return `${localDate(iso, tz)}, ${localTime(iso, tz)}`;
}

/** Minutes since local midnight in the business timezone, for placing a booking on the day grid. */
export function minutesOfDay(iso: string, tz = BUSINESS_TZ): number {
  const parts = new Intl.DateTimeFormat("en-GB", { hour: "2-digit", minute: "2-digit", hour12: false, timeZone: tz })
    .formatToParts(new Date(iso));
  const get = (type: string) => Number(parts.find((p) => p.type === type)?.value ?? 0);
  return (get("hour") % 24) * 60 + get("minute");
}

/** YYYY-MM-DD of an instant in the business timezone. */
export function isoDay(date: Date, tz = BUSINESS_TZ): string {
  return new Intl.DateTimeFormat("en-CA", { timeZone: tz, year: "numeric", month: "2-digit", day: "2-digit" }).format(date);
}

export function addDays(day: string, n: number): string {
  const [y, m, d] = day.split("-").map(Number) as [number, number, number];
  const next = new Date(Date.UTC(y, m - 1, d + n));
  return next.toISOString().slice(0, 10);
}

export function dayLabel(day: string): string {
  const [y, m, d] = day.split("-").map(Number) as [number, number, number];
  return new Intl.DateTimeFormat("en-IN", { weekday: "long", day: "numeric", month: "long", timeZone: "UTC" })
    .format(new Date(Date.UTC(y, m - 1, d)));
}

export function ms(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return value >= 1000 ? `${(value / 1000).toFixed(2)} s` : `${value} ms`;
}

export function rupees(minor: number): string {
  return new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR", maximumFractionDigits: 0 }).format(minor / 100);
}

export function relative(iso: string, now = Date.now()): string {
  const seconds = Math.round((now - new Date(iso).getTime()) / 1000);
  if (seconds < 60) return "just now";
  if (seconds < 3600) return `${Math.floor(seconds / 60)} min ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)} h ago`;
  return localDate(iso);
}

/** Compact one-line view of tool arguments: aliases and short values, never long ids. */
export function compactArgs(args: Record<string, unknown>): string {
  return Object.entries(args)
    .map(([k, v]) => {
      const text = typeof v === "string" ? v : JSON.stringify(v);
      return `${k}=${text.length > 28 ? `${text.slice(0, 12)}…${text.slice(-8)}` : text}`;
    })
    .join(", ");
}
