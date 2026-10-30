"""The calendar is the truth: availability, the Postgres no-overlap constraint, idempotency, races (milestone M2)."""

import threading
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

from app.db import SessionLocal
from app.models import Booking, BookingStatus, Staff
from app.services import calendar as cal
from tests.conftest import FIXED_NOW

pytestmark = pytest.mark.postgres
TUE = date(2026, 11, 3)


def _slots(db, code="haircut", day=TUE, limit=200, **kw):
    start, end = cal.local_range(day, day)
    return cal.find_free_slots(db, cal.get_service(db, code), start, end, now=FIXED_NOW, limit=limit, **kw)


def test_slots_follow_shifts_steps_and_notice(db):
    slots = _slots(db)
    ist = [s.starts_at.astimezone(cal.get_settings().tz) for s in slots]
    assert ist[0].hour == 10 and ist[0].minute == 0                       # Asha and Ravi start at 10:00
    assert all(t.minute % 15 == 0 for t in ist)
    assert {s.staff_name for s in slots} == {"Asha", "Ravi"}              # Meera does not work Tuesdays
    asha_last = max(t for t, s in zip(ist, slots) if s.staff_name == "Asha")
    assert (asha_last.hour, asha_last.minute) == (18, 30)                 # 30 min haircut must end by 19:00
    monday = _slots(db, day=date(2026, 11, 2))
    assert monday and all(s.starts_at >= FIXED_NOW + timedelta(minutes=60) for s in monday)   # min notice


def test_booking_removes_slot_and_slot_id_round_trips(db):
    slot = _slots(db)[0]
    staff_id, code, starts = cal.parse_slot_id(slot.slot_id)
    assert (staff_id, code, starts) == (slot.staff_id, "haircut", slot.starts_at)
    cal.create_booking(db, slot, name="Asha Test", phone_e164="+919800000010", idempotency_key="k1")
    assert slot.slot_id not in {s.slot_id for s in _slots(db)}
    with pytest.raises(cal.SlotTaken):
        cal.validate_slot(db, slot.slot_id, now=FIXED_NOW)


def test_invalid_slots_are_refused(db):
    slot = _slots(db)[0]
    with pytest.raises(cal.CalendarError) as off_grid:
        cal.validate_slot(db, cal.format_slot_id(slot.staff_id, "haircut", slot.starts_at + timedelta(minutes=7)),
                          now=FIXED_NOW)
    assert off_grid.value.code == "slot_unavailable"
    with pytest.raises(cal.CalendarError) as garbage:
        cal.validate_slot(db, "slot_1", now=FIXED_NOW)
    assert garbage.value.code == "invalid_slot"
    past = cal.format_slot_id(slot.staff_id, "haircut", FIXED_NOW - timedelta(days=1))
    with pytest.raises(cal.CalendarError) as old:
        cal.validate_slot(db, past, now=FIXED_NOW)
    assert old.value.code == "slot_in_past"


def test_constraint_refuses_overlap_even_without_the_app_check(db):
    """Raw inserts that skip find_free_slots still cannot double-book one staff member."""
    slot = _slots(db)[0]
    first, _ = cal.create_booking(db, slot, name="One", phone_e164="+919800000011", idempotency_key=None)
    db.commit()
    with pytest.raises(IntegrityError) as exc:
        db.add(Booking(customer_id=first.customer_id, staff_id=slot.staff_id, service_id=first.service_id,
                       starts_at=slot.starts_at + timedelta(minutes=15), ends_at=slot.ends_at + timedelta(minutes=15)))
        db.flush()
    assert exc.value.orig.sqlstate == cal.NO_OVERLAP_SQLSTATE
    db.rollback()


def test_half_open_ranges_allow_back_to_back_and_cancelled_frees_the_slot(db):
    slot = _slots(db)[0]
    a, _ = cal.create_booking(db, slot, name="A", phone_e164="+919800000012", idempotency_key=None)
    after = cal.Slot(slot.staff_id, slot.staff_name, "haircut", slot.ends_at, slot.ends_at + timedelta(minutes=30))
    cal.create_booking(db, after, name="B", phone_e164="+919800000013", idempotency_key=None)   # 10:30 right after 10:00-10:30
    with pytest.raises(cal.SlotTaken):
        cal.create_booking(db, slot, name="C", phone_e164="+919800000014", idempotency_key=None)
    cal.cancel_booking(db, a, "test")
    c, created = cal.create_booking(db, slot, name="C", phone_e164="+919800000014", idempotency_key=None)
    assert created and c.status == BookingStatus.confirmed


def test_idempotency_key_returns_the_original(db):
    slot = _slots(db)[0]
    first, created = cal.create_booking(db, slot, name="Idem", phone_e164="+919800000015", idempotency_key="call:slot")
    again, created_again = cal.create_booking(db, slot, name="Idem", phone_e164="+919800000015",
                                              idempotency_key="call:slot")
    assert created and not created_again and again.id == first.id


