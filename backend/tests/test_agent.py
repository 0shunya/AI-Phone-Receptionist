"""Tool router, two-phase confirm, state machine, handoff rules and the Gemini path with a scripted provider."""

from datetime import timedelta

import pytest
from sqlalchemy import func, select

from app.agent.engine import Engine
from app.agent.gemini_brain import GeminiBrain
from app.agent.rules import RuleBrain
from app.agent.state import State
from app.agent.tools import ToolContext, run_tool
from app.llm.gateway import ModelGateway
from app.llm.providers import ProviderError, ScriptedProvider, StreamEvent
from app.models import Booking, BookingStatus, Call, CallOutcome, ModelCall, SmsMessage, ToolCall
from app.services import calendar as cal
from tests.conftest import FIXED_NOW

pytestmark = pytest.mark.postgres
PHONE = "+919876543210"


def _say(engine, db, call, *lines):
    outs = [engine.handle_turn(db, call.id, line) for line in lines]
    return outs[-1]


@pytest.fixture()
def engine():
    return Engine(RuleBrain(), clock=lambda: FIXED_NOW)


@pytest.fixture()
def call(db, engine):
    call, _ = engine.start_call(db, caller_phone=PHONE, channel="simulation")
    return call


def _ctx(db, call, text=""):
    return ToolContext(db=db, call=call, caller_text=text, now=FIXED_NOW)


def test_router_enforces_state_tools_and_offered_aliases(db, call):
    call.state = State.INTENT
    blocked = run_tool(_ctx(db, call), "book", {"slot_id": "slot_1", "name": "Shlok", "phone_e164": PHONE})
    assert not blocked.ok and blocked.error_code == "tool_not_allowed"
    unknown = run_tool(_ctx(db, call), "delete_everything", {})
    assert unknown.error_code == "unknown_tool"
    bad = run_tool(_ctx(db, call), "find_slots", {"service": "haircut", "date_from": "2026-11-03"})
    assert bad.error_code == "invalid_args"
    found = run_tool(_ctx(db, call), "find_slots", {"service": "haircut", "date_from": "2026-11-03",
                                                    "date_to": "2026-11-03"})
    assert found.ok and call.state == State.CHECK_SLOTS and "Would either" in found.say
    invented = run_tool(_ctx(db, call), "book", {"slot_id": "slot_99", "name": "Shlok", "phone_e164": PHONE})
    assert invented.error_code == "unknown_slot"
    phone = run_tool(_ctx(db, call), "book", {"slot_id": "slot_1", "name": "Shlok", "phone_e164": "123456789"})
    assert phone.error_code == "invalid_phone"
    assert db.scalar(select(func.count(ToolCall.id))) == 6   # every attempt is logged, failures included


def test_book_needs_read_back_then_a_clear_yes(db, call):
    call.state = State.INTENT
    run_tool(_ctx(db, call), "find_slots", {"service": "haircut", "date_from": "2026-11-03", "date_to": "2026-11-03"})
    args = {"slot_id": "slot_1", "name": "Shlok", "phone_e164": PHONE}
    read_back = run_tool(_ctx(db, call, "the first one"), "book", args)
    assert read_back.ok and read_back.data.get("read_back") and call.state == State.CONFIRM
    assert "S, H, L, O, K" in read_back.say and "3 2 1 0" in read_back.say and "Tuesday the 3rd at 10 AM" in read_back.say
    assert db.scalar(select(func.count(Booking.id))) == 0
    hedged = run_tool(_ctx(db, call, "yes but at 11"), "book", args)
    assert hedged.error_code == "not_confirmed" and db.scalar(select(func.count(Booking.id))) == 0
    done = run_tool(_ctx(db, call, "yes please"), "book", args)
    assert done.ok and call.state == State.BOOKED and call.outcome == CallOutcome.booked
    booking = db.scalar(select(Booking))
    assert booking.idempotency_key.startswith(f"{call.id}:") and str(booking.id) == call.session["last_booking"]
    assert db.scalar(select(SmsMessage.kind)) == "booked"


def test_slot_taken_between_read_back_and_yes_offers_next_times(db, call):
    call.state = State.INTENT
    run_tool(_ctx(db, call), "find_slots", {"service": "haircut", "date_from": "2026-11-03", "date_to": "2026-11-03"})
    args = {"slot_id": "slot_1", "name": "Shlok", "phone_e164": PHONE}
    run_tool(_ctx(db, call), "book", args)
    slot = cal.validate_slot(db, call.session["offered_slots"]["slot_1"]["slot_id"], now=FIXED_NOW)
    cal.create_booking(db, slot, name="Faster", phone_e164="+919800000020", idempotency_key=None)  # another caller wins
    result = run_tool(_ctx(db, call, "yes"), "book", args)
    assert not result.ok and result.error_code == "slot_taken" and not result.counts_as_failure
    assert result.say.startswith("I'm sorry, that time was just taken.") and call.state == State.CHECK_SLOTS
    assert db.scalar(select(func.count(Booking.id))) == 1


