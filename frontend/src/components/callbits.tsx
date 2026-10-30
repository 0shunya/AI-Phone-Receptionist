import { CheckCircle2, CircleX, PhoneForwarded } from "lucide-react";
import { compactArgs, ms, OUTCOME_LABEL, STATE_LABEL, STATES } from "../lib/format";
import type { AgentState, Outcome, ToolCallRow, TurnRow } from "../lib/types";
import { Badge, cx } from "./ui";

const OUTCOME_TONE: Record<Outcome, "neutral" | "ok" | "warn" | "bad" | "info" | "accent"> = {
  in_progress: "info", booked: "ok", rescheduled: "ok", cancelled: "neutral", answered: "neutral",
  handoff: "warn", abandoned: "neutral",
};

export function OutcomeBadge({ outcome }: { outcome: Outcome }) {
  return <Badge tone={OUTCOME_TONE[outcome]}>{OUTCOME_LABEL[outcome]}</Badge>;
}

/** The state machine, with the current step highlighted. HANDOFF sits apart: it is reachable from any step. */
export function StateStrip({ state }: { state: AgentState }) {
  const current = STATES.indexOf(state);
  return (
    <div className="flex flex-wrap items-center gap-1.5" aria-label={`Current step: ${STATE_LABEL[state]}`}>
      <ol className="flex flex-wrap items-center gap-1">
        {STATES.map((s, i) => (
          <li key={s} className="flex items-center gap-1">
            <span
              aria-current={s === state ? "step" : undefined}
              className={cx("rounded px-1.5 py-0.5 font-mono text-[11px] font-medium",
                s === state ? "bg-accent text-white"
                  : current >= 0 && i < current ? "bg-accent-soft text-accent" : "bg-sunken text-ink-3")}>
              {STATE_LABEL[s]}
            </span>
            {i < STATES.length - 1 && <span className="text-ink-3" aria-hidden>›</span>}
          </li>
        ))}
      </ol>
      <span className={cx("ml-1 inline-flex items-center gap-1 rounded px-1.5 py-0.5 font-mono text-[11px] font-medium",
        state === "HANDOFF" ? "bg-warn text-white" : "border border-dashed border-line-strong text-ink-3")}>
        <PhoneForwarded className="size-3" aria-hidden /> Handoff
      </span>
    </div>
  );
}

export function ToolCallList({ calls }: { calls: ToolCallRow[] }) {
  if (!calls.length) return <p className="text-[13px] text-ink-3">No tool calls yet. Tools run when the agent needs the calendar.</p>;
  return (
    <ol className="flex flex-col gap-2">
      {calls.map((t) => (
        <li key={t.id} className="rounded-md border border-line px-3 py-2">
          <div className="flex items-center gap-2">
            {t.ok ? <CheckCircle2 className="size-3.5 shrink-0 text-ok" aria-label="ok" />
              : <CircleX className="size-3.5 shrink-0 text-bad" aria-label="refused" />}
            <code className="font-mono text-[13px] font-medium">{t.tool}</code>
            {t.state && <span className="font-mono text-[11px] text-ink-3">in {t.state}</span>}
            {t.actor && t.actor !== "model" && <Badge tone="accent">{t.actor === "guard" ? "guard rule" : t.actor}</Badge>}
            <span className="num ml-auto text-xs text-ink-3">{t.latency_ms} ms</span>
          </div>
          <p className="mt-1 truncate font-mono text-[11px] text-ink-2" title={JSON.stringify(t.args)}>{compactArgs(t.args)}</p>
          {t.error_code && <p className="mt-0.5 font-mono text-[11px] text-bad">{t.error_code}</p>}
        </li>
      ))}
    </ol>
  );
}

export function LatencyTable({ turns }: { turns: TurnRow[] }) {
  const agent = turns.filter((t) => t.speaker === "agent" && t.seq > 1);
  if (!agent.length) return <p className="text-[13px] text-ink-3">Latency appears after the first reply.</p>;
  return (
    <table className="w-full text-[13px]">
      <thead>
        <tr className="text-left text-xs text-ink-3">
          <th className="pb-1 font-medium">Turn</th>
          <th className="pb-1 text-right font-medium" title="Caller text received to first agent sentence ready, on the server">Server, first sentence</th>
          <th className="pb-1 text-right font-medium" title="End of caller speech to first agent audio, measured in the browser">End of speech → audio</th>
        </tr>
      </thead>
      <tbody>
        {agent.map((t) => (
          <tr key={t.seq} className="border-t border-line">
            <td className="py-1 font-mono text-xs">#{t.seq}{t.interrupted && <span className="ml-1 text-warn">barge-in</span>}</td>
            <td className="num py-1 text-right">{ms(t.server_first_ms)}</td>
            <td className="num py-1 text-right">{ms(t.eos_to_audio_ms)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
