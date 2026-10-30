"""RuleBrain: a deterministic, keyword-and-regex reference policy with the same interface as the model.

It exists so the scenario evaluation can exercise the *system* (state machine, tool router, Postgres calendar,
two-phase confirm, handoff rules) thousands of times for free and reproducibly. It is not a language model and
its scores say nothing about language understanding; the live-model run measures that separately.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import date, datetime, timedelta

from app.agent.brain import Decision, TurnContext
from app.agent.state import _words, is_clear_yes
from app.services.phone import to_e164

WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
          "november", "december"]
SERVICE_WORDS = [("beard_trim", r"beard"), ("colour", r"colou?r|dye|highlights"), ("manicure", r"manicure|nails"),
                 ("haircut", r"hair ?cut|cut my hair|trim|hair")]
NAME_STOP = {"looking", "calling", "wondering", "not", "sure", "fine", "good", "free", "hoping", "trying", "a", "the",
             "here", "ok", "okay", "just", "so", "very", "available", "going", "busy", "after", "interested", "ready",
             "back", "sorry", "afraid", "still", "also", "really", "booked", "yes", "no", "that", "it", "for",
             "tomorrow", "today", "next", "this", "morning", "afternoon", "evening", "at", "on", "in", "my", "me",
             "great", "perfect", "correct", "right", "haircut", "hair", "beard", "colour", "color", "manicure"}
UNKNOWN = re.compile(r" (refund|complaint|complain|job|hiring|invoice|lost|damage|damaged|insurance|lawyer|"
                     r"gift card|voucher|wholesale|partnership) ")
QUESTION = re.compile(r" (how much|price|prices|cost|costs|charge|open|opening|close|closing|hours|timings|"
                      r"what services|which services) ")
BYE = re.compile(r" (no thanks|no thank you|that's all|that is all|that's it|nothing else|bye|goodbye|no) ")


def _clean(text: str) -> str:
    return re.sub(r"\[[^\]]*\]", " ", text)   # "[static]", "[noise]" markers in noisy-line scenarios


def parse_service(words: str) -> str | None:
    for code, pattern in SERVICE_WORDS:
        if re.search(rf" ({pattern})", words):
            return code
    return None


def parse_range(words: str, now: datetime) -> tuple[str, str] | None:
    """A local (date_from, date_to) pair for find_slots, or None if no day was mentioned."""
    today = now.date()
    day: date | None = None
    span = 0
    if " day after tomorrow " in words:
        day = today + timedelta(days=2)
    elif " tomorrow " in words:
        day = today + timedelta(days=1)
    elif " today " in words:
        day = today
    elif " next week " in words:
        day, span = today + timedelta(days=7 - today.weekday()), 6
    elif " this week " in words:
        day, span = today, 6 - today.weekday()
    if day is None:
        for index, name in enumerate(WEEKDAYS):
            if re.search(rf" {name}s? | {name[:3]} ", words):
                ahead = (index - today.weekday()) % 7 or 7
                day = today + timedelta(days=ahead)
                break
    if day is None:
        match = re.search(r" (?:the )?(\d{1,2})(?:st|nd|rd|th) | (" + "|".join(MONTHS) + r") (\d{1,2}) ", words)
        if match:
            number = int(match.group(1) or match.group(3))
            month = MONTHS.index(match.group(2)) + 1 if match.group(2) else today.month
            year = today.year
            try:
                day = date(year, month, number)
                if day < today:
                    day = date(year + (month == 12), month % 12 + 1, number) if not match.group(2) else date(
                        year + 1, month, number)
            except ValueError:
                day = None
    if day is None:
        return None
    start = "00:00"
    if " afternoon " in words:
        start = "12:00"
    elif " evening " in words:
        start = "17:00"
    time_match = re.search(r" (?:at|after|around|from|by) (\d{1,2})(?::(\d{2}))? ?(am|pm|a m|p m)? ", words)
    if time_match:
        hour, minute = int(time_match.group(1)), int(time_match.group(2) or 0)
        meridiem = (time_match.group(3) or "").replace(" ", "")
        if meridiem == "pm" and hour < 12 or (not meridiem and 1 <= hour <= 7):
            hour += 12
        if 0 <= hour <= 23:
            start = f"{hour:02d}:{minute:02d}"
    end = day + timedelta(days=span)
    return (day.isoformat() if start == "00:00" else f"{day.isoformat()}T{start}", end.isoformat())


def parse_name(raw: str, awaiting: bool) -> str | None:
    match = re.search(r"(?:my name is|my name's|name is|this is|it's|it is|i am|i'm|call me|under|for)\s+"
                      r"([A-Za-z][A-Za-z'\-]+(?:\s+[A-Za-z][A-Za-z'\-]+)?)", raw, re.I)
    candidate = match.group(1) if match else (raw.strip(" .!?,") if awaiting else None)
    if not candidate:
        return None
    parts = [p for p in re.split(r"\s+", candidate) if p]
    if not parts or len(parts) > 2 or parts[0].lower() in NAME_STOP or not all(re.fullmatch(r"[A-Za-z'\-]+", p) for p in parts):
        return None
    parts = [p for p in parts if p.lower() not in NAME_STOP and p.lower() not in WEEKDAYS]
    return " ".join(p.capitalize() for p in parts) or None


def parse_phone(raw: str, words: str, caller_phone: str | None) -> str | None:
    if caller_phone and re.search(r" (this|same|my) number | calling from | this one ", words):
        return caller_phone
    digits = re.sub(r"[^\d+]", "", raw)
    return to_e164(digits) if len(re.sub(r"\D", "", digits)) >= 10 else None


def parse_choice(words: str, session: dict) -> str | None:
    offered: dict = session.get("offered_slots") or {}
    spoken: list = session.get("spoken") or list(offered)[:2]
    if not offered:
        return None
    if re.search(r" (second|later|latter|other one|2nd|the last one|number two) ", words) and len(spoken) > 1:
        return spoken[1]
    if re.search(r" (first|earlier|earliest|former|1st|number one|either|any|that works|sounds good) ", words):
        return spoken[0]
    for alias, item in offered.items():
        if f" {item['staff'].lower()} " in words:
            return alias
    for alias, item in offered.items():
        clock = _words(item["when"].split(" at ")[-1])
        if clock.strip() and clock in words:
            return alias
    if is_clear_yes(words) and len(spoken) == 1:
        return spoken[0]
    return None


class RuleBrain:
    name = "rules-v1"

    def decide(self, ctx: TurnContext, on_text: Callable[[str], None]) -> Decision:
        decision = self._decide(ctx)
        if decision.text:
            on_text(decision.text)
        return decision

    def _decide(self, ctx: TurnContext) -> Decision:
        raw = _clean(ctx.caller_text)
        words = _words(raw)
        session = ctx.session
        notes = dict(session.get("brain_notes") or {})
        awaiting = notes.pop("awaiting", None)

        def ask(text: str, what: str | None = None, understood: bool = True) -> Decision:
            if what:
                notes["awaiting"] = what
            return Decision(text=text, understood=understood, notes=notes)

        def call(tool: str, **args) -> Decision:
            return Decision(tool=tool, args=args, notes=notes)

        if UNKNOWN.search(words):
            return call("transfer_to_human", reason="unknown_request",
                        summary=f"Caller asked about something the agent cannot handle.\nThey said: {raw.strip()[:150]}")

        if " cancel " in words or " cancelling " in words or " call off " in words:
            notes["intent"] = "cancel"
        elif re.search(r" (reschedule|move|change|push|shift|postpone|rearrange|different time|different day) ", words) \
                and ctx.state not in ("CONFIRM", "CHECK_SLOTS"):
            notes["intent"] = "reschedule"
        elif re.search(r" (book|booking|appointment|slot|available|availability|come in|get a|need a|want a|"
                       r"like a|haircut|trim|colour|color|manicure) ", words) and not notes.get("intent"):
            notes["intent"] = "book"
        service = parse_service(words)
        if service:
            notes["service"] = service
            notes.setdefault("intent", "book")
        new_range = parse_range(words, ctx.now_local)
        if new_range:
            notes["range"] = new_range
        name = parse_name(raw, awaiting == "name")
        if name:
            notes["name"] = name
        spoken_phone = parse_phone(raw, words, ctx.caller_phone)
        if spoken_phone:
            notes["phone"] = spoken_phone
        phone = notes.get("phone") or ctx.caller_phone
        intent = notes.get("intent")

        if QUESTION.search(words) and not new_range and intent in (None, "book") and not session.get("offered_slots"):
            return ask(self._answer(words, ctx))

        if ctx.state == "CONFIRM" and session.get("pending"):
            pending = session["pending"]
            if is_clear_yes(raw):
                return call(pending["tool"], **pending["args"])
            choice = parse_choice(words, session) if not new_range else None
            if new_range:
                return self._search(ctx, notes, call, ask)
            if pending["tool"] == "book" and (choice or name):
                return call("book", slot_id=choice or pending["args"]["slot_id"],
                            name=name or pending["args"]["name"], phone_e164=pending["args"]["phone_e164"])
            if pending["tool"] == "reschedule" and choice:
                return call("reschedule", booking_id=pending["args"]["booking_id"], new_slot_id=choice)
            return ask("No problem. What would you like to change?")

        if intent == "cancel":
            if not session.get("bookings"):
                return call("find_bookings", phone_e164=phone) if phone else ask(
                    "What phone number is the booking under?", "phone")
            return call("cancel", booking_id=self._booking_choice(words, session), reason="caller request")

        if intent == "reschedule":
            if not session.get("bookings"):
                return call("find_bookings", phone_e164=phone) if phone else ask(
                    "What phone number is the booking under?", "phone")
            if session.get("offered_slots") and not new_range:
                choice = parse_choice(words, session)
                if choice:
                    return call("reschedule", booking_id=self._booking_choice(words, session), new_slot_id=choice)
                return ask("Which of those times would you like?", understood=False)
            if notes.get("range"):
                booking = session["bookings"].get(self._booking_choice(words, session)) or {}
                notes["service"] = booking.get("service", notes.get("service"))
                return self._search(ctx, notes, call, ask)
            return ask("What day would you like to move it to?", "date")

        if intent == "book":
            if ctx.state in ("CHECK_SLOTS",) and session.get("offered_slots") and not new_range:
                choice = parse_choice(words, session) or notes.get("choice")
                if not choice:
                    return ask("Which of those times would suit you?", understood=bool(name or spoken_phone))
                notes["choice"] = choice
                if not notes.get("name"):
                    return ask("Great. Can I take your name, please?", "name")
                if not phone:
                    return ask("And what's the best number for the confirmation text?", "phone")
                return call("book", slot_id=choice, name=notes["name"], phone_e164=phone)
            if not notes.get("service"):
                return ask("Sure. Which service would you like: a haircut, beard trim, colour or manicure?", "service")
            if not notes.get("range"):
                return ask("What day would suit you?", "date")
            return self._search(ctx, notes, call, ask)

        if ctx.state in ("BOOKED", "WRAP_UP") and BYE.search(words):
            return ask(f"Thanks for calling {ctx.business_name}. Goodbye!")
        return ask("Sorry, I didn't catch that. Would you like to book, reschedule or cancel an appointment?",
                   understood=False)

    @staticmethod
    def _search(ctx: TurnContext, notes: dict, call, ask) -> Decision:
        if not notes.get("service"):
            return ask("Which service is it for?", "service")
        notes.pop("choice", None)
        date_from, date_to = notes["range"]
        return call("find_slots", service=notes["service"], date_from=date_from, date_to=date_to)

    @staticmethod
    def _booking_choice(words: str, session: dict) -> str:
        bookings = list(session.get("bookings") or {})
        if len(bookings) > 1 and re.search(r" (second|2nd|later|other) ", words):
            return bookings[1]
        return bookings[0] if bookings else "booking_1"

    @staticmethod
    def _answer(words: str, ctx: TurnContext) -> str:
        if re.search(r" (open|opening|close|closing|hours|timings) ", words):
            return f"{ctx.hours_text} Would you like to book something?"
        service = parse_service(words)
        chosen = [s for s in ctx.services if s["code"] == service] or ctx.services
        listing = ", ".join(f"a {s['name'].lower()} is {s['price']} and takes {s['duration_min']} minutes"
                            for s in chosen)
        return f"Sure: {listing}. Would you like to book?"