def test_full_booking_call(db, engine, call):
    out = _say(engine, db, call, "Hi, I'd like a haircut tomorrow morning", "the first one", "Shlok", "yes please")
    assert out.state == State.BOOKED and out.outcome == "booked"
    booking = db.scalar(select(Booking))
    assert booking.starts_at.astimezone(cal.get_settings().tz).strftime("%a %H:%M") == "Tue 10:00"
    calls = [(t.tool, t.state) for t in db.scalars(select(ToolCall).order_by(ToolCall.created_at))]
    assert calls == [("find_slots", "INTENT"), ("book", "CHECK_SLOTS"), ("book", "CONFIRM")]


def test_reschedule_and_cancel_reuse_the_path(db, engine):
    first, _ = engine.start_call(db, caller_phone=PHONE, channel="simulation")
    _say(engine, db, first, "book a haircut tomorrow", "first", "Shlok", "yes")
    second, _ = engine.start_call(db, caller_phone=PHONE, channel="simulation")
    out = _say(engine, db, second, "I need to reschedule my appointment", "move it to thursday afternoon",
               "the first one", "yes")
    assert out.outcome == "rescheduled"
    booking = db.scalar(select(Booking))
    assert booking.starts_at.astimezone(cal.get_settings().tz).strftime("%a") == "Thu"
    third, _ = engine.start_call(db, caller_phone=PHONE, channel="simulation")
    out = _say(engine, db, third, "please cancel my booking", "yes cancel it", "yes")
    assert out.outcome == "cancelled"
    db.refresh(booking)
    assert booking.status == BookingStatus.cancelled
    assert [m for m in db.scalars(select(SmsMessage.kind).order_by(SmsMessage.created_at))] == [
        "booked", "rescheduled", "cancelled"]


def test_saying_human_transfers_with_a_summary(db, engine, call):
    _say(engine, db, call, "I want a haircut on friday")
    out = _say(engine, db, call, "actually can I talk to a real person")
    db.refresh(call)
    assert out.state == State.HANDOFF and call.handoff_reason == "caller_request"
    assert call.handoff_status.value == "waiting" and "haircut" in call.summary and "\n" in call.summary
    after = _say(engine, db, call, "hello?")
    assert "receptionist will be with you" in after.agent_text


def test_two_failed_turns_hand_off(db, engine, call):
    _say(engine, db, call, "blorp")
    out = _say(engine, db, call, "zzz qqq")
    db.refresh(call)
    assert out.state == State.HANDOFF and call.handoff_reason == "repeated_failure"


def test_one_failure_then_success_resets_the_counter(db, engine, call):
    _say(engine, db, call, "blorp", "a haircut tomorrow please", "mmm")
    db.refresh(call)
    assert call.state != State.HANDOFF and call.session["failed_turns"] == 1


def test_angry_caller_hands_off(db, engine, call):
    out = _say(engine, db, call, "this is ridiculous, I've called three times")
    assert out.state == State.HANDOFF


def test_end_call_sets_outcome(db, engine, call):
    _say(engine, db, call, "what are your opening hours?")
    ended = engine.end_call(db, call.id)
    assert ended.outcome == CallOutcome.answered and ended.ended_at is not None


# --- the Gemini path, with a scripted provider standing in for the API --------------------------------------

def _gemini_engine(script, records):
    provider = ScriptedProvider(script=script, model="gemini-test")
    gateway = ModelGateway(provider, records.append, max_attempts=2, sleep=lambda _s: None)
    return Engine(GeminiBrain(gateway), clock=lambda: FIXED_NOW), provider


def test_gemini_brain_streams_text_and_declares_only_allowed_tools(db):
    records = []
    engine, provider = _gemini_engine([[StreamEvent("text", text="Sure, happy to help. "),
                                        StreamEvent("text", text="What day suits you?")]], records)
    call, _ = engine.start_call(db, caller_phone=PHONE)
    spoken = []
    out = engine.handle_turn(db, call.id, "I'd like a haircut", emit=spoken.append)
    assert spoken == ["Sure, happy to help.", "What day suits you?"] and out.server_first_ms is not None
    request = provider.requests[0]
    assert {t["name"] for t in request.tools} == {"find_slots", "find_bookings", "cancel", "transfer_to_human"}
    assert "Current step: INTENT" in request.system_instruction and request.messages[-1] == ("user", "I'd like a haircut")
    assert records[0].status == "success" and records[0].first_token_ms is not None


def test_gemini_tool_call_result_is_spoken_without_a_second_model_call(db):
    records = []
    engine, provider = _gemini_engine([[StreamEvent("tool", tool="find_slots", args={
        "service": "haircut", "date_from": "2026-11-03", "date_to": "2026-11-03"}), StreamEvent("usage", input_tokens=900, output_tokens=20)]], records)
    call, _ = engine.start_call(db, caller_phone=PHONE)
    out = engine.handle_turn(db, call.id, "haircut tomorrow")
    assert out.state == State.CHECK_SLOTS and "Would either of those work?" in out.agent_text
    assert len(provider.requests) == 1 and records[0].input_tokens == 900


