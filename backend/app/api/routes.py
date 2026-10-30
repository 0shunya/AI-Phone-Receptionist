"""HTTP endpoints: the roadmap's API surface plus the read endpoints the console UI needs."""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session, selectinload

from app import __version__
from app.agent.engine import CallClosed
from app.agent.factory import get_engine
from app.api.errors import ApiError
from app.api.ratelimit import SlidingWindowLimiter
from app.api.schemas import (BookingCreate, BookingOut, BookingUpdate, CallerTurn, HandoffRequest, HandoffUpdate,
                             IncomingCall, SlotOut)
from app.config import get_settings
from app.db import get_db
from app.models import (Booking, BookingStatus, Call, HandoffStatus, ModelCall, Service, SmsMessage,
                        Speaker, Staff, ToolCall, Turn)
from app.services import calendar as cal
from app.services.notify import send_booking_sms
from app.services.observability import percentile
from app.services.phone import to_e164

router = APIRouter(prefix="/api")
call_limiter = SlidingWindowLimiter(get_settings().calls_per_hour_per_client, 3600)
turn_limiter = SlidingWindowLimiter(get_settings().turns_per_minute_per_call, 60)


def _uuid(value: str, what: str = "resource") -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError as exc:
        raise ApiError(404, "not_found", f"No such {what}.") from exc


def _now() -> datetime:
    return get_engine().clock()


def booking_out(b: Booking) -> BookingOut:
    return BookingOut(id=str(b.id), service=b.service.code, service_name=b.service.name, staff=b.staff.name,
                      staff_id=str(b.staff_id), customer=b.customer.name, phone_e164=b.customer.phone_e164,
                      starts_at=b.starts_at, ends_at=b.ends_at, status=b.status.value, event_id=b.event_id,
                      call_id=str(b.call_id) if b.call_id else None, created_at=b.created_at)


def _get_booking(db: Session, booking_id: str) -> Booking:
    booking = db.scalar(select(Booking).options(selectinload(Booking.service), selectinload(Booking.staff),
                                                selectinload(Booking.customer)).where(Booking.id == _uuid(booking_id, "booking")))
    if booking is None:
        raise ApiError(404, "not_found", "No such booking.")
    return booking


def _calendar_error(exc: cal.CalendarError) -> ApiError:
    status = 409 if exc.code == "slot_taken" else 422
    return ApiError(status, exc.code, exc.message)


@router.get("/health")
def health(db: Session = Depends(get_db)):
    settings = get_settings()
    db.execute(text("SELECT 1"))
    return {"status": "ok", "version": __version__, "database": "ok", "brain": settings.agent_brain,
            "model": settings.gemini_model if settings.agent_brain == "gemini" else None,
            "model_configured": settings.gemini_configured, "business": settings.business_name,
            "timezone": settings.business_timezone}


@router.get("/services")
def list_services(db: Session = Depends(get_db)):
    return [{"code": s.code, "name": s.name, "duration_min": s.duration_min, "price_minor": s.price_minor}
            for s in db.scalars(select(Service).order_by(Service.name))]


@router.get("/staff")
def list_staff(db: Session = Depends(get_db)):
    staff = db.scalars(select(Staff).options(selectinload(Staff.shifts)).order_by(Staff.name))
    return [{"id": str(s.id), "name": s.name, "calendar_id": s.calendar_id, "timezone": s.timezone,
             "shifts": [{"weekday": sh.weekday, "start": sh.start_local.strftime("%H:%M"),
                         "end": sh.end_local.strftime("%H:%M")} for sh in s.shifts]} for s in staff]


@router.get("/availability", response_model=list[SlotOut])
def availability(service: str, date_from: date, date_to: date, limit: int = Query(default=50, ge=1, le=200),
                 db: Session = Depends(get_db)):
    if date_to < date_from or (date_to - date_from).days > 14:
        raise ApiError(422, "invalid_range", "date_to must be on or after date_from, at most 14 days later.")
    try:
        svc = cal.get_service(db, service)
    except cal.CalendarError as exc:
        raise _calendar_error(exc) from exc
    start, end = cal.local_range(date_from, date_to)
    return [SlotOut(slot_id=s.slot_id, starts_at=s.starts_at, ends_at=s.ends_at, staff=s.staff_name, service=s.service_code)
            for s in cal.find_free_slots(db, svc, start, end, now=_now(), limit=limit)]


