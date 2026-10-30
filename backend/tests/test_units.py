"""Pure unit tests: speech formatting, phone normalisation, the deterministic detectors, RuleBrain parsing."""

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from app.agent.engine import SentenceStream
from app.agent.rules import parse_choice, parse_name, parse_range, parse_service
from app.agent.state import _words, asks_for_human, is_clear_yes, sounds_angry
from app.services.observability import percentile
from app.services.phone import to_e164
from app.services.speech import ordinal, say_when, spell

IST = ZoneInfo("Asia/Kolkata")
NOW = datetime(2026, 11, 2, 9, 0, tzinfo=IST)   # a Monday


@pytest.mark.parametrize("day,expected", [(1, "1st"), (2, "2nd"), (3, "3rd"), (4, "4th"), (11, "11th"),
                                          (12, "12th"), (13, "13th"), (21, "21st"), (22, "22nd"), (31, "31st")])
def test_ordinal(day, expected):
    assert ordinal(day) == expected


def test_say_when_speaks_local_time_and_the_date_aloud():
    utc = datetime(2026, 11, 3, 4, 30, tzinfo=timezone.utc)          # 10:00 IST on Tuesday
    assert say_when(utc, IST, NOW.astimezone(timezone.utc)) == "tomorrow, Tuesday the 3rd at 10 AM"
    later = datetime(2026, 11, 14, 9, 15, tzinfo=timezone.utc)       # 14:45 IST, 12 days out
    assert say_when(later, IST, NOW.astimezone(timezone.utc)) == "Saturday the 14th of November at 2:45 PM"


def test_spell():
    assert spell("Shlok") == "S, H, L, O, K"


@pytest.mark.parametrize("raw,expected", [
    ("+91 98765 43210", "+919876543210"), ("98765-43210", "+919876543210"), ("098765 43210", "+919876543210"),
    ("919876543210", "+919876543210"), ("0044 20 7946 0958", "+442079460958"), ("+1 (415) 555-0100", "+14155550100"),
    ("12345", None), ("call me", None), ("", None), ("+0123456789", None),
])
def test_to_e164(raw, expected):
    assert to_e164(raw, "+91") == expected


@pytest.mark.parametrize("text,yes", [
    ("yes", True), ("Yes please.", True), ("yeah that's right", True), ("correct", True), ("sure, book it", True),
    ("no", False), ("yes but make it 11", False), ("wait, actually Thursday", False), ("hmm maybe", False),
    ("I don't know", False), ("yes no", False),
])
def test_clear_yes(text, yes):
    assert is_clear_yes(text) is yes


@pytest.mark.parametrize("text,human", [
    ("I want to talk to a human", True), ("can I speak to someone please", True), ("transfer me", True),
    ("put me through to the manager", True), ("I need a person", True), ("real person please", True),
    ("I want someone to cut my hair", False), ("book me with someone good", False), ("a haircut please", False),
])
def test_asks_for_human(text, human):
    assert asks_for_human(text) is human


def test_sounds_angry():
    assert sounds_angry("This is ridiculous, I've been waiting forever")
    assert not sounds_angry("I'd like a haircut on Friday")


def test_parse_service_and_range():
    assert parse_service(_words("can I get my beard done")) == "beard_trim"
    assert parse_service(_words("a hair cut please")) == "haircut"
    assert parse_service(_words("colour and highlights")) == "colour"
    assert parse_range(_words("tomorrow afternoon"), NOW) == ("2026-11-03T12:00", "2026-11-03")
    assert parse_range(_words("on friday at 4 pm"), NOW) == ("2026-11-06T16:00", "2026-11-06")
    assert parse_range(_words("monday"), NOW) == ("2026-11-09", "2026-11-09")       # never "today" for a weekday
    assert parse_range(_words("the 14th"), NOW) == ("2026-11-14", "2026-11-14")
    assert parse_range(_words("next week"), NOW) == ("2026-11-09", "2026-11-15")
    assert parse_range(_words("hello there"), NOW) is None


def test_parse_name_ignores_non_names():
    assert parse_name("My name is shlok singh", awaiting=False) == "Shlok Singh"
    assert parse_name("Priya", awaiting=True) == "Priya"
    assert parse_name("I'm looking for a haircut", awaiting=False) is None
    assert parse_name("it's for tomorrow", awaiting=False) is None


def test_parse_choice_uses_spoken_order_staff_and_time():
    session = {"offered_slots": {"slot_1": {"staff": "Asha", "when": "tomorrow at 10 AM"},
                                 "slot_2": {"staff": "Ravi", "when": "tomorrow at 11:30 AM"}},
               "spoken": ["slot_1", "slot_2"]}
    assert parse_choice(_words("the first one"), session) == "slot_1"
    assert parse_choice(_words("the later one"), session) == "slot_2"
    assert parse_choice(_words("with ravi please"), session) == "slot_2"
    assert parse_choice(_words("11:30 am works"), session) == "slot_2"
    assert parse_choice(_words("what"), session) is None


def test_sentence_stream_splits_on_sentence_ends_only():
    said = []
    stream = SentenceStream(said.append, 0.0)
    for delta in ["Sure. I can do 10.", "30 tomorrow", "! Anything else?"]:
        stream.feed(delta)
    stream.flush()
    assert said == ["Sure.", "I can do 10.30 tomorrow!", "Anything else?"]


def test_percentile_is_nearest_rank():
    values = list(range(1, 101))
    assert percentile(values, 50) == 50
    assert percentile(values, 95) == 95
    assert percentile([7], 95) == 7
    assert percentile([], 50) is None
