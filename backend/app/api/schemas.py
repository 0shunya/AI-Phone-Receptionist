"""Request and response models for the HTTP API."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class IncomingCall(_In):
    caller_phone: str | None = Field(default=None, max_length=20)
    channel: Literal["browser", "text"] = "browser"


class CallerTurn(_In):
    text: str = Field(min_length=1, max_length=500)


class HandoffRequest(_In):
    reason: Literal["caller_request", "angry_caller", "repeated_failure", "unknown_request",
                    "model_unavailable"] = "caller_request"
    summary: str | None = Field(default=None, max_length=300)


class HandoffUpdate(_In):
    status: Literal["claimed", "resolved"]


class BookingCreate(_In):
    slot_id: str = Field(min_length=10, max_length=80)
    name: str = Field(min_length=1, max_length=60, pattern=r"^[A-Za-z][A-Za-z .'\-]*$")
    phone_e164: str = Field(min_length=8, max_length=20)
    call_id: str | None = None


class BookingUpdate(_In):
    action: Literal["reschedule", "cancel"]
    new_slot_id: str | None = Field(default=None, max_length=80)
    reason: str | None = Field(default=None, max_length=200)


class SlotOut(BaseModel):
    slot_id: str
    starts_at: datetime
    ends_at: datetime
    staff: str
    service: str


class BookingOut(BaseModel):
    id: str
    service: str
    service_name: str
    staff: str
    staff_id: str
    customer: str
    phone_e164: str
    starts_at: datetime
    ends_at: datetime
    status: str
    event_id: str | None
    call_id: str | None
    created_at: datetime