@router.get("/bookings", response_model=list[BookingOut])
def list_bookings(date_from: date | None = None, date_to: date | None = None, include_cancelled: bool = False,
                  db: Session = Depends(get_db)):
    query = select(Booking).options(selectinload(Booking.service), selectinload(Booking.staff),
                                    selectinload(Booking.customer)).order_by(Booking.starts_at)
    if date_from and date_to:
        start, end = cal.local_range(date_from, date_to)
        query = query.where(Booking.starts_at < end, Booking.ends_at > start)
    if not include_cancelled:
        query = query.where(Booking.status != BookingStatus.cancelled)
    return [booking_out(b) for b in db.scalars(query.limit(500))]


@router.post("/bookings", response_model=BookingOut, status_code=201)
def create_booking(body: BookingCreate, response: Response, db: Session = Depends(get_db),
                   idempotency_key: str | None = Header(default=None, max_length=200)):
    phone = to_e164(body.phone_e164, get_settings().default_country_code)
    if phone is None:
        raise ApiError(422, "invalid_phone", "phone_e164 must be a valid E.164 number such as +919876543210.")
    key = idempotency_key or (f"{body.call_id}:{body.slot_id}" if body.call_id else None)
    try:
        slot = cal.validate_slot(db, body.slot_id, now=_now())
    except cal.CalendarError as exc:
        if key and (existing := db.scalar(select(Booking).where(Booking.idempotency_key == key))):
            response.status_code = 200
            return booking_out(_get_booking(db, str(existing.id)))
        raise _calendar_error(exc) from exc
    try:
        booking, created = cal.create_booking(db, slot, name=body.name, phone_e164=phone, idempotency_key=key)
    except cal.CalendarError as exc:
        db.rollback()
        raise _calendar_error(exc) from exc
    if created:
        send_booking_sms(db, booking, "booked")
    else:
        response.status_code = 200
    db.commit()
    return booking_out(_get_booking(db, str(booking.id)))


@router.patch("/bookings/{booking_id}", response_model=BookingOut)
def update_booking(booking_id: str, body: BookingUpdate, db: Session = Depends(get_db)):
    booking = _get_booking(db, booking_id)
    try:
        if body.action == "cancel":
            if booking.status != BookingStatus.cancelled:
                cal.cancel_booking(db, booking, body.reason)
                send_booking_sms(db, booking, "cancelled")
        else:
            if not body.new_slot_id:
                raise ApiError(422, "invalid_request", "new_slot_id is required to reschedule.")
            slot = cal.validate_slot(db, body.new_slot_id, now=_now(), exclude_booking=booking.id)
            cal.reschedule_booking(db, booking, slot)
            send_booking_sms(db, booking, "rescheduled")
    except cal.CalendarError as exc:
        db.rollback()
        raise _calendar_error(exc) from exc
    db.commit()
    return booking_out(_get_booking(db, booking_id))


# --- calls -------------------------------------------------------------------------------------------------

def _client_key(request: Request) -> str:
    return request.client.host if request.client else "unknown"


@router.post("/voice/incoming", status_code=201)
def incoming_call(body: IncomingCall, request: Request, db: Session = Depends(get_db)):
    """Answer a call: create it, speak the greeting at once, and point the client at the stream."""
    if not call_limiter.allow(_client_key(request)):
        raise ApiError(429, "rate_limited", "Too many calls from this browser. Try again later.")
    phone = to_e164(body.caller_phone, get_settings().default_country_code) if body.caller_phone else None
    if body.caller_phone and phone is None:
        raise ApiError(422, "invalid_phone", "caller_phone must be a valid phone number.")
    call, greeting = get_engine().start_call(db, caller_phone=phone, channel=body.channel)
    db.commit()
    return {"call_id": str(call.id), "state": call.state, "greeting": greeting.text, "seq": greeting.seq,
            "stream_path": f"/api/voice/stream?call_id={call.id}"}


