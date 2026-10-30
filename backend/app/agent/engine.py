"""One caller turn, end to end: state transition, guard rails, brain, tool router, failure rule, call log.

The engine is shared by the browser voice stream, the text chat endpoint and the scenario evaluation, so the
evaluation measures the same code path a caller hits.
"""

from __future__ import annotations

import re
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload
from sqlalchemy.orm.attributes import flag_modified

from app.agent.brain import Brain, TurnContext
from app.agent.state import ALLOWED_TOOLS, State, asks_for_human, sounds_angry
from app.agent.tools import ToolContext, ToolResult, run_tool, tool_schemas
from app.config import get_settings
from app.models import Call, CallOutcome, Service, Speaker, Staff, Turn
from app.services.speech import say_time

MAX_CALLER_CHARS = 500


class CallClosed(Exception):
    pass


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class SentenceStream:
    """Turns streamed text deltas into whole sentences, so speech can start on the first one."""

    # A sentence ends at . ! or ? followed by whitespace; "10.30" or a trailing "." waits for more text.
    _END = re.compile(r"[.!?]\s+")

    def __init__(self, emit: Callable[[str], None], started: float):
        self._emit, self._started = emit, started
        self._buffer = ""
        self.sentences: list[str] = []
        self.first_ms: int | None = None

    def feed(self, delta: str) -> None:
        self._buffer += delta
        while match := self._END.search(self._buffer):
            self._say(self._buffer[:match.start() + 1])
            self._buffer = self._buffer[match.end():]

    def say(self, text: str) -> None:
        self.flush()
        for sentence in re.findall(r"[^.!?]+[.!?]?", text):
            if sentence.strip():
                self._say(sentence)

    def flush(self) -> None:
        if self._buffer.strip():
            self._say(self._buffer)
        self._buffer = ""

    def _say(self, sentence: str) -> None:
        sentence = sentence.strip()
        if not sentence:
            return
        if self.first_ms is None:
            self.first_ms = round((time.perf_counter() - self._started) * 1000)
        self.sentences.append(sentence)
        self._emit(sentence)


@dataclass
class TurnOutcome:
    call_id: str
    seq: int
    agent_text: str
    state: str
    outcome: str
    tools: list[dict] = field(default_factory=list)
    server_first_ms: int | None = None
    server_total_ms: int = 0
    ended: bool = False


def business_facts(db: Session) -> tuple[list[dict], str]:
    settings = get_settings()
    services = [{"code": s.code, "name": s.name, "duration_min": s.duration_min,
                 "price": f"{s.price_minor // 100} {settings.currency_word}"}
                for s in db.scalars(select(Service).order_by(Service.name))]
    hours: dict[int, tuple] = {}
    for member in db.scalars(select(Staff).options(selectinload(Staff.shifts))):
        for shift in member.shifts:
            lo, hi = hours.get(shift.weekday, (shift.start_local, shift.end_local))
            hours[shift.weekday] = (min(lo, shift.start_local), max(hi, shift.end_local))
    names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    groups: list[tuple[list[int], tuple]] = []
    for day in range(7):
        if day in hours and groups and groups[-1][1] == hours[day] and groups[-1][0][-1] == day - 1:
            groups[-1][0].append(day)
        elif day in hours:
            groups.append(([day], hours[day]))
    def fmt(t) -> str:
        return say_time(datetime(2000, 1, 1, t.hour, t.minute))
    parts = [f"{names[g[0]]}{' to ' + names[g[-1]] if len(g) > 1 else ''}, {fmt(lo)} to {fmt(hi)}" for g, (lo, hi) in groups]
    closed = [names[d] for d in range(7) if d not in hours]
    text = "We're open " + "; ".join(parts) + "." + (f" Closed {', '.join(closed)}." if closed else "")
    return services, text


def greeting() -> str:
    return (f"Thanks for calling {get_settings().business_name}, this is the virtual receptionist. "
            "This call is transcribed so we can help you. How can I help today?")


