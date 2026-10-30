export type AgentState = "GREET" | "INTENT" | "COLLECT" | "CHECK_SLOTS" | "CONFIRM" | "BOOKED" | "WRAP_UP" | "HANDOFF";
export type Outcome = "in_progress" | "booked" | "rescheduled" | "cancelled" | "answered" | "handoff" | "abandoned";
export type HandoffStatus = "waiting" | "claimed" | "resolved";

export interface Health {
  status: string;
  version: string;
  database: string;
  brain: "gemini" | "rules";
  model: string | null;
  model_configured: boolean;
  business: string;
  timezone: string;
}

export interface Service { code: string; name: string; duration_min: number; price_minor: number }
export interface StaffMember {
  id: string; name: string; calendar_id: string; timezone: string;
  shifts: { weekday: number; start: string; end: string }[];
}

export interface Slot { slot_id: string; starts_at: string; ends_at: string; staff: string; service: string }

export interface Booking {
  id: string; service: string; service_name: string; staff: string; staff_id: string; customer: string;
  phone_e164: string; starts_at: string; ends_at: string; status: "confirmed" | "cancelled";
  event_id: string | null; call_id: string | null; created_at: string;
}

export interface CallRow {
  id: string; channel: string; caller_phone: string | null; outcome: Outcome; state: AgentState;
  summary: string | null; handoff_reason: string | null; handoff_status: HandoffStatus | null; brain: string | null;
  started_at: string; ended_at: string | null; turns: number;
}

export interface TurnRow {
  seq: number; speaker: "caller" | "agent"; text: string; state: AgentState | null;
  eos_to_audio_ms: number | null; server_first_ms: number | null; server_total_ms: number | null;
  interrupted: boolean; created_at: string;
}

export interface ToolCallRow {
  id: string; turn_seq: number | null; tool: string; args: Record<string, unknown>; ok: boolean;
  error_code: string | null; state: AgentState | null; latency_ms: number; say: string | null; actor: string | null;
}

export interface ModelCallRow {
  model: string; status: string; latency_ms: number; first_token_ms: number | null; input_tokens: number | null;
  output_tokens: number | null; error_category: string | null; prompt_version: string;
}

export interface CallDetail extends Omit<CallRow, "turns"> {
  session: Record<string, unknown>;
  turns: TurnRow[];
  tool_calls: ToolCallRow[];
  model_calls: ModelCallRow[];
  bookings: Booking[];
  latency: { server_first_p50: number | null; server_first_p95: number | null;
             eos_to_audio_p50: number | null; eos_to_audio_p95: number | null };
}

export interface IncomingCall { call_id: string; state: AgentState; greeting: string; seq: number; stream_path: string }

export interface TurnOutcome {
  call_id: string; seq: number; agent_text: string; state: AgentState; outcome: Outcome;
  tools: { tool: string; args: Record<string, unknown>; ok: boolean; error_code: string | null; actor: string }[];
  server_first_ms: number | null; server_total_ms: number; ended: boolean;
}

export interface Sms {
  id: string; to: string; body: string; kind: string; status: string; booking_id: string | null;
  call_id: string | null; created_at: string;
}

interface Pctl { n: number; p50: number | null; p95: number | null }
export interface Metrics {
  days: number;
  turns: { eos_to_audio_ms: Pctl; server_first_ms: Pctl; server_total_ms: Pctl };
  outcomes: Partial<Record<Outcome, number>>;
  calls: number;
  tool_calls: { ok: number; failed: number };
  model_calls: { n: number; errors: number; latency_ms: Pctl; first_token_ms: Pctl };
}
