"""E.164 phone numbers. Every phone that reaches the database has passed ``to_e164``."""

from __future__ import annotations

import re

E164 = re.compile(r"^\+[1-9]\d{7,14}$")


def to_e164(raw: str, default_country_code: str = "+91") -> str | None:
    """Normalise spoken or typed digits to E.164, or return None if they cannot be a phone number.

    Accepts "+91 98765 43210", "098765 43210" (national trunk prefix) and "9876543210" (assumes the default
    country). Anything else (too short, letters, a bare extension) is rejected rather than guessed.
    """
    if not raw:
        return None
    cleaned = re.sub(r"[\s().\-]", "", raw.strip())
    if cleaned.startswith("00"):
        cleaned = "+" + cleaned[2:]
    if not cleaned.startswith("+"):
        if not cleaned.isdigit():
            return None
        if cleaned.startswith("0"):
            cleaned = cleaned.lstrip("0")
        country = default_country_code.lstrip("+")
        if len(cleaned) == 10:
            cleaned = f"+{country}{cleaned}"
        elif cleaned.startswith(country) and len(cleaned) == len(country) + 10:
            cleaned = "+" + cleaned
        else:
            return None
    return cleaned if E164.match(cleaned) else None
