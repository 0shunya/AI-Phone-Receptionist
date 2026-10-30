"""Speakable text: dates said aloud in the caller's local time, phone digits read back, name spelling.

Pitfalls from the roadmap: store UTC, speak local time, say the date aloud ("Tuesday the 14th"), read digits back.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo


def ordinal(day: int) -> str:
    suffix = "th" if 11 <= day % 100 <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th")
    return f"{day}{suffix}"


def say_time(local: datetime) -> str:
    hour = local.hour % 12 or 12
    meridiem = "AM" if local.hour < 12 else "PM"
    return f"{hour} {meridiem}" if local.minute == 0 else f"{hour}:{local.minute:02d} {meridiem}"


def say_day(local: datetime, now_local: datetime) -> str:
    delta = (local.date() - now_local.date()).days
    named = f"{local.strftime('%A')} the {ordinal(local.day)}"
    if delta == 0:
        return f"today, {named},"
    if delta == 1:
        return f"tomorrow, {named},"
    if delta > 6:
        return f"{named} of {local.strftime('%B')}"
    return named


def say_when(starts_at_utc: datetime, tz: ZoneInfo, now_utc: datetime) -> str:
    local, now_local = starts_at_utc.astimezone(tz), now_utc.astimezone(tz)
    return f"{say_day(local, now_local)} at {say_time(local)}".replace(", at", " at")


def say_phone_tail(phone_e164: str) -> str:
    return " ".join(phone_e164[-4:])


def spell(name: str) -> str:
    return ", ".join(ch.upper() for ch in name if ch.isalpha())