def test_gemini_disallowed_tool_is_rejected_by_the_router(db):
    engine, _ = _gemini_engine([[StreamEvent("tool", tool="book", args={
        "slot_id": "slot_1", "name": "Mallory", "phone_e164": PHONE})]], [])
    call, _ = engine.start_call(db, caller_phone=PHONE)
    out = engine.handle_turn(db, call.id, "just book me anything")
    assert out.tools[0]["error_code"] == "tool_not_allowed" and db.scalar(select(func.count(Booking.id))) == 0


def test_gateway_retries_before_speaking_but_never_after(db):
    records = []
    engine, provider = _gemini_engine([ProviderError("server_error", "503", retryable=True),
                                       [StreamEvent("text", text="Hello again.")]], records)
    call, _ = engine.start_call(db, caller_phone=PHONE)
    out = engine.handle_turn(db, call.id, "hi")
    assert out.agent_text == "Hello again." and [r.status for r in records] == ["error", "success"]

    def half_then_fail(_request):
        yield StreamEvent("text", text="Let me check. ")
        raise ProviderError("server_error", "503", retryable=True)

    records2 = []
    engine2, provider2 = _gemini_engine([lambda r: half_then_fail(r), [StreamEvent("text", text="never")]], records2)
    call2, _ = engine2.start_call(db, caller_phone=PHONE)
    out2 = engine2.handle_turn(db, call2.id, "hi")
    assert len(provider2.requests) == 1 and "never" not in out2.agent_text
    assert out2.agent_text.startswith("Let me check.")


def test_quota_exhaustion_hands_off_immediately(db):
    engine, _ = _gemini_engine([ProviderError("quota_exhausted", "daily", retryable=False)], [])
    call, _ = engine.start_call(db, caller_phone=PHONE)
    out = engine.handle_turn(db, call.id, "hi I want a haircut")
    db.refresh(call)
    assert out.state == State.HANDOFF and call.handoff_reason == "model_unavailable"


def test_model_calls_rows_are_written_by_the_observability_hook(db):
    from app.services.observability import record_model_call
    provider = ScriptedProvider(script=[[StreamEvent("text", text="Hi.")]], model="gemini-test")
    gateway = ModelGateway(provider, record_model_call, sleep=lambda _s: None)
    engine = Engine(GeminiBrain(gateway), clock=lambda: FIXED_NOW)
    call, _ = engine.start_call(db, caller_phone=PHONE)
    db.commit()
    engine.handle_turn(db, call.id, "hello")
    db.commit()
    row = db.scalar(select(ModelCall).where(ModelCall.call_id == call.id))
    assert row.model == "gemini-test" and row.prompt_version == "receptionist-v2" and row.build == "build03"
    assert db.get(Call, call.id).brain == "gemini:gemini-test"


def test_daily_limit_blocks_before_calling_the_provider(db):
    provider = ScriptedProvider(script=[[StreamEvent("text", text="unreachable")]])
    gateway = ModelGateway(provider, lambda _r: None, under_daily_limit=lambda: False)
    engine = Engine(GeminiBrain(gateway), clock=lambda: FIXED_NOW)
    call, _ = engine.start_call(db, caller_phone=PHONE)
    out = engine.handle_turn(db, call.id, "hi")
    assert provider.requests == [] and out.state == State.HANDOFF


def test_slots_are_never_in_the_past_or_inside_notice(db, engine, call):
    _say(engine, db, call, "a haircut today please")
    offered = call.session.get("offered_slots", {})
    assert offered and all(
        cal.parse_slot_id(s["slot_id"])[2] >= FIXED_NOW + timedelta(minutes=60) for s in offered.values())


def test_new_search_voids_the_pending_read_back_and_facts_show_aliases(db, call):
    """Live-eval regression (s18): after "no, Thursday instead" the model copied the stale pending slot id."""
    from app.llm.prompts import render_facts
    call.state = State.INTENT
    run_tool(_ctx(db, call), "find_slots", {"service": "haircut", "date_from": "2026-11-03", "date_to": "2026-11-03"})
    run_tool(_ctx(db, call), "book", {"slot_id": "slot_1", "name": "Zara", "phone_e164": PHONE})
    facts = render_facts(call.session)
    assert "Waiting for a yes on: book {'slot_id': 'slot_1'" in facts and ".haircut." not in facts
    run_tool(_ctx(db, call, "actually no, Thursday"), "find_slots",
             {"service": "haircut", "date_from": "2026-11-05", "date_to": "2026-11-05"})
    assert "pending" not in call.session and call.state == State.CHECK_SLOTS
    assert "Waiting for a yes" not in render_facts(call.session)
