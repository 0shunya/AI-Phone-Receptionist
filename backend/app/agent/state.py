"""The state machine around the model. Your code owns the state; the model owns the words.

GREET -> INTENT -> COLLECT -> CHECK_SLOTS -> CONFIRM -> BOOKED -> WRAP_UP, with HANDOFF reachable from any
state. Reschedule and cancel reuse the same path (a booking is looked up in COLLECT first). Each state allows
only its own tools; ``transfer_to_human`` is the one tool always allowed.

The deterministic detectors below are guard rails that do not depend on the model: an explicit request for a
person always hands off, and only a clear yes can commit a read-back.
"""

from __future__ import annotations

import re
from enum import StrEnum


class State(StrEnum):
    GREET = "GREET"
    INTENT = "INTENT"
    COLLECT = "COLLECT"
    CHECK_SLOTS = "CHECK_SLOTS"
    CONFIRM = "CONFIRM"
    BOOKED = "BOOKED"
    WRAP_UP = "WRAP_UP"
    HANDOFF = "HANDOFF"


ALWAYS = "transfer_to_human"
_LOOKUP = ["find_slots", "find_bookings"]

ALLOWED_TOOLS: dict[State, list[str]] = {
    State.GREET: [ALWAYS],
    State.INTENT: [*_LOOKUP, "cancel", ALWAYS],
    State.COLLECT: [*_LOOKUP, "cancel", ALWAYS],
    State.CHECK_SLOTS: [*_LOOKUP, "book", "reschedule", ALWAYS],
    State.CONFIRM: [*_LOOKUP, "book", "reschedule", "cancel", ALWAYS],
    State.BOOKED: [*_LOOKUP, ALWAYS],
    State.WRAP_UP: [*_LOOKUP, ALWAYS],
    State.HANDOFF: [],
}

# Tools whose first call is a read-back and whose second identical call (after a clear yes) commits.
COMMIT_TOOLS = {"book", "reschedule", "cancel"}

HANDOFF_REASONS = ["caller_request", "angry_caller", "repeated_failure", "unknown_request", "model_unavailable"]


def _words(text: str) -> str:
    return " " + re.sub(r"[^a-z0-9' ]+", " ", text.lower()) + " "


_YES = re.compile(r" (yes|yeah|yep|yup|ya|correct|right|sure|confirm|confirmed|perfect|ok|okay|absolutely|"
                  r"definitely|exactly|great|fine|go ahead|please do|do it|book it|sounds good|that works|"
                  r"that's it|that is it|that's right|that is right|haan|ha) ")
_HEDGE = re.compile(r" (no|nope|not|don't|dont|wait|actually|change|wrong|but|hold on|instead|different|"
                    r"another|other|hmm|maybe|cancel|isn't|wasn't) ")


def is_clear_yes(text: str) -> bool:
    """True only for an unhedged agreement. "Yes but make it 11" is not a clear yes."""
    words = _words(text)
    return bool(_YES.search(words)) and not _HEDGE.search(words) and len(words.split()) <= 12


_HUMAN_ANYWHERE = re.compile(r" (human|real person|actual person|live agent|representative|operator|transfer me) ")
_HUMAN_ASK = re.compile(r" ((talk|speak) (to|with)|connect me (to|with)|transfer me( to)?|put me through( to)?|get me|"
                        r"(want|need) (a|an|the|your)) (a |an |the |your )?"
                        r"(someone|somebody|person|agent|manager|receptionist|staff|people) ")
_ANGER = re.compile(r" (ridiculous|useless|furious|angry|pathetic|stupid|idiot|terrible|horrible|worst|"
                    r"fed up|sick of|damn|hell|shut up|wtf|nonsense) ")


def asks_for_human(text: str) -> bool:
    words = _words(text)
    return bool(_HUMAN_ANYWHERE.search(words) or _HUMAN_ASK.search(words))


def sounds_angry(text: str) -> bool:
    return bool(_ANGER.search(_words(text)))