def test_reschedule_into_a_taken_slot_is_refused_and_rolled_back(db):
    slots = _slots(db, limit=400)
    asha = [s for s in slots if s.staff_name == "Asha"]
    mine, _ = cal.create_booking(db, asha[0], name="Mine", phone_e164="+919800000016", idempotency_key=None)
    cal.create_booking(db, asha[4], name="Theirs", phone_e164="+919800000017", idempotency_key=None)
    with pytest.raises(cal.SlotTaken):
        cal.reschedule_booking(db, mine, asha[4])
    db.refresh(mine)
    assert mine.starts_at == asha[0].starts_at
    # Moving by 15 minutes overlaps only the booking itself, which reschedule excludes.
    shifted = cal.validate_slot(db, cal.format_slot_id(mine.staff_id, "haircut", mine.starts_at + timedelta(minutes=15)),
                                now=FIXED_NOW, exclude_booking=mine.id)
    cal.reschedule_booking(db, mine, shifted)
    assert mine.starts_at == asha[0].starts_at + timedelta(minutes=15)


def test_parallel_bookings_for_one_slot_exactly_one_wins(database):
    """Milestone M2: N callers grab the same slot at the same instant; Postgres lets exactly one through."""
    with SessionLocal() as setup:
        slot = _slots(setup)[0]
    barrier, results, n = threading.Barrier(8), [], 8

    def attempt(i: int) -> None:
        with SessionLocal() as s:
            barrier.wait()
            try:
                cal.create_booking(s, slot, name=f"Caller{i}", phone_e164=f"+9198000001{i:02d}", idempotency_key=None)
                s.commit()
                results.append("ok")
            except cal.SlotTaken:
                s.rollback()
                results.append("taken")

    threads = [threading.Thread(target=attempt, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(results) == ["ok"] + ["taken"] * (n - 1)
    with SessionLocal() as s:
        live = s.scalar(select(func.count(Booking.id)).where(Booking.staff_id == slot.staff_id,
                                                             Booking.starts_at == slot.starts_at,
                                                             Booking.status == BookingStatus.confirmed))
    assert live == 1


def test_no_overlaps_query_is_empty(db):
    """The evaluation's double-booking check, run here against a few legal bookings."""
    for slot in _slots(db, limit=5):
        try:
            cal.create_booking(db, slot, name="X", phone_e164="+919800000099", idempotency_key=None)
        except cal.SlotTaken:
            pass
    overlaps = db.scalar(text("""
        SELECT count(*) FROM bookings a JOIN bookings b ON a.staff_id = b.staff_id AND a.id < b.id
        WHERE a.status <> 'cancelled' AND b.status <> 'cancelled'
          AND tstzrange(a.starts_at, a.ends_at, '[)') && tstzrange(b.starts_at, b.ends_at, '[)')"""))
    assert overlaps == 0
    assert db.scalar(select(func.count(Staff.id))) == 3


def test_local_range_is_local_midnight():
    start, end = cal.local_range(TUE, TUE)
    assert start == datetime(2026, 11, 2, 18, 30, tzinfo=timezone.utc) and end - start == timedelta(days=1)


def test_concurrent_overlapping_inserts_do_not_deadlock(database):
    """Guard for the deadlock the evaluation's random-load phase hit (overlapping inserts, exclusion check).

    With the per-staff advisory lock every loser is a clean exclusion violation. This is a race, so it does not
    reproduce the deadlock deterministically without the lock; the load phase in evaluation/run_eval.py did."""
    with SessionLocal() as setup:
        colour = cal.get_service(setup, "colour")
        base = _slots(setup, code="colour")[0]
    starts = [base.starts_at + timedelta(minutes=15 * i) for i in range(6)]   # 90-min bookings 0-75 min apart: all overlap
    barrier, results, errors, causes = threading.Barrier(len(starts)), [], [], []

    def attempt(i: int, start) -> None:
        slot = cal.Slot(base.staff_id, base.staff_name, "colour", start, start + timedelta(minutes=colour.duration_min))
        with SessionLocal() as s:
            barrier.wait()
            try:
                cal.create_booking(s, slot, name=f"Over{i}", phone_e164=f"+9198000002{i:02d}", idempotency_key=None)
                s.commit()
                results.append("ok")
            except cal.SlotTaken as exc:
                s.rollback()
                results.append("taken")
                causes.append(exc.__cause__.orig.sqlstate)
            except Exception as exc:   # anything else is a failure of this test
                errors.append(repr(exc))

    threads = [threading.Thread(target=attempt, args=(i, st)) for i, st in enumerate(starts)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == [] and results.count("ok") == 1
    assert set(causes) == {cal.NO_OVERLAP_SQLSTATE}