@router.post("/calls/{call_id}/turns")
def caller_turn(call_id: str, body: CallerTurn, db: Session = Depends(get_db)):
    """Text mode (milestone M1): the same engine as the voice stream, without audio."""
    cid = _uuid(call_id, "call")
    if not turn_limiter.allow(call_id):
        raise ApiError(429, "rate_limited", "Too many turns on this call. Slow down a little.")
    try:
        outcome = get_engine().handle_turn(db, cid, body.text)
    except CallClosed as exc:
        db.rollback()
        raise ApiError(409, "call_closed", "This call has ended.") from exc
    db.commit()
    return outcome.__dict__


@router.post("/calls/{call_id}/handoff")
def handoff(call_id: str, body: HandoffRequest, db: Session = Depends(get_db)):
    try:
        call = get_engine().handoff(db, _uuid(call_id, "call"), body.reason, body.summary)
    except CallClosed as exc:
        db.rollback()
        raise ApiError(409, "call_closed", "This call has ended.") from exc
    db.commit()
    return {"call_id": str(call.id), "state": call.state, "summary": call.summary, "reason": call.handoff_reason}


@router.post("/calls/{call_id}/end")
def end_call(call_id: str, db: Session = Depends(get_db)):
    try:
        call = get_engine().end_call(db, _uuid(call_id, "call"))
    except CallClosed as exc:
        raise ApiError(404, "not_found", "No such call.") from exc
    db.commit()
    return {"call_id": str(call.id), "outcome": call.outcome.value, "ended_at": call.ended_at}


def _call_row(c: Call, turns: int = 0) -> dict:
    return {"id": str(c.id), "channel": c.channel, "caller_phone": c.caller_phone, "outcome": c.outcome.value,
            "state": c.state, "summary": c.summary, "handoff_reason": c.handoff_reason,
            "handoff_status": c.handoff_status.value if c.handoff_status else None, "brain": c.brain,
            "started_at": c.started_at, "ended_at": c.ended_at, "turns": turns}


@router.get("/calls")
def list_calls(limit: int = Query(default=50, ge=1, le=200), channel: str | None = None,
               db: Session = Depends(get_db)):
    counts = select(Turn.call_id, func.count(Turn.id).label("n")).group_by(Turn.call_id).subquery()
    query = select(Call, func.coalesce(counts.c.n, 0)).outerjoin(counts, counts.c.call_id == Call.id)
    if channel:
        query = query.where(Call.channel == channel)
    rows = db.execute(query.order_by(Call.started_at.desc()).limit(limit)).all()
    return [_call_row(c, n) for c, n in rows]


@router.get("/calls/{call_id}")
def get_call(call_id: str, db: Session = Depends(get_db)):
    """Transcript, tool calls and latency per turn."""
    call = db.get(Call, _uuid(call_id, "call"))
    if call is None:
        raise ApiError(404, "not_found", "No such call.")
    turns = list(db.scalars(select(Turn).where(Turn.call_id == call.id).order_by(Turn.seq)))
    tools = list(db.scalars(select(ToolCall).where(ToolCall.call_id == call.id).order_by(ToolCall.created_at)))
    models = list(db.scalars(select(ModelCall).where(ModelCall.call_id == call.id).order_by(ModelCall.created_at)))
    bookings = list(db.scalars(select(Booking).options(selectinload(Booking.service), selectinload(Booking.staff),
                                                       selectinload(Booking.customer)).where(Booking.call_id == call.id)))
    agent = [t for t in turns if t.speaker == Speaker.agent and t.seq > 1]
    return {
        **_call_row(call, len(turns)), "session": {k: v for k, v in (call.session or {}).items() if k != "brain_notes"},
        "turns": [{"seq": t.seq, "speaker": t.speaker.value, "text": t.text, "state": t.state,
                   "eos_to_audio_ms": t.eos_to_audio_ms, "server_first_ms": t.server_first_ms,
                   "server_total_ms": t.server_total_ms, "interrupted": t.interrupted, "created_at": t.created_at}
                  for t in turns],
        "tool_calls": [{"id": str(t.id), "turn_seq": t.turn_seq, "tool": t.tool, "args": t.args, "ok": t.ok,
                        "error_code": t.error_code, "state": t.state, "latency_ms": t.latency_ms,
                        "say": (t.result or {}).get("say"), "actor": (t.result or {}).get("actor")} for t in tools],
        "model_calls": [{"model": m.model, "status": m.status, "latency_ms": m.latency_ms,
                         "first_token_ms": m.first_token_ms, "input_tokens": m.input_tokens,
                         "output_tokens": m.output_tokens, "error_category": m.error_category,
                         "prompt_version": m.prompt_version} for m in models],
        "bookings": [booking_out(b).model_dump(mode="json") for b in bookings],
        "latency": {"server_first_p50": percentile([t.server_first_ms for t in agent if t.server_first_ms is not None], 50),
                    "server_first_p95": percentile([t.server_first_ms for t in agent if t.server_first_ms is not None], 95),
                    "eos_to_audio_p50": percentile([t.eos_to_audio_ms for t in agent if t.eos_to_audio_ms is not None], 50),
                    "eos_to_audio_p95": percentile([t.eos_to_audio_ms for t in agent if t.eos_to_audio_ms is not None], 95)},
    }


