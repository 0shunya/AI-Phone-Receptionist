"""Narrow tools and the router that re-validates every argument.

Tools that are hard to misuse: strict schemas, E.164 phone numbers, ids instead of free text. The model only
ever sees short aliases (``slot_1``, ``booking_1``) for things a tool has returned in *this* call, so it cannot
book a slot it was never offered. Every tool returns a short, speakable sentence; the engine speaks it directly
instead of paying for a second model round-trip.

``book``, ``reschedule`` and ``cancel`` are two-phase: the first call only reads the details back and moves the
call to CONFIRM; the identical second call commits, and only if the caller's last words were a clear yes.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.agent.state import ALLOWED_TOOLS, COMMIT_TOOLS, HANDOFF_REASONS, State, is_clear_yes
from app.config import get_settings
from app.models import Booking, Call, CallOutcome, HandoffStatus, Service, Staff, ToolCall
from app.services import calendar as cal
from app.services.notify import send_booking_sms
from app.services.phone import to_e164
from app.services.speech import say_phone_tail, say_time, say_when, spell

MAX_OFFERED = 6
SEARCH_LIMIT = 200


class _Args(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class FindSlotsArgs(_Args):
    service: str = Field(min_length=1, max_length=40)
    date_from: str = Field(min_length=10, max_length=16)
    date_to: str = Field(min_length=10, max_length=16)
    staff: str | None = Field(default=None, max_length=40)


class FindBookingsArgs(_Args):
    phone_e164: str = Field(min_length=8, max_length=20)


class BookArgs(_Args):
    slot_id: str = Field(min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=60, pattern=r"^[A-Za-z][A-Za-z .'\-]*$")
    phone_e164: str = Field(min_length=8, max_length=20)


class RescheduleArgs(_Args):
    booking_id: str = Field(min_length=1, max_length=60)
    new_slot_id: str = Field(min_length=1, max_length=80)


class CancelArgs(_Args):
    booking_id: str = Field(min_length=1, max_length=60)
    reason: str = Field(default="", max_length=200)


class TransferArgs(_Args):
    reason: Literal["caller_request", "angry_caller", "repeated_failure", "unknown_request", "model_unavailable"]
    summary: str = Field(min_length=1, max_length=300)


ARG_MODELS: dict[str, type[_Args]] = {
    "find_slots": FindSlotsArgs, "find_bookings": FindBookingsArgs, "book": BookArgs,
    "reschedule": RescheduleArgs, "cancel": CancelArgs, "transfer_to_human": TransferArgs,
}

DESCRIPTIONS = {
    "find_slots": "Find real free appointment times. Use local dates (YYYY-MM-DD) or local date-times "
                  "(YYYY-MM-DDTHH:MM). Never claim a time is free without calling this.",
    "find_bookings": "Look up the caller's upcoming bookings by phone number, before rescheduling or cancelling.",
    "book": "Book an offered slot. The first call reads the details back to the caller; call it again with the "
            "same arguments only after the caller clearly says yes.",
    "reschedule": "Move one of the caller's bookings to an offered slot. Same read-back then confirm pattern.",
    "cancel": "Cancel one of the caller's bookings. Same read-back then confirm pattern.",
    "transfer_to_human": "Transfer the call to a human receptionist with a two-line summary. Use when the caller "
                         "asks for a person, is upset, or wants something you cannot do.",
}


def tool_schemas(db: Session, names: list[str]) -> list[dict]:
    """JSON schemas for the allowed tools only. The service enum comes from the database."""
    codes = list(db.scalars(select(Service.code).order_by(Service.code)))
    staff_names = list(db.scalars(select(Staff.name).order_by(Staff.name)))
    props: dict[str, dict] = {
        "find_slots": {"service": {"type": "string", "enum": codes},
                       "date_from": {"type": "string", "description": "Local start, e.g. 2026-11-03 or 2026-11-03T14:00"},
                       "date_to": {"type": "string", "description": "Local end (inclusive day), e.g. 2026-11-03"},
                       "staff": {"type": "string", "enum": staff_names,
                                 "description": "Only if the caller asks for a particular person"}},
        "find_bookings": {"phone_e164": {"type": "string", "description": "E.164, e.g. +919876543210"}},
        "book": {"slot_id": {"type": "string", "description": "An offered slot alias such as slot_1"},
                 "name": {"type": "string", "description": "The caller's name as they gave it"},
                 "phone_e164": {"type": "string", "description": "E.164, e.g. +919876543210"}},
        "reschedule": {"booking_id": {"type": "string", "description": "A booking alias such as booking_1"},
                       "new_slot_id": {"type": "string", "description": "An offered slot alias such as slot_2"}},
        "cancel": {"booking_id": {"type": "string", "description": "A booking alias such as booking_1"},
                   "reason": {"type": "string"}},
        "transfer_to_human": {"reason": {"type": "string", "enum": HANDOFF_REASONS},
                              "summary": {"type": "string", "description": "Two short lines for the receptionist"}},
    }
    required = {"find_slots": ["service", "date_from", "date_to"], "find_bookings": ["phone_e164"],
                "book": ["slot_id", "name", "phone_e164"], "reschedule": ["booking_id", "new_slot_id"],
                "cancel": ["booking_id"], "transfer_to_human": ["reason", "summary"]}
    return [{"name": n, "description": DESCRIPTIONS[n],
             "parameters": {"type": "object", "properties": props[n], "required": required[n]}} for n in names]


@dataclass
class ToolResult:
    ok: bool
    say: str
    error_code: str | None = None
    data: dict = field(default_factory=dict)
    counts_as_failure: bool = False     # feeds the "two failed turns -> human" rule


@dataclass
class ToolContext:
    db: Session
    call: Call
    caller_text: str
    now: datetime
    turn_seq: int | None = None
    actor: str = "model"                # model | guard (a deterministic rule called the tool)


class ToolError(Exception):
    def __init__(self, code: str, say: str, *, failure: bool = True):
        super().__init__(say)
        self.code, self.say, self.failure = code, say, failure


def _parse_local(value: str, *, end: bool) -> datetime:
    tz = get_settings().tz
    try:
        if len(value) == 10:
            day = date.fromisoformat(value)
            return datetime.combine(day + timedelta(days=1) if end else day, datetime.min.time(), tz)
        return datetime.fromisoformat(value).replace(tzinfo=tz)
    except ValueError as exc:
        raise ToolError("invalid_date", "Sorry, which day did you mean?") from exc


def _phone(raw: str) -> str:
    phone = to_e164(raw, get_settings().default_country_code)
    if phone is None:
        raise ToolError("invalid_phone", "I didn't get a valid phone number. Could you say it again, digit by digit?")
    return phone


def _session(call: Call) -> dict:
    return dict(call.session or {})


def _slot_from_alias(session: dict, alias: str) -> str:
    offered = session.get("offered_slots", {})
    if alias in offered:
        return offered[alias]["slot_id"]
    for item in offered.values():          # tolerate the full id of an offered slot
        if item["slot_id"] == alias:
            return alias
    raise ToolError("unknown_slot", "I can only book a time I've offered. Let me check the calendar again.")


def _booking_from_alias(ctx: ToolContext, session: dict, alias: str) -> Booking:
    known = session.get("bookings", {})
    booking_id = known.get(alias, {}).get("booking_id") or next(
        (b["booking_id"] for b in known.values() if b["booking_id"] == alias), None)
    if booking_id is None:
        raise ToolError("unknown_booking", "I need to find your booking first. What number is it under?")
    booking = ctx.db.get(Booking, uuid.UUID(booking_id))
    if booking is None:
        raise ToolError("unknown_booking", "I couldn't find that booking.")
    return booking


def _spread(slots: list[cal.Slot]) -> list[cal.Slot]:
    """Up to MAX_OFFERED options at least an hour apart, earliest first, so the two spoken ones really differ."""
    picked: list[cal.Slot] = []
    for slot in slots:
        if not picked or slot.starts_at - picked[-1].starts_at >= timedelta(hours=1):
            picked.append(slot)
        if len(picked) == MAX_OFFERED:
            break
    return picked


def _offer(ctx: ToolContext, session: dict, slots: list[cal.Slot], service: Service) -> str:
    tz, now = get_settings().tz, ctx.now
    options = _spread(slots)
    session["offered_slots"] = {
        f"slot_{i}": {"slot_id": s.slot_id, "staff": s.staff_name, "service": s.service_code,
                      "when": say_when(s.starts_at, tz, now), "starts_at": s.starts_at.isoformat()}
        for i, s in enumerate(options, start=1)}
    session["spoken"] = list(session["offered_slots"])[:2]
    one = f"{say_when(options[0].starts_at, tz, now)} with {options[0].staff_name}"
    if len(options) == 1:
        return f"For a {service.name.lower()}, the only time I have is {one}. Would that work?"
    same_day = options[0].starts_at.astimezone(tz).date() == options[1].starts_at.astimezone(tz).date()
    second_when = say_time(options[1].starts_at.astimezone(tz)) if same_day else say_when(options[1].starts_at, tz, now)
    two = f"{second_when} with {options[1].staff_name}"
    return f"For a {service.name.lower()}, the earliest I have is {one}, or {two}. Would either of those work?"


def find_slots(ctx: ToolContext, args: FindSlotsArgs) -> ToolResult:
    session = _session(ctx.call)
    try:
        service = cal.get_service(ctx.db, args.service)
    except cal.CalendarError as exc:
        raise ToolError(exc.code, "Which service would you like: a haircut, beard trim, colour or manicure?") from exc
    start, end = _parse_local(args.date_from, end=False), _parse_local(args.date_to, end=True)
    if end <= start or end - start > timedelta(days=15):
        raise ToolError("invalid_range", "Which day would you like to come in?")
    staff_id = None
    if args.staff:
        staff_id = ctx.db.scalar(select(Staff.id).where(func.lower(Staff.name) == args.staff.lower()))
        if staff_id is None:
            team = list(ctx.db.scalars(select(Staff.name).order_by(Staff.name)))
            raise ToolError("unknown_staff", f"Sorry, I don't know that name. Our team is {', '.join(team[:-1])} "
                                             f"and {team[-1]}.")
    target = session.get("target_booking")
    slots = cal.find_free_slots(ctx.db, service, start, end, now=ctx.now, limit=SEARCH_LIMIT, staff_id=staff_id,
                                exclude_booking=uuid.UUID(target) if target else None)
    session["service"] = service.code
    session.setdefault("intent", "book")
    session.pop("pending", None)   # a new search voids any read-back the caller has not said yes to
    if not slots:
        session["offered_slots"] = {}
        ctx.call.session = session
        who = f" with {args.staff.capitalize()}" if args.staff else ""
        return ToolResult(True, f"I'm sorry, there are no {service.name.lower()} openings{who} then. "
                                "Would another day work for you?", data={"slots": []})
    say = _offer(ctx, session, slots, service)
    ctx.call.session = session
    ctx.call.state = State.CHECK_SLOTS
    return ToolResult(True, say, data={"slots": list(session["offered_slots"].keys())})


def find_bookings(ctx: ToolContext, args: FindBookingsArgs) -> ToolResult:
    session, phone = _session(ctx.call), _phone(args.phone_e164)
    bookings = cal.upcoming_bookings(ctx.db, phone, now=ctx.now)
    tz = get_settings().tz
    session["phone"] = phone
    if bookings:
        session["name"] = bookings[0].customer.name
    session["bookings"] = {
        f"booking_{i}": {"booking_id": str(b.id), "service": b.service.code, "staff": b.staff.name,
                         "when": say_when(b.starts_at, tz, ctx.now)} for i, b in enumerate(bookings, start=1)}
    session["target_booking"] = str(bookings[0].id) if len(bookings) == 1 else None
    if session.get("intent") not in ("reschedule", "cancel"):
        session["intent"] = "change"
    ctx.call.session = session
    if not bookings:
        return ToolResult(True, "I can't find any upcoming bookings for that number. Would you like to make one?",
                          data={"bookings": []})
    ctx.call.state = State.COLLECT
    first = bookings[0]
    say = f"I can see your {first.service.name.lower()} {say_when(first.starts_at, tz, ctx.now)} with {first.staff.name}."
    if len(bookings) > 1:
        say += f" You have {len(bookings) - 1} more after that."
    return ToolResult(True, say + " What would you like to do with it?", data={"bookings": list(session["bookings"])})


def _two_phase(ctx: ToolContext, session: dict, tool: str, args: dict, read_back: str) -> bool:
    """Returns True when this call should commit; otherwise stores the read-back and moves to CONFIRM."""
    pending = session.get("pending")
    if ctx.call.state == State.CONFIRM and pending == {"tool": tool, "args": args}:
        if is_clear_yes(ctx.caller_text):
            return True
        raise ToolError("not_confirmed", f"Sorry, I need a clear yes first. {read_back}", failure=False)
    session["pending"] = {"tool": tool, "args": args}
    ctx.call.session = session
    ctx.call.state = State.CONFIRM
    raise _ReadBack(read_back)


class _ReadBack(Exception):
    def __init__(self, say: str):
        super().__init__(say)
        self.say = say


def _after_commit(ctx: ToolContext, session: dict, outcome: CallOutcome, booking: Booking) -> None:
    session.pop("pending", None)
    session.pop("brain_notes", None)     # the request is done; the next one starts from a clean slate
    session["last_booking"] = str(booking.id)
    session["offered_slots"] = {}
    session["intent"] = None
    session["target_booking"] = None
    ctx.call.session = session
    ctx.call.state = State.BOOKED
    ctx.call.outcome = outcome
    ctx.call.customer_id = booking.customer_id


def _taken(ctx: ToolContext, session: dict, slot_id: str, exclude: uuid.UUID | None = None) -> ToolResult:
    """The database refused the slot. Apologise and offer the next real free times."""
    _, service_code, starts_at = cal.parse_slot_id(slot_id)
    service = cal.get_service(ctx.db, service_code)
    session.pop("pending", None)
    slots = cal.find_free_slots(ctx.db, service, starts_at, starts_at + timedelta(days=7), now=ctx.now,
                                limit=SEARCH_LIMIT, exclude_booking=exclude)
    if not slots:
        session["offered_slots"] = {}
        ctx.call.session = session
        ctx.call.state = State.COLLECT
        return ToolResult(False, "I'm sorry, that time was just taken and I have nothing else that week. "
                                 "Would another week work?", error_code="slot_taken")
    say = _offer(ctx, session, slots, service).replace("the earliest I have", "the next I have", 1)
    ctx.call.session = session
    ctx.call.state = State.CHECK_SLOTS
    return ToolResult(False, "I'm sorry, that time was just taken. " + say, error_code="slot_taken")


def book(ctx: ToolContext, args: BookArgs) -> ToolResult:
    session = _session(ctx.call)
    slot_id, phone, name = _slot_from_alias(session, args.slot_id), _phone(args.phone_e164), args.name.strip()
    try:
        slot = cal.validate_slot(ctx.db, slot_id, now=ctx.now)
    except cal.SlotTaken:
        return _taken(ctx, session, slot_id)
    except cal.CalendarError as exc:
        raise ToolError(exc.code, "That time isn't available. Shall I look for another?") from exc
    service = cal.get_service(ctx.db, slot.service_code)
    tz = get_settings().tz
    read_back = (f"Just to confirm: a {service.name.lower()} {say_when(slot.starts_at, tz, ctx.now)} with "
                 f"{slot.staff_name}, for {name}, that's {spell(name)}, and I'll text the number ending "
                 f"{say_phone_tail(phone)}. Shall I book it?")
    session["name"], session["phone"] = name, phone
    _two_phase(ctx, session, "book", {"slot_id": slot_id, "name": name, "phone_e164": phone}, read_back)
    try:
        booking, _ = cal.create_booking(ctx.db, slot, name=name, phone_e164=phone,
                                        idempotency_key=f"{ctx.call.id}:{slot_id}", call_id=ctx.call.id)
    except cal.SlotTaken:
        return _taken(ctx, session, slot_id)
    send_booking_sms(ctx.db, booking, "booked", ctx.call.id)
    _after_commit(ctx, session, CallOutcome.booked, booking)
    return ToolResult(True, f"You're booked: {service.name.lower()} {say_when(slot.starts_at, tz, ctx.now)} with "
                            f"{slot.staff_name}. I've sent a confirmation text. Is there anything else?",
                      data={"booking_id": str(booking.id)})


def reschedule(ctx: ToolContext, args: RescheduleArgs) -> ToolResult:
    session = _session(ctx.call)
    booking = _booking_from_alias(ctx, session, args.booking_id)
    slot_id = _slot_from_alias(session, args.new_slot_id)
    try:
        slot = cal.validate_slot(ctx.db, slot_id, now=ctx.now, exclude_booking=booking.id)
    except cal.SlotTaken:
        return _taken(ctx, session, slot_id, exclude=booking.id)
    except cal.CalendarError as exc:
        raise ToolError(exc.code, "That time isn't available. Shall I look for another?") from exc
    tz = get_settings().tz
    old, new = say_when(booking.starts_at, tz, ctx.now), say_when(slot.starts_at, tz, ctx.now)
    read_back = (f"Just to confirm: move your {booking.service.name.lower()} from {old} to {new} with "
                 f"{slot.staff_name}. Shall I go ahead?")
    _two_phase(ctx, session, "reschedule", {"booking_id": str(booking.id), "new_slot_id": slot_id}, read_back)
    try:
        cal.reschedule_booking(ctx.db, booking, slot)
    except cal.SlotTaken:
        return _taken(ctx, session, slot_id, exclude=booking.id)
    send_booking_sms(ctx.db, booking, "rescheduled", ctx.call.id)
    _after_commit(ctx, session, CallOutcome.rescheduled, booking)
    return ToolResult(True, f"Done. Your {booking.service.name.lower()} is now {new} with {slot.staff_name}. "
                            "I've texted you the new time. Anything else?", data={"booking_id": str(booking.id)})


def cancel(ctx: ToolContext, args: CancelArgs) -> ToolResult:
    session = _session(ctx.call)
    booking = _booking_from_alias(ctx, session, args.booking_id)
    tz = get_settings().tz
    when = say_when(booking.starts_at, tz, ctx.now)
    read_back = f"Just to confirm: cancel your {booking.service.name.lower()} {when}. Shall I cancel it?"
    _two_phase(ctx, session, "cancel", {"booking_id": str(booking.id), "reason": args.reason}, read_back)
    cal.cancel_booking(ctx.db, booking, args.reason)
    send_booking_sms(ctx.db, booking, "cancelled", ctx.call.id)
    _after_commit(ctx, session, CallOutcome.cancelled, booking)
    return ToolResult(True, f"That's cancelled: your {booking.service.name.lower()} {when}. "
                            "I've sent you a text. Anything else?", data={"booking_id": str(booking.id)})


def transfer_to_human(ctx: ToolContext, args: TransferArgs) -> ToolResult:
    call = ctx.call
    call.state = State.HANDOFF
    call.outcome = CallOutcome.handoff
    call.handoff_reason = args.reason
    call.handoff_status = HandoffStatus.waiting
    call.summary = args.summary.strip()[:300]
    return ToolResult(True, "I'm transferring you to a receptionist now. I've passed on a quick summary, "
                            "so you won't need to repeat yourself.", data={"reason": args.reason})


HANDLERS: dict[str, Callable[[ToolContext, Any], ToolResult]] = {
    "find_slots": find_slots, "find_bookings": find_bookings, "book": book, "reschedule": reschedule,
    "cancel": cancel, "transfer_to_human": transfer_to_human,
}


def run_tool(ctx: ToolContext, name: str, raw_args: dict) -> ToolResult:
    """The tool router: state check, schema validation, execution, and one ``tool_calls`` row per attempt."""
    started = time.perf_counter()
    state = State(ctx.call.state)
    try:
        if name not in HANDLERS:
            raise ToolError("unknown_tool", "Sorry, I can't do that over the phone.")
        if name not in ALLOWED_TOOLS[state]:
            raise ToolError("tool_not_allowed", "Sorry, let me take that one step at a time.")
        try:
            args = ARG_MODELS[name].model_validate(raw_args or {})
        except ValidationError as exc:
            field_name = str(exc.errors()[0]["loc"][0]) if exc.errors() and exc.errors()[0]["loc"] else ""
            hint = {"name": "Could I take your name, please?",
                    "phone_e164": "What's the best phone number for the confirmation text?"}.get(
                field_name, "Sorry, could you say that again?")
            raise ToolError("invalid_args", hint) from exc
        result = HANDLERS[name](ctx, args)
    except _ReadBack as rb:
        result = ToolResult(True, rb.say, data={"read_back": True})
    except ToolError as exc:
        result = ToolResult(False, exc.say, error_code=exc.code, counts_as_failure=exc.failure)
    latency = round((time.perf_counter() - started) * 1000)
    ctx.db.add(ToolCall(call_id=ctx.call.id, turn_seq=ctx.turn_seq, tool=name, args=raw_args or {}, ok=result.ok,
                        error_code=result.error_code, state=state.value, latency_ms=latency,
                        result={"say": result.say, "actor": ctx.actor, **result.data}))
    ctx.db.flush()
    return result


assert set(HANDLERS) == set(ARG_MODELS) and all(t in HANDLERS for ts in ALLOWED_TOOLS.values() for t in ts)
assert COMMIT_TOOLS <= set(HANDLERS)
