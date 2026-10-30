import { useQuery } from "@tanstack/react-query";
import { ArrowLeft, ScrollText } from "lucide-react";
import { Link, useParams } from "react-router";
import { LatencyTable, OutcomeBadge, StateStrip, ToolCallList } from "../components/callbits";
import { Badge, cx, EmptyState, ErrorBox, PageHeader, Panel, Spinner, Stat } from "../components/ui";
import { api } from "../lib/api";
import { localDateTime, ms, REASON_LABEL, relative } from "../lib/format";

export function CallsPage() {
  const calls = useQuery({ queryKey: ["calls"], queryFn: () => api.calls(100), refetchInterval: 15_000 });
  const metrics = useQuery({ queryKey: ["metrics"], queryFn: () => api.metrics(7), refetchInterval: 30_000 });
  const m = metrics.data;
  return (
    <>
      <PageHeader title="Call log" description="Every call with its transcript, tool calls and per-turn latency. Figures cover the last 7 days of live calls on this deployment (the evaluation runs in its own database)." />
      {m && (
        <div className="mb-5 grid grid-cols-2 divide-x divide-line rounded-md border border-line bg-surface sm:grid-cols-4">
          <Stat label="Calls" value={m.calls} hint={`${m.outcomes.booked ?? 0} booked · ${m.outcomes.handoff ?? 0} handed off`} />
          <Stat label="Server, first sentence p95" value={ms(m.turns.server_first_ms.p95)} hint={`p50 ${ms(m.turns.server_first_ms.p50)} · n=${m.turns.server_first_ms.n}`} />
          <Stat label="End of speech → audio p95" value={ms(m.turns.eos_to_audio_ms.p95)} hint={`p50 ${ms(m.turns.eos_to_audio_ms.p50)} · n=${m.turns.eos_to_audio_ms.n} voice turns`} />
          <Stat label="Tool calls refused" value={m.tool_calls.failed} hint={`of ${m.tool_calls.ok + m.tool_calls.failed} · model errors ${m.model_calls.errors}`} />
        </div>
      )}
      <Panel bodyClassName="">
        {calls.isError ? <div className="p-4"><ErrorBox error={calls.error} onRetry={() => void calls.refetch()} /></div>
          : !calls.data ? <Spinner /> : !calls.data.length ? (
            <EmptyState icon={<ScrollText className="size-6" />} title="No calls yet" action={<Link to="/" className="text-accent underline">Make the first call</Link>}>
              Calls appear here as soon as they start.
            </EmptyState>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-[13px]">
                <thead className="border-b border-line text-left text-xs text-ink-3">
                  <tr><th className="px-4 py-2 font-medium">Started</th><th className="px-4 py-2 font-medium">Caller</th>
                    <th className="px-4 py-2 font-medium">Outcome</th><th className="px-4 py-2 font-medium">Turns</th>
                    <th className="px-4 py-2 font-medium">Brain</th><th className="px-4 py-2 font-medium">Channel</th></tr>
                </thead>
                <tbody>
                  {calls.data.map((c) => (
                    <tr key={c.id} className="border-b border-line last:border-0 hover:bg-canvas">
                      <td className="px-4 py-2"><Link to={`/calls/${c.id}`} className="text-accent hover:underline">{relative(c.started_at)}</Link></td>
                      <td className="px-4 py-2 font-mono text-xs">{c.caller_phone ?? "withheld"}</td>
                      <td className="px-4 py-2"><OutcomeBadge outcome={c.outcome} /></td>
                      <td className="num px-4 py-2">{c.turns}</td>
                      <td className="px-4 py-2 font-mono text-xs text-ink-2">{c.brain}</td>
                      <td className="px-4 py-2 text-ink-2">{c.channel}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
      </Panel>
    </>
  );
}

export function CallDetailPage() {
  const { id = "" } = useParams();
  const call = useQuery({ queryKey: ["call", id], queryFn: () => api.call(id), refetchInterval: (q) => (q.state.data?.ended_at ? false : 5_000) });
  if (call.isError) return <ErrorBox error={call.error} onRetry={() => void call.refetch()} />;
  if (!call.data) return <Spinner label="Loading call" />;
  const c = call.data;
  return (
    <>
      <Link to="/calls" className="mb-3 inline-flex items-center gap-1 text-[13px] text-ink-2 hover:text-ink"><ArrowLeft className="size-3.5" /> Call log</Link>
      <PageHeader title={`Call from ${c.caller_phone ?? "a withheld number"}`}
        description={<>{localDateTime(c.started_at)} · {c.channel} · <span className="font-mono text-xs">{c.brain}</span></>}
        actions={<OutcomeBadge outcome={c.outcome} />} />
      <div className="mb-4"><StateStrip state={c.state} /></div>
      {c.summary && (
        <div className="mb-4 rounded-md border border-warn/30 bg-warn-soft px-4 py-3 text-[13px] text-warn">
          <p className="font-medium">Handoff: {REASON_LABEL[c.handoff_reason ?? ""] ?? c.handoff_reason} ({c.handoff_status})</p>
          <p className="mt-1 whitespace-pre-line">{c.summary}</p>
        </div>
      )}
      <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_380px]">
        <Panel title="Transcript">
          <ol className="flex flex-col gap-2.5">
            {c.turns.map((t) => (
              <li key={t.seq} className={cx("flex gap-3", t.speaker === "caller" && "flex-row-reverse text-right")}>
                <span className="w-8 shrink-0 pt-0.5 font-mono text-[11px] text-ink-3">#{t.seq}</span>
                <div className={cx("max-w-[85%] rounded-lg px-3 py-2", t.speaker === "caller" ? "bg-accent-soft" : "border border-line bg-canvas")}>
                  <p className={cx(t.interrupted && "line-through decoration-ink-3/40")}>{t.text}</p>
                  <p className="mt-0.5 font-mono text-[11px] text-ink-3">
                    {t.speaker} · {t.state}{t.interrupted && " · interrupted"}
                    {t.speaker === "agent" && t.seq > 1 && ` · ${ms(t.server_first_ms)}`}
                  </p>
                </div>
              </li>
            ))}
          </ol>
        </Panel>
        <div className="flex flex-col gap-5">
          <Panel title="Tool calls"><ToolCallList calls={c.tool_calls} /></Panel>
          <Panel title="Turn latency"><LatencyTable turns={c.turns} /></Panel>
          <Panel title="Model calls">
            {!c.model_calls.length ? <p className="text-[13px] text-ink-3">No model calls (rule-based brain, or every turn was handled by a guard rule).</p> : (
              <ul className="flex flex-col gap-1.5 text-[13px]">
                {c.model_calls.map((m, i) => (
                  <li key={i} className="flex items-center gap-2">
                    <Badge tone={m.status === "success" ? "ok" : "bad"}>{m.status}</Badge>
                    <span className="num">{ms(m.latency_ms)}</span>
                    <span className="num text-ink-3">first token {ms(m.first_token_ms)}</span>
                    <span className="num ml-auto text-xs text-ink-3">{m.input_tokens ?? "?"}→{m.output_tokens ?? "?"} tok</span>
                    {m.error_category && <span className="font-mono text-[11px] text-bad">{m.error_category}</span>}
                  </li>
                ))}
              </ul>
            )}
          </Panel>
        </div>
      </div>
    </>
  );
}
