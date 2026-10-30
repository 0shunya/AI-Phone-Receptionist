"""Tables from the roadmap's Build 03 data model, plus the shared ``model_calls`` log.

Additions beyond the roadmap's columns are deliberate and small: ``services.code`` (tools take ids, not free text),
``staff_shifts`` (working hours for the built-in calendar), ``bookings.idempotency_key``, the call's state-machine
snapshot on ``calls``, and an ``sms_messages`` outbox because this build sends no real SMS.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime, time

from sqlalchemy import (Boolean, DateTime, Enum, ForeignKey, Index, Integer, SmallInteger, String, Text, Time,
                        UniqueConstraint, event, func, text)
from sqlalchemy.dialects.postgresql import ExcludeConstraint, JSONB, TSTZRANGE, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def _uuid() -> uuid.UUID:
    return uuid.uuid4()


def _enum(cls: type[enum.Enum], name: str) -> Enum:
    return Enum(cls, name=name, values_callable=lambda members: [m.value for m in members])


class BookingStatus(str, enum.Enum):
    confirmed = "confirmed"
    cancelled = "cancelled"


class CallOutcome(str, enum.Enum):
    in_progress = "in_progress"
    booked = "booked"
    rescheduled = "rescheduled"
    cancelled = "cancelled"
    answered = "answered"        # a question answered, nothing changed
    handoff = "handoff"
    abandoned = "abandoned"      # caller hung up before anything was done


class Speaker(str, enum.Enum):
    caller = "caller"
    agent = "agent"


class HandoffStatus(str, enum.Enum):
    waiting = "waiting"
    claimed = "claimed"
    resolved = "resolved"


class Service(Base):
    __tablename__ = "services"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    code: Mapped[str] = mapped_column(String(40), unique=True)
    name: Mapped[str] = mapped_column(Text)
    duration_min: Mapped[int] = mapped_column(Integer)
    price_minor: Mapped[int] = mapped_column(Integer)


class Staff(Base):
    __tablename__ = "staff"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(Text)
    calendar_id: Mapped[str] = mapped_column(Text)
    timezone: Mapped[str] = mapped_column(Text)
    shifts: Mapped[list[StaffShift]] = relationship(back_populates="staff", order_by="StaffShift.weekday")


class StaffShift(Base):
    """Weekly working hours in the staff member's local time. weekday: 0 = Monday."""
    __tablename__ = "staff_shifts"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    staff_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("staff.id", ondelete="CASCADE"), index=True)
    weekday: Mapped[int] = mapped_column(SmallInteger)
    start_local: Mapped[time] = mapped_column(Time)
    end_local: Mapped[time] = mapped_column(Time)
    staff: Mapped[Staff] = relationship(back_populates="shifts")


class Customer(Base):
    __tablename__ = "customers"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(Text)
    phone_e164: Mapped[str] = mapped_column(String(16), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Booking(Base):
    __tablename__ = "bookings"
    __table_args__ = (
        # THE CALENDAR IS THE TRUTH: two live bookings for one staff member can never overlap.
        # Half-open '[)' ranges let 10:00-11:00 sit next to 11:00-12:00. Needs the btree_gist extension.
        ExcludeConstraint(
            ("staff_id", "="),
            (func.tstzrange(text("starts_at"), text("ends_at"), text("'[)'")), "&&"),
            name="bookings_no_overlap",
            using="gist",
            where=text("status <> 'cancelled'"),
        ),
        Index("ix_bookings_staff_starts", "staff_id", "starts_at"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    customer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("customers.id"), index=True)
    staff_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("staff.id"))
    service_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("services.id"))
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[BookingStatus] = mapped_column(_enum(BookingStatus, "booking_status"),
                                                  default=BookingStatus.confirmed)
    event_id: Mapped[str | None] = mapped_column(Text)
    idempotency_key: Mapped[str | None] = mapped_column(String(200), unique=True)
    call_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("calls.id"), index=True)
    cancel_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(),
                                                 onupdate=func.now())

    customer: Mapped[Customer] = relationship()
    staff: Mapped[Staff] = relationship()
    service: Mapped[Service] = relationship()


