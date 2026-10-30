import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Keyboard, Mic, MicOff, Phone, PhoneForwarded, PhoneOff, Send, Volume2 } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import type { FormEvent } from "react";
import { Link } from "react-router";
import { LatencyTable, OutcomeBadge, StateStrip, ToolCallList } from "../components/callbits";
import { Badge, Button, cx, Label, PageHeader, Panel, TextInput } from "../components/ui";
import { api } from "../lib/api";
import { localDateTime, ms } from "../lib/format";
import { recognitionSupported, synthesisSupported, useCall } from "../lib/useCall";

const TRY_LINES = [
  "Hi, I'd like a haircut tomorrow morning.",
  "Can I move my appointment to Thursday afternoon?",
  "Please cancel my booking.",
  "How much is a colour?",
  "Can I talk to a real person?",
];

export default function CallPage() {
  const call = useCall();
  const qc = useQueryClient();
  const [callerPhone, setCallerPhone] = useState("+91 98765 43210");
  const [withheld, setWithheld] = useState(false);
  const [mode, setMode] = useState<"voice" | "text">(recognitionSupported() ? "voice" : "text");
  const [speakReplies, setSpeakReplies] = useState(true);
  const [bargeIn, setBargeIn] = useState(true);
  const [draft, setDraft] = useState("");
  const scroller = useRef<HTMLDivElement>(null);
  const live = call.status === "live";

  const detail = useQuery({
    queryKey: ["call", call.callId, call.turnsDone],
    queryFn: () => api.call(call.callId!),
    enabled: !!call.callId,
  });

  useEffect(() => {
    scroller.current?.scrollTo({ top: scroller.current.scrollHeight, behavior: "smooth" });
  }, [call.lines, call.interim]);

  useEffect(() => {
    if (call.turnsDone > 0 || call.status === "ended") {
      void qc.invalidateQueries({ queryKey: ["bookings"] });
      void qc.invalidateQueries({ queryKey: ["handoffs"] });
      void qc.invalidateQueries({ queryKey: ["sms"] });
      void qc.invalidateQueries({ queryKey: ["calls"] });
    }
  }, [call.turnsDone, call.status, qc]);

  function begin() {
    void call.start({ callerPhone: withheld ? null : callerPhone, mode, speakReplies, bargeIn });
  }

  function submit(event: FormEvent) {
    event.preventDefault();
    if (!draft.trim()) return;
    call.interrupt();
    call.sendText(draft);
    setDraft("");
  }

  const bookings = detail.data?.bookings ?? [];
  return (
    <>
      <PageHeader
        title="Call the receptionist"
        description="Book, move or cancel a salon appointment by voice. The agent checks the live calendar, reads every booking back before committing it, and hands you to a person whenever you ask."
      />
      <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_380px]">
        <Panel
          title={<span className="flex items-center gap-2">Call {call.callId && <OutcomeBadge outcome={call.outcome} />}</span>}
          actions={live && (
            <>
              <Button size="sm" variant="secondary" icon={<PhoneForwarded className="size-3.5" />} onClick={() => void call.transfer()}
                disabled={call.agentState === "HANDOFF"}>
                Transfer to human
              </Button>
              <Button size="sm" variant="danger" icon={<PhoneOff className="size-3.5" />} onClick={call.hangUp}>Hang up</Button>
            </>
          )}
          bodyClassName="flex min-h-[520px] flex-col"
        >
          {call.status === "idle" || (call.status === "ended" && !call.lines.length) ? (
            <StartForm
              callerPhone={callerPhone} setCallerPhone={setCallerPhone} withheld={withheld} setWithheld={setWithheld}
              mode={mode} setMode={setMode} speakReplies={speakReplies} setSpeakReplies={setSpeakReplies}
              bargeIn={bargeIn} setBargeIn={setBargeIn} onStart={begin} error={call.error}
            />
          ) : (
            <>
              <div className="flex items-center gap-3 border-b border-line px-4 py-2.5">
                <StateStrip state={call.agentState} />
              </div>
              <div ref={scroller} className="flex-1 space-y-3 overflow-y-auto px-4 py-4" aria-live="polite">
                {call.status === "connecting" && <p className="text-ink-3">Ringing…</p>}
                {call.lines.map((line) => (
                  <div key={line.id} className={cx("flex", line.role === "caller" ? "justify-end" : "justify-start")}>
                    {line.role === "system" ? (
                      <p className="w-full rounded-md border border-warn/30 bg-warn-soft px-3 py-2 text-[13px] whitespace-pre-line text-warn">{line.text}</p>
                    ) : (
                      <div className={cx("max-w-[80%] rounded-lg px-3 py-2",
                        line.role === "caller" ? "bg-accent text-white" : "border border-line bg-canvas")}>
                        <p className={cx(line.interrupted && "text-ink-3 line-through decoration-ink-3/40")}>{line.text}</p>
                        {line.role === "agent" && (line.serverFirstMs != null || line.interrupted) && (
                          <p className="num mt-1 text-[11px] text-ink-3">
                            {line.interrupted && <span className="mr-2 text-warn">interrupted</span>}
                            {line.serverFirstMs != null && <>server {ms(line.serverFirstMs)}</>}
                            {line.eosToAudioMs != null && <> · speech→audio {ms(line.eosToAudioMs)}</>}
                          </p>
                        )}
                      </div>
                    )}
                  </div>
                ))}
                {call.interim && (
                  <div className="flex justify-end"><p className="max-w-[80%] rounded-lg bg-accent-soft px-3 py-2 text-accent italic">{call.interim}…</p></div>
                )}
                {call.thinking && <p className="text-[13px] text-ink-3">Receptionist is checking…</p>}
                {call.agentState === "HANDOFF" && (
                  <p className="rounded-md border border-warn/30 bg-warn-soft px-3 py-2 text-[13px] text-warn">
                    This call is with a human receptionist now. It is waiting in the <Link className="underline" to="/handoffs">handoff queue</Link>.
                  </p>
                )}
                {call.status === "ended" && <p className="text-center text-[13px] text-ink-3">Call ended.</p>}
              </div>
              {call.error && <p role="alert" className="mx-4 mb-2 rounded-md bg-bad-soft px-3 py-2 text-[13px] text-bad">{call.error}</p>}
              <div className="border-t border-line px-4 py-3">
                {live ? (
                  <form onSubmit={submit} className="flex items-center gap-2">
                    {recognitionSupported() && (
                      <Button type="button" variant={call.listening ? "primary" : "secondary"} onClick={call.toggleMic}
                        aria-pressed={call.listening} aria-label={call.listening ? "Mute microphone" : "Unmute microphone"}
                        icon={call.listening ? <Mic className="size-4" /> : <MicOff className="size-4" />}>
                        <span className="hidden sm:inline">{call.listening ? "Listening" : "Mic off"}</span>
                      </Button>
                    )}
                    <TextInput value={draft} onChange={(e) => setDraft(e.target.value)} aria-label="Type what the caller says"
                      placeholder={call.listening ? "Speak, or type here…" : "Type what you would say…"} />
                    <Button type="submit" variant="primary" icon={<Send className="size-4" />} disabled={!draft.trim()}>Send</Button>
                  </form>
                ) : (
                  <div className="flex items-center justify-between gap-3">
                    <p className="text-[13px] text-ink-2">
                      {call.callId && <Link to={`/calls/${call.callId}`} className="text-accent underline">Open the full call log</Link>}
                    </p>
                    <Button variant="primary" icon={<Phone className="size-4" />} onClick={begin}>Call again</Button>
                  </div>
                )}
                {live && call.speaking && (
                  <p className="mt-2 flex items-center gap-1.5 text-xs text-ink-3"><Volume2 className="size-3.5" aria-hidden />
                    Agent speaking{bargeIn && recognitionSupported() ? ": start talking to interrupt" : ""}</p>
                )}
              </div>
            </>
          )}
        </Panel>

        <div className="flex flex-col gap-5">
          <Panel title="Tool calls" actions={detail.data && <span className="text-xs text-ink-3">{detail.data.tool_calls.length}</span>}>
            {call.callId ? <ToolCallList calls={detail.data?.tool_calls ?? []} />
              : <p className="text-[13px] text-ink-3">Every tool the agent calls appears here with its arguments, the step it ran in and whether the router accepted it.</p>}
          </Panel>
          <Panel title="Turn latency">
            <LatencyTable turns={detail.data?.turns ?? []} />
            {detail.data && detail.data.latency.server_first_p95 !== null && (
              <p className="num mt-2 text-xs text-ink-3">This call: server p50 {ms(detail.data.latency.server_first_p50)}, p95 {ms(detail.data.latency.server_first_p95)}</p>
            )}
          </Panel>
          {bookings.length > 0 && (
            <Panel title="Booked on this call">
              <ul className="flex flex-col gap-2 text-[13px]">
                {bookings.map((b) => (
                  <li key={b.id} className="flex items-center justify-between gap-2">
                    <span>{b.service_name} with {b.staff}<br /><span className="text-ink-2">{localDateTime(b.starts_at)}</span></span>
                    <Badge tone={b.status === "confirmed" ? "ok" : "neutral"}>{b.status}</Badge>
                  </li>
                ))}
              </ul>
              <Link to="/calendar" className="mt-3 inline-block text-[13px] text-accent underline">See it on the calendar</Link>
            </Panel>
          )}
        </div>
      </div>
    </>
  );
}

