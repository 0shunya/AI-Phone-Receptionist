import { useCallback, useEffect, useRef, useState } from "react";
import { api, streamUrl } from "./api";
import type { AgentState, Outcome, TurnOutcome } from "./types";

/**
 * A browser "phone call". Speech-to-text and text-to-speech run in the browser (Web Speech API); the server runs
 * the agent and streams back one sentence at a time over WS /api/voice/stream.
 *
 * Turn detection: the recogniser's final result is the end of the caller's turn. Latency: the time from that
 * moment to the first agent sentence starting to play is reported back as eos_to_audio_ms. Barge-in: if the
 * caller starts talking while the agent speaks, playback is cancelled at once and the server is told.
 */

export type CallStatus = "idle" | "connecting" | "live" | "ended";
export interface Line {
  id: number;
  role: "caller" | "agent" | "system";
  text: string;
  seq?: number;
  interrupted?: boolean;
  serverFirstMs?: number | null;
  eosToAudioMs?: number | null;
}

interface RecognitionResult { isFinal: boolean; 0: { transcript: string } }
interface RecognitionEvent { resultIndex: number; results: ArrayLike<RecognitionResult> }
interface Recognition {
  lang: string; continuous: boolean; interimResults: boolean;
  onresult: ((event: RecognitionEvent) => void) | null;
  onerror: ((event: { error: string }) => void) | null;
  onend: (() => void) | null;
  start(): void; stop(): void; abort(): void;
}
type RecognitionCtor = new () => Recognition;

export function recognitionSupported(): boolean {
  return typeof window !== "undefined" && !!((window as unknown as { SpeechRecognition?: RecognitionCtor }).SpeechRecognition
    || (window as unknown as { webkitSpeechRecognition?: RecognitionCtor }).webkitSpeechRecognition);
}

export function synthesisSupported(): boolean {
  return typeof window !== "undefined" && "speechSynthesis" in window;
}