@router.get("/handoffs")
def list_handoffs(db: Session = Depends(get_db)):
    order = {HandoffStatus.waiting: 0, HandoffStatus.claimed: 1, HandoffStatus.resolved: 2}
    calls = list(db.scalars(select(Call).where(Call.handoff_status.is_not(None)).order_by(Call.started_at.desc()).limit(100)))
    calls.sort(key=lambda c: order[c.handoff_status])
    return [_call_row(c) for c in calls]


@router.patch("/calls/{call_id}/handoff")
def update_handoff(call_id: str, body: HandoffUpdate, db: Session = Depends(get_db)):
    call = db.get(Call, _uuid(call_id, "call"))
    if call is None or call.handoff_status is None:
        raise ApiError(404, "not_found", "No handoff for this call.")
    call.handoff_status = HandoffStatus(body.status)
    db.commit()
    return _call_row(call)


@router.get("/sms")
def list_sms(limit: int = Query(default=100, ge=1, le=500), db: Session = Depends(get_db)):
    rows = db.scalars(select(SmsMessage).order_by(SmsMessage.created_at.desc()).limit(limit))
    return [{"id": str(m.id), "to": m.to_e164, "body": m.body, "kind": m.kind, "status": m.status,
             "booking_id": str(m.booking_id) if m.booking_id else None,
             "call_id": str(m.call_id) if m.call_id else None, "created_at": m.created_at} for m in rows]


@router.get("/metrics")
def metrics(days: int = Query(default=7, ge=1, le=90), channel: str | None = None, db: Session = Depends(get_db)):
    """Turn latency percentiles and outcomes from the call log (live calls; evaluation runs use their own DB)."""
    since = datetime.now(timezone.utc) - timedelta(days=days)
    call_filter = [Call.started_at >= since] + ([Call.channel == channel] if channel else [])
    call_ids = select(Call.id).where(*call_filter)
    turns = db.execute(select(Turn.eos_to_audio_ms, Turn.server_first_ms, Turn.server_total_ms).where(
        Turn.call_id.in_(call_ids), Turn.speaker == Speaker.agent, Turn.seq > 1)).all()
    outcomes = dict(db.execute(select(Call.outcome, func.count()).where(*call_filter).group_by(Call.outcome)).all())
    tools = db.execute(select(ToolCall.ok, func.count()).where(ToolCall.call_id.in_(call_ids)).group_by(ToolCall.ok)).all()
    models = db.execute(select(ModelCall.latency_ms, ModelCall.first_token_ms, ModelCall.status).where(
        ModelCall.created_at >= since)).all()

    def stats(values: list[int | None]) -> dict:
        vals = [v for v in values if v is not None]
        return {"n": len(vals), "p50": percentile(vals, 50), "p95": percentile(vals, 95)}

    ok_models = [m for m in models if m.status == "success"]
    return {
        "days": days,
        "turns": {"eos_to_audio_ms": stats([t[0] for t in turns]), "server_first_ms": stats([t[1] for t in turns]),
                  "server_total_ms": stats([t[2] for t in turns])},
        "outcomes": {k.value: v for k, v in outcomes.items()},
        "calls": sum(outcomes.values()),
        "tool_calls": {"ok": next((n for ok, n in tools if ok), 0), "failed": next((n for ok, n in tools if not ok), 0)},
        "model_calls": {"n": len(models), "errors": len(models) - len(ok_models),
                        "latency_ms": stats([m.latency_ms for m in ok_models]),
                        "first_token_ms": stats([m.first_token_ms for m in ok_models])},
    }

