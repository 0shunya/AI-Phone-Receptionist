"""Seed the salon: services, staff and weekly shifts. Idempotent; safe to run on every deploy.

    python -m scripts.seed               # reference data only
    python -m scripts.seed --demo        # plus a few bookings over the next days, so the calendar is not empty
"""

from __future__ import annotations

import argparse
import uuid
from datetime import datetime, time, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import session_scope
from app.models import Service, Staff, StaffShift
from app.services import calendar as cal

SERVICES = [
    ("haircut", "Haircut", 30, 60000),
    ("beard_trim", "Beard trim", 15, 30000),
    ("colour", "Colour", 90, 250000),
    ("manicure", "Manicure", 45, 80000),
]
# (name, weekdays (0=Mon), start, end) in the business timezone
STAFF = [
    ("Asha", [1, 2, 3, 4, 5, 6], time(10), time(19)),
    ("Ravi", [0, 1, 2, 3, 4, 5], time(10), time(18)),
    ("Meera", [2, 3, 4, 5, 6], time(11), time(19)),
]
DEMO_CUSTOMERS = [("Priya", "+919800000001"), ("Arjun", "+919800000002"), ("Kavya", "+919800000003")]


def seed_reference(db: Session) -> None:
    tz_name = get_settings().business_timezone
    for code, name, minutes, price in SERVICES:
        service = db.scalar(select(Service).where(Service.code == code))
        if service is None:
            db.add(Service(code=code, name=name, duration_min=minutes, price_minor=price))
        else:
            service.name, service.duration_min, service.price_minor = name, minutes, price
    for name, days, start, end in STAFF:
        member = db.scalar(select(Staff).where(Staff.name == name))
        if member is None:
            member = Staff(id=uuid.uuid5(uuid.NAMESPACE_DNS, f"staff.{name.lower()}.larkspur"), name=name,
                           calendar_id=f"local:{name.lower()}", timezone=tz_name)
            db.add(member)
            db.flush()
        existing = {shift.weekday for shift in db.scalars(select(StaffShift).where(StaffShift.staff_id == member.id))}
        for day in days:
            if day not in existing:
                db.add(StaffShift(staff_id=member.id, weekday=day, start_local=start, end_local=end))
    db.flush()


def seed_demo_bookings(db: Session, now: datetime | None = None) -> int:
    now = now or datetime.now(timezone.utc)
    haircut = cal.get_service(db, "haircut")
    made = 0
    for offset, (name, phone) in enumerate(DEMO_CUSTOMERS, start=1):
        start, end = cal.local_range((now + timedelta(days=offset)).date(), (now + timedelta(days=offset)).date())
        slots = cal.find_free_slots(db, haircut, start + timedelta(hours=offset), end, now=now, limit=1)
        if slots:
            _, created = cal.create_booking(db, slots[0], name=name, phone_e164=phone,
                                            idempotency_key=f"demo:{phone}:{slots[0].starts_at:%Y%m%d}")
            made += int(created)
    return made


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--demo", action="store_true", help="also create a few demo bookings")
    args = parser.parse_args()
    with session_scope() as db:
        seed_reference(db)
        made = seed_demo_bookings(db) if args.demo else 0
    print(f"Seeded {len(SERVICES)} services, {len(STAFF)} staff" + (f", {made} demo bookings" if args.demo else ""))


if __name__ == "__main__":
    main()
