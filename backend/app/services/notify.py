"""Confirmation texts. This build has no SMS provider: messages go to an outbox table shown in the UI.

``SmsSender`` is the seam where Twilio/Exotel would plug in; the outbox row is written either way so a
reviewer can see exactly what the caller would have received.
"""

from __future__ import annotations

import uuid
from typing import Protocol

from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Booking, SmsMessage
from app.services.speech import say_time


class SmsSender(Protocol):
    def send(self, to_e164: str, body: str) -> str: ...


class OutboxOnly:
    """Records the message and reports it as simulated. Nothing leaves the server."""

    def send(self, to_e164: str, body: str) -> str:
        return "simulated"


sms_sender: SmsSender = OutboxOnly()


def booking_text(booking: Booking, kind: str) -> str:
    settings = get_settings()
    local = booking.starts_at.astimezone(settings.tz)
    when = f"{local:%a %d %b} at {say_time(local)}"
    ref = str(booking.id)[:8].upper()
    if kind == "cancelled":
        return f"{settings.business_name}: your {booking.service.name} on {when} is cancelled. Ref {ref}."
    verb = "moved to" if kind == "rescheduled" else "booked for"
    return (f"{settings.business_name}: your {booking.service.name} with {booking.staff.name} is {verb} {when}. "
            f"Ref {ref}. Reply or call to change it.")


def send_booking_sms(db: Session, booking: Booking, kind: str, call_id: uuid.UUID | None = None) -> SmsMessage:
    db.refresh(booking, ["service", "staff", "customer"])
    to, body = booking.customer.phone_e164, booking_text(booking, kind)
    message = SmsMessage(to_e164=to, body=body, kind=kind, status=sms_sender.send(to, body),
                         booking_id=booking.id, call_id=call_id)
    db.add(message)
    db.flush()
    return message