function StartForm(props: {
  callerPhone: string; setCallerPhone: (v: string) => void; withheld: boolean; setWithheld: (v: boolean) => void;
  mode: "voice" | "text"; setMode: (m: "voice" | "text") => void; speakReplies: boolean; setSpeakReplies: (v: boolean) => void;
  bargeIn: boolean; setBargeIn: (v: boolean) => void; onStart: () => void; error: string | null;
}) {
  const voiceOk = recognitionSupported();
  return (
    <div className="flex flex-1 flex-col gap-5 p-4 sm:p-6">
      <div className="grid gap-4 xl:grid-cols-2">
        <div>
          <Label htmlFor="caller-id">Calling from (caller ID)</Label>
          <TextInput id="caller-id" value={props.callerPhone} onChange={(e) => props.setCallerPhone(e.target.value)}
            disabled={props.withheld} inputMode="tel" />
          <label className="mt-1.5 flex items-center gap-2 text-[13px] text-ink-2">
            <input type="checkbox" checked={props.withheld} onChange={(e) => props.setWithheld(e.target.checked)} />
            Withhold my number (the agent will ask for it)
          </label>
        </div>
        <fieldset>
          <legend className="mb-1 block text-xs font-medium text-ink-2">How you talk</legend>
          <div className="inline-flex rounded-md border border-line-strong p-0.5" role="radiogroup">
            {(["voice", "text"] as const).map((m) => (
              <button key={m} type="button" role="radio" aria-checked={props.mode === m}
                disabled={m === "voice" && !voiceOk} onClick={() => props.setMode(m)}
                className={cx("inline-flex h-8 items-center gap-1.5 rounded px-3 text-sm font-medium disabled:opacity-40",
                  props.mode === m ? "bg-accent text-white" : "text-ink-2 hover:bg-sunken")}>
                {m === "voice" ? <Mic className="size-4" aria-hidden /> : <Keyboard className="size-4" aria-hidden />}
                {m === "voice" ? "Voice" : "Text"}
              </button>
            ))}
          </div>
          {!voiceOk && <p className="mt-1.5 text-[13px] text-ink-3">This browser has no speech recognition; use Chrome or Edge for voice.</p>}
          <label className="mt-2 flex items-center gap-2 text-[13px] text-ink-2">
            <input type="checkbox" checked={props.mode === "voice" || props.speakReplies} disabled={props.mode === "voice" || !synthesisSupported()}
              onChange={(e) => props.setSpeakReplies(e.target.checked)} />
            Speak the agent's replies aloud
          </label>
          <label className="mt-1 flex items-center gap-2 text-[13px] text-ink-2">
            <input type="checkbox" checked={props.bargeIn} disabled={props.mode !== "voice"} onChange={(e) => props.setBargeIn(e.target.checked)} />
            Barge-in: talking over the agent stops it (use headphones)
          </label>
        </fieldset>
      </div>
      {props.error && <p role="alert" className="rounded-md bg-bad-soft px-3 py-2 text-[13px] text-bad">{props.error}</p>}
      <div>
        <Button variant="primary" icon={<Phone className="size-4" />} onClick={props.onStart} className="h-10 px-5">Start call</Button>
        <p className="mt-2 text-[13px] text-ink-3">The call is transcribed (no audio is recorded). Speech recognition runs in your browser.</p>
      </div>
      <div className="mt-auto rounded-md border border-line bg-canvas p-4">
        <p className="text-xs font-medium text-ink-2">Things to try</p>
        <ul className="mt-2 grid gap-1 text-[13px] text-ink-2 xl:grid-cols-2">
          {TRY_LINES.map((t) => <li key={t}>“{t}”</li>)}
        </ul>
      </div>
    </div>
  );
}