class Call(Base):
    __tablename__ = "calls"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    customer_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("customers.id"), index=True)
    provider_call_id: Mapped[str | None] = mapped_column(Text)
    channel: Mapped[str] = mapped_column(String(20), default="browser")    # browser | text | simulation
    caller_phone: Mapped[str | None] = mapped_column(String(16))             # caller ID, if any
    outcome: Mapped[CallOutcome] = mapped_column(_enum(CallOutcome, "call_outcome"), default=CallOutcome.in_progress)
    summary: Mapped[str | None] = mapped_column(Text)
    recording_key: Mapped[str | None] = mapped_column(Text)                  # always null: no audio is recorded
    state: Mapped[str] = mapped_column(String(20), default="GREET")
    session: Mapped[dict] = mapped_column(JSONB, default=dict)               # the state machine's memory
    handoff_reason: Mapped[str | None] = mapped_column(Text)
    handoff_status: Mapped[HandoffStatus | None] = mapped_column(_enum(HandoffStatus, "handoff_status"))
    brain: Mapped[str | None] = mapped_column(String(60))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    turns: Mapped[list[Turn]] = relationship(back_populates="call", order_by="Turn.seq")
    tool_calls: Mapped[list[ToolCall]] = relationship(order_by="ToolCall.created_at")


class Turn(Base):
    __tablename__ = "turns"
    __table_args__ = (UniqueConstraint("call_id", "seq", name="uq_turns_call_seq"),)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    call_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("calls.id", ondelete="CASCADE"), index=True)
    seq: Mapped[int] = mapped_column(Integer)
    speaker: Mapped[Speaker] = mapped_column(_enum(Speaker, "speaker"))
    text: Mapped[str] = mapped_column(Text)
    state: Mapped[str | None] = mapped_column(String(20))
    # End of caller speech -> first agent audio, as measured by the browser (null for text and simulated turns).
    eos_to_audio_ms: Mapped[int | None] = mapped_column(Integer)
    # Caller text received -> first agent sentence ready, measured on the server.
    server_first_ms: Mapped[int | None] = mapped_column(Integer)
    server_total_ms: Mapped[int | None] = mapped_column(Integer)
    interrupted: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    call: Mapped[Call] = relationship(back_populates="turns")


class ToolCall(Base):
    __tablename__ = "tool_calls"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    call_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("calls.id", ondelete="CASCADE"), index=True)
    turn_seq: Mapped[int | None] = mapped_column(Integer)
    tool: Mapped[str] = mapped_column(String(40))
    args: Mapped[dict] = mapped_column(JSONB)
    ok: Mapped[bool] = mapped_column(Boolean)
    error_code: Mapped[str | None] = mapped_column(String(40))
    result: Mapped[dict | None] = mapped_column(JSONB)
    state: Mapped[str | None] = mapped_column(String(20))
    latency_ms: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SmsMessage(Base):
    """Outbox for confirmation texts. Delivery is simulated: no SMS provider is connected in this build."""
    __tablename__ = "sms_messages"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    to_e164: Mapped[str] = mapped_column(String(16))
    body: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(String(20))          # booked | rescheduled | cancelled
    status: Mapped[str] = mapped_column(String(20), default="simulated")
    booking_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("bookings.id"), index=True)
    call_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("calls.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ModelCall(Base):
    """One row per model attempt (the shared table from the roadmap's foundation page)."""
    __tablename__ = "model_calls"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    trace_id: Mapped[str] = mapped_column(String(64), index=True)
    call_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    build: Mapped[str] = mapped_column(String(20), default="build03")
    feature: Mapped[str] = mapped_column(String(40))
    provider: Mapped[str] = mapped_column(String(20))
    model: Mapped[str] = mapped_column(String(80))
    prompt_version: Mapped[str] = mapped_column(String(40))
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    cost_usd: Mapped[float | None] = mapped_column()
    latency_ms: Mapped[int] = mapped_column(Integer)
    first_token_ms: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20))
    error_category: Mapped[str | None] = mapped_column(String(40))
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)


@event.listens_for(Base.metadata, "before_create")
def _extensions(_target, connection, **_kw) -> None:
    # create_all() (tests) needs the same extension the migration installs.
    connection.execute(text("CREATE EXTENSION IF NOT EXISTS btree_gist"))