function words(text: string): string[] {
  return text.toLowerCase().replace(/[^a-z0-9' ]+/g, " ").split(/\s+/).filter(Boolean);
}

/** True when what the mic heard is mostly the agent's own voice coming back through the speakers. */
export function isEcho(heard: string, speaking: string): boolean {
  const h = words(heard);
  if (!h.length || !speaking) return false;
  const spoken = new Set(words(speaking));
  return h.filter((w) => spoken.has(w)).length / h.length >= 0.6;
}

/** The server accepts and then closes with these codes, so the reason survives a cross-origin connection. */
export function closeMessage(code: number): string | null {
  switch (code) {
    case 4401: return "The access code was not accepted. Reload the page and enter it again.";
    case 4403: return "This site is not allowed to open the call stream (the API's FRONTEND_ORIGIN does not list it).";
    case 4404: return "This call no longer exists. Start a new one.";
    case 1006: return "The call connection dropped. Check your connection and start a new call.";
    default: return null;
  }
}

let lineId = 1;

export function useCall() {
  const [status, setStatus] = useState<CallStatus>("idle");
  const [callId, setCallId] = useState<string | null>(null);
  const [lines, setLines] = useState<Line[]>([]);
  const [agentState, setAgentState] = useState<AgentState>("GREET");
  const [outcome, setOutcome] = useState<Outcome>("in_progress");
  const [listening, setListening] = useState(false);
  const [speaking, setSpeaking] = useState(false);
  const [thinking, setThinking] = useState(false);
  const [interim, setInterim] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [turnsDone, setTurnsDone] = useState(0);

  const ws = useRef<WebSocket | null>(null);
  const recognition = useRef<Recognition | null>(null);
  const voice = useRef(false);            // speak replies aloud
  const listen = useRef(false);           // keep the recogniser running
  const bargeIn = useRef(true);
  const eosAt = useRef<number | null>(null);
  const audioMs = useRef<number | null>(null);
  const queue = useRef(0);                // utterances queued or playing
  const nowSpeaking = useRef("");
  const statusRef = useRef<CallStatus>("idle");
  useEffect(() => { statusRef.current = status; }, [status]);

  const push = useCallback((line: Omit<Line, "id">) => setLines((all) => [...all, { ...line, id: lineId++ }]), []);

  const appendAgent = useCallback((sentence: string) => {
    setLines((all) => {
      const last = all[all.length - 1];
      if (last && last.role === "agent" && last.seq === undefined && !last.interrupted) {
        return [...all.slice(0, -1), { ...last, text: `${last.text} ${sentence}` }];
      }
      return [...all, { id: lineId++, role: "agent", text: sentence }];
    });
  }, []);

  const speak = useCallback((sentence: string) => {
    if (!voice.current || !synthesisSupported()) return;
    const utterance = new SpeechSynthesisUtterance(sentence);
    utterance.lang = "en-IN";
    utterance.rate = 1.05;
    utterance.onstart = () => {
      nowSpeaking.current = sentence;
      setSpeaking(true);
      if (eosAt.current !== null && audioMs.current === null) {
        audioMs.current = Math.round(performance.now() - eosAt.current);
      }
    };
    const done = () => {
      queue.current = Math.max(0, queue.current - 1);
      if (queue.current === 0) {
        setSpeaking(false);
        nowSpeaking.current = "";
      }
    };
    utterance.onend = done;
    utterance.onerror = done;
    queue.current += 1;
    window.speechSynthesis.speak(utterance);
  }, []);

  const stopSpeaking = useCallback(() => {
    if (synthesisSupported()) window.speechSynthesis.cancel();
    queue.current = 0;
    nowSpeaking.current = "";
    setSpeaking(false);
  }, []);

  const sendCaller = useCallback((text: string) => {
    const socket = ws.current;
    const clean = text.trim();
    if (!clean || !socket || socket.readyState !== WebSocket.OPEN) return;
    eosAt.current = performance.now();
    audioMs.current = null;
    push({ role: "caller", text: clean });
    setThinking(true);
    socket.send(JSON.stringify({ type: "caller_turn", text: clean }));
  }, [push]);

  const interrupt = useCallback(() => {
    if (queue.current === 0) return;
    stopSpeaking();
    setLines((all) => {
      const index = all.map((l) => l.role).lastIndexOf("agent");
      return index < 0 ? all : all.map((l, i) => (i === index ? { ...l, interrupted: true } : l));
    });
    ws.current?.send(JSON.stringify({ type: "barge_in" }));
  }, [stopSpeaking]);

  const startRecognition = useCallback(() => {
    const Ctor = (window as unknown as { SpeechRecognition?: RecognitionCtor }).SpeechRecognition
      ?? (window as unknown as { webkitSpeechRecognition?: RecognitionCtor }).webkitSpeechRecognition;
    if (!Ctor) return;
    const rec = new Ctor();
    rec.lang = "en-IN";
    rec.continuous = true;
    rec.interimResults = true;
    rec.onresult = (event) => {
      let finalText = "";
      let partial = "";
      for (let i = event.resultIndex; i < event.results.length; i++) {
        const result = event.results[i]!;
        if (result.isFinal) finalText += result[0].transcript;
        else partial += result[0].transcript;
      }
      const heard = (finalText || partial).trim();
      if (heard && queue.current > 0 && isEcho(heard, nowSpeaking.current)) return;   // our own voice
      if (partial.trim().split(/\s+/).length >= 2 && queue.current > 0 && bargeIn.current) interrupt();
      setInterim(partial.trim());
      if (finalText.trim()) {
        setInterim("");
        sendCaller(finalText);
      }
    };
    rec.onerror = (event) => {
      if (event.error === "not-allowed" || event.error === "service-not-allowed") {
        listen.current = false;
        setError("Microphone access was blocked. Allow it in the browser, or type instead.");
      }
    };
    rec.onend = () => {
      // Chrome ends recognition after a pause even in continuous mode; keep listening while the call is live.
      if (listen.current && statusRef.current === "live") {
        try { rec.start(); } catch { /* already restarting */ }
      } else {
        setListening(false);
      }
    };
    recognition.current = rec;
    try {
      rec.start();
      setListening(true);
    } catch {
      setError("Could not start the microphone.");
    }
  }, [interrupt, sendCaller]);

  const cleanup = useCallback(() => {
    listen.current = false;
    recognition.current?.abort();
    recognition.current = null;
    setListening(false);
    ws.current?.close();
    ws.current = null;
  }, []);

  const start = useCallback(async (opts: { callerPhone: string | null; mode: "voice" | "text"; speakReplies: boolean;
    bargeIn: boolean }) => {
    setError(null);
    setLines([]);
    setOutcome("in_progress");
    setAgentState("GREET");
    setTurnsDone(0);
    setStatus("connecting");
    voice.current = opts.mode === "voice" || opts.speakReplies;
    bargeIn.current = opts.bargeIn;
    let call;
    try {
      call = await api.startCall(opts.callerPhone, opts.mode === "voice" ? "browser" : "text");
    } catch (e) {
      setStatus("idle");
      setError(e instanceof Error ? e.message : "Could not start the call.");
      return;
    }
    setCallId(call.call_id);
    const socket = new WebSocket(streamUrl(call.stream_path));
    ws.current = socket;
    socket.onopen = () => {
      setStatus("live");
      push({ role: "agent", text: call.greeting, seq: call.seq });
      speak(call.greeting);
      if (opts.mode === "voice") {
        listen.current = true;
        startRecognition();
      }
    };
    socket.onmessage = (event) => {
      const msg = JSON.parse(event.data as string);
      if (msg.type === "agent_sentence") {
        appendAgent(msg.text);
        speak(msg.text);
      } else if (msg.type === "turn_done") {
        const done = msg as TurnOutcome & { type: string };
        setThinking(false);
        setAgentState(done.state);
        setOutcome(done.outcome);
        const finish = (eos: number | null) => {
          setLines((all) => {
            const index = all.map((l) => l.role).lastIndexOf("agent");
            return all.map((l, i) => (i === index ? { ...l, seq: done.seq, serverFirstMs: done.server_first_ms, eosToAudioMs: eos } : l));
          });
          if (eos !== null) socket.send(JSON.stringify({ type: "audio_metrics", seq: done.seq, eos_to_audio_ms: eos }));
          // Bump after the metric is on its way so the call detail refetch includes it.
          window.setTimeout(() => setTurnsDone((n) => n + 1), eos === null ? 0 : 300);
        };
        if (!voice.current) finish(null);
        else if (audioMs.current !== null) finish(audioMs.current);
        else window.setTimeout(() => finish(audioMs.current), 1500);   // first sentence may still be starting
        if (done.state === "HANDOFF") {
          listen.current = false;
          recognition.current?.stop();
        }
      } else if (msg.type === "error") {
        setThinking(false);
        setError(msg.message);
      } else if (msg.type === "ended") {
        setOutcome(msg.outcome);
      }
    };
    socket.onclose = (event) => {
      setThinking(false);
      const reason = closeMessage(event.code);
      if (reason) setError(reason);
      if (statusRef.current === "live" || statusRef.current === "connecting") {
        setStatus("ended");
        listen.current = false;
        recognition.current?.abort();
        setListening(false);
      }
    };
    socket.onerror = () => setError("The call connection failed.");
  }, [appendAgent, push, speak, startRecognition]);

  const hangUp = useCallback(() => {
    stopSpeaking();
    const socket = ws.current;
    if (socket && socket.readyState === WebSocket.OPEN) socket.send(JSON.stringify({ type: "hangup" }));
    else if (callId) void api.endCall(callId).catch(() => undefined);
    listen.current = false;
    recognition.current?.abort();
    setListening(false);
    setStatus("ended");
    window.setTimeout(cleanup, 800);
  }, [callId, cleanup, stopSpeaking]);

  const transfer = useCallback(async () => {
    if (!callId) return;
    try {
      const r = await api.handoff(callId);
      setAgentState("HANDOFF");
      setOutcome("handoff");
      push({ role: "system", text: `Transferred to a receptionist. Summary: ${r.summary}` });
    } catch (e) {
      setError(e instanceof Error ? e.message : "Transfer failed.");
    }
  }, [callId, push]);

  const toggleMic = useCallback(() => {
    if (listening) {
      listen.current = false;
      recognition.current?.stop();
      setListening(false);
    } else if (statusRef.current === "live") {
      listen.current = true;
      voice.current = true;
      startRecognition();
    }
  }, [listening, startRecognition]);

  useEffect(() => () => { stopSpeaking(); cleanup(); }, [cleanup, stopSpeaking]);

  return { status, callId, lines, agentState, outcome, listening, speaking, thinking, interim, error, turnsDone,
    start, hangUp, sendText: sendCaller, transfer, toggleMic, interrupt, setError };
}