class Engine:
    def __init__(self, brain: Brain, clock: Callable[[], datetime] = utcnow):
        self.brain = brain
        self.clock = clock

    def start_call(self, db: Session, *, caller_phone: str | None, channel: str = "browser",
                   provider_call_id: str | None = None) -> tuple[Call, Turn]:
        call = Call(caller_phone=caller_phone, channel=channel, provider_call_id=provider_call_id,
                    state=State.GREET, session={}, brain=self.brain.name, outcome=CallOutcome.in_progress)
        db.add(call)
        db.flush()
        turn = Turn(call_id=call.id, seq=1, speaker=Speaker.agent, text=greeting(), state=State.GREET,
                    server_first_ms=0, server_total_ms=0)
        db.add(turn)
        db.flush()
        return call, turn

    def handle_turn(self, db: Session, call_id: uuid.UUID, text: str,
                    emit: Callable[[str], None] = lambda _s: None) -> TurnOutcome:
        started = time.perf_counter()
        call = db.get(Call, call_id, with_for_update=True)   # one turn at a time per call
        if call is None or call.ended_at is not None:
            raise CallClosed()
        text = re.sub(r"\s+", " ", text).strip()[:MAX_CALLER_CHARS]
        now = self.clock()
        seq = (db.scalar(select(func.max(Turn.seq)).where(Turn.call_id == call.id)) or 0) + 1
        history = [(t.speaker.value, t.text) for t in db.scalars(
            select(Turn).where(Turn.call_id == call.id).order_by(Turn.seq))]
        db.add(Turn(call_id=call.id, seq=seq, speaker=Speaker.caller, text=text, state=call.state))
        stream = SentenceStream(emit, started)
        tools: list[dict] = []

        def run(name: str, args: dict, actor: str) -> ToolResult:
            ctx = ToolContext(db=db, call=call, caller_text=text, now=now, turn_seq=seq, actor=actor)
            result = run_tool(ctx, name, args)
            tools.append({"tool": name, "args": args, "ok": result.ok, "error_code": result.error_code,
                          "actor": actor})
            return result

        def hand_off(reason: str) -> None:
            run("transfer_to_human", {"reason": reason, "summary": summarize(call, reason, text)}, "guard")

        state = State(call.state)
        if state == State.HANDOFF:
            stream.say("A receptionist will be with you in just a moment.")
        else:
            if state == State.GREET:
                call.state = State.INTENT
            elif state == State.INTENT:
                call.state = State.COLLECT
            elif state == State.BOOKED:
                call.state = State.WRAP_UP
            if asks_for_human(text):
                hand_off("caller_request")
            elif sounds_angry(text):
                hand_off("angry_caller")
            else:
                self._brain_turn(db, call, text, history, now, stream, run, hand_off)
        stream.flush()
        session = dict(call.session or {})
        call.session = session
        flag_modified(call, "session")
        agent_text = " ".join(stream.sentences)
        total = round((time.perf_counter() - started) * 1000)
        db.add(Turn(call_id=call.id, seq=seq + 1, speaker=Speaker.agent, text=agent_text, state=call.state,
                    server_first_ms=stream.first_ms, server_total_ms=total))
        db.flush()
        return TurnOutcome(str(call.id), seq + 1, agent_text, call.state, call.outcome.value, tools,
                           stream.first_ms, total)

    def _brain_turn(self, db, call: Call, text: str, history, now, stream: SentenceStream, run, hand_off) -> None:
        settings = get_settings()
        services, hours_text = business_facts(db)
        allowed = ALLOWED_TOOLS[State(call.state)]
        ctx = TurnContext(call_id=str(call.id), state=call.state, allowed_tools=allowed,
                          tool_schemas=tool_schemas(db, allowed), session=dict(call.session or {}),
                          history=history, caller_text=text, now_local=now.astimezone(settings.tz),
                          caller_phone=call.caller_phone, services=services, hours_text=hours_text,
                          business_name=settings.business_name)
        decision = self.brain.decide(ctx, stream.feed)
        if decision.notes is not None:
            call.session = {**(call.session or {}), "brain_notes": decision.notes}
        if decision.fatal:
            stream.say("I'm sorry, I'm having trouble on my side. Let me get a receptionist for you.")
            hand_off("model_unavailable")
            return
        result = run(decision.tool, decision.args, "model") if decision.tool else None
        if result is not None:
            stream.say(result.say)
        elif not decision.text:
            stream.say("Sorry, could you say that again?")
        failed = (not decision.understood) or bool(decision.error_category) or bool(result and result.counts_as_failure)
        if call.state == State.HANDOFF:
            return
        session = dict(call.session or {})
        session["failed_turns"] = session.get("failed_turns", 0) + 1 if failed else 0
        call.session = session
        if session["failed_turns"] >= settings.max_failed_turns:
            stream.say("Let me get a receptionist to help you with this.")
            hand_off("repeated_failure")

    def handoff(self, db: Session, call_id: uuid.UUID, reason: str, summary: str | None) -> Call:
        """POST /calls/:id/handoff - a manual transfer (e.g. the caller pressed the button in the UI)."""
        call = db.get(Call, call_id, with_for_update=True)
        if call is None or call.ended_at is not None:
            raise CallClosed()
        if call.state != State.HANDOFF:
            ctx = ToolContext(db=db, call=call, caller_text="", now=self.clock(), actor="api")
            run_tool(ctx, "transfer_to_human", {"reason": reason, "summary": summary or summarize(call, reason, "")})
        return call

    def end_call(self, db: Session, call_id: uuid.UUID) -> Call:
        call = db.get(Call, call_id, with_for_update=True)
        if call is None:
            raise CallClosed()
        if call.ended_at is None:
            call.ended_at = self.clock()
            if call.outcome == CallOutcome.in_progress:
                spoke = db.scalar(select(func.count(Turn.id)).where(Turn.call_id == call.id,
                                                                     Turn.speaker == Speaker.caller))
                call.outcome = CallOutcome.answered if spoke else CallOutcome.abandoned
        return call


INTENT_LABELS = {"book": "Wants to book", "change": "Wants to change a booking", "reschedule": "Wants to reschedule",
                 "cancel": "Wants to cancel", None: "Request unclear"}


def summarize(call: Call, reason: str, last_text: str) -> str:
    """The two-line summary a human receptionist sees, built from session state, not from the model."""
    session = call.session or {}
    notes = session.get("brain_notes") or {}
    intent = session.get("intent") or notes.get("intent")
    service = session.get("service") or notes.get("service")
    who = session.get("name") or notes.get("name") or "name not given"
    phone = session.get("phone") or call.caller_phone or "no number"
    line1 = f"{INTENT_LABELS.get(intent, 'Request unclear')}{' a ' + service.replace('_', ' ') if service else ''}; {who}, {phone}."
    said = f' Last said: "{last_text[:120]}"' if last_text else ""
    return f"{line1}\nTransferred: {reason.replace('_', ' ')}.{said}"
