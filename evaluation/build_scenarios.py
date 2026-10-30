"""Writes scenarios.jsonl: the labelled caller scenarios. Edit here, then re-run this file.

The clock for every scenario is Monday 2 November 2026, 09:00 Asia/Kolkata. Staff shifts (from the seed):
Asha Tue-Sun 10-19, Ravi Mon-Sat 10-18, Meera Wed-Sun 11-19. Expected end states were written by hand from the
caller's words and these shifts, not by running the agent.
"""

import json
from pathlib import Path

P = "+919876543210"           # default caller ID
S = []


def add(id, category, goal, turns, expect, caller_phone=P, setup=None, persona=None):
    S.append({"id": id, "category": category, "goal": goal, "persona": persona or {}, "caller_phone": caller_phone,
              "setup": setup or {}, "turns": turns, "expect": expect})


def booked(service, date, time=None, phone=P, name=None, staff=None, status="confirmed"):
    row = {"phone": phone, "service": service, "date": date, "status": status}
    for key, value in (("time", time), ("name", name), ("staff", staff)):
        if value:
            row[key] = value
    return row


# --- simple booking ---------------------------------------------------------------------------------------
add("s01", "simple_booking", "Book a haircut tomorrow morning, take the first time offered. Your name is Shlok.",
    ["Hi, this is Shlok. Can I book a haircut tomorrow morning?", "The first one, please.", "Yes, that's right."],
    {"outcome": "booked", "handoff": False, "bookings": [booked("haircut", "2026-11-03", "10:00", name="Shlok")],
     "tools_required": ["find_slots", "book"]}, persona={"name": "Shlok"})
add("s02", "simple_booking", "Book a beard trim on Friday afternoon, take the second option. Your name is Arjun.",
    ["I'd like a beard trim on Friday afternoon.", "The second one.", "It's Arjun.", "Yes please."],
    {"outcome": "booked", "handoff": False, "bookings": [booked("beard_trim", "2026-11-06", name="Arjun")],
     "tools_required": ["find_slots", "book"]}, persona={"name": "Arjun"})
add("s03", "simple_booking", "Book a colour on Saturday at 2 pm. Your name is Kavya.",
    ["Do you have anything for colour on Saturday at 2 pm? My name is Kavya.", "The first one works.", "Yes."],
    {"outcome": "booked", "handoff": False, "bookings": [booked("colour", "2026-11-07", "14:00", name="Kavya")],
     "tools_required": ["find_slots", "book"]}, persona={"name": "Kavya"})
add("s04", "simple_booking", "Book a manicure on Wednesday. Your caller ID is hidden; your number is 98100 22334. "
    "Your name is Neha.",
    ["Hello, I want a manicure on Wednesday.", "The first one.", "Neha.", "My number is 98100 22334.", "Yes, correct."],
    {"outcome": "booked", "handoff": False,
     "bookings": [booked("manicure", "2026-11-04", phone="+919810022334", name="Neha")],
     "tools_required": ["find_slots", "book"]}, caller_phone=None, persona={"name": "Neha", "phone": "98100 22334"})
add("s05", "simple_booking", "Book a haircut on Thursday specifically with Ravi. Your name is Rohan.",
    ["Can I get a haircut on Thursday with Ravi? I'm Rohan.", "The first one.", "Yes."],
    {"outcome": "booked", "handoff": False,
     "bookings": [booked("haircut", "2026-11-05", "10:00", name="Rohan", staff="Ravi")],
     "tools_required": ["find_slots", "book"]}, persona={"name": "Rohan"})
add("s06", "simple_booking", "Book a haircut tomorrow at 10:30. Your name is Dev.",
    ["Could I book a haircut tomorrow at 10:30? Name's Dev.", "Yes, the first one.", "Yes."],
    {"outcome": "booked", "handoff": False, "bookings": [booked("haircut", "2026-11-03", "10:30", name="Dev")],
     "tools_required": ["find_slots", "book"]}, persona={"name": "Dev"})
add("s07", "simple_booking", "Book a haircut on the 10th of November. Your name is Isha.",
    ["I'd like a haircut on the 10th, my name is Isha.", "First one please.", "Yes."],
    {"outcome": "booked", "handoff": False, "bookings": [booked("haircut", "2026-11-10", name="Isha")],
     "tools_required": ["find_slots", "book"]}, persona={"name": "Isha"})
add("s08", "question_then_booking", "Ask the price of a haircut, then book one tomorrow afternoon. Your name is Sam.",
    ["How much is a haircut?", "OK, can I book one for tomorrow afternoon? This is Sam.", "The first one.", "Yes."],
    {"outcome": "booked", "handoff": False, "bookings": [booked("haircut", "2026-11-03", "12:00", name="Sam")],
     "tools_required": ["find_slots", "book"], "agent_says": {"0": "600"}}, persona={"name": "Sam"})
add("s09", "question_only", "Ask what the opening hours are, then say goodbye. Do not book anything.",
    ["What are your opening hours?", "Great, thanks, bye."],
    {"outcome": "answered", "handoff": False, "bookings": [], "tools_forbidden": ["book", "cancel", "reschedule"],
     "agent_says": {"0": "10 AM"}})

# --- no free slot -----------------------------------------------------------------------------------------
add("s10", "no_free_slot", "Try to book a haircut tomorrow; if there is nothing, accept Wednesday's first time. "
    "Your name is Tara.",
    ["I need a haircut tomorrow, I'm Tara.", "OK, how about Wednesday?", "The first one.", "Yes."],
    {"outcome": "booked", "handoff": False, "bookings": [booked("haircut", "2026-11-04", name="Tara")],
     "tools_required": ["find_slots", "book"]}, setup={"block": ["2026-11-03"]}, persona={"name": "Tara"})
add("s11", "no_free_slot", "You need a manicure tomorrow and only tomorrow. If nothing is free, give up politely.",
    ["Can I get a manicure tomorrow?", "No, it has to be tomorrow. Never mind, bye."],
    {"outcome": "answered", "handoff": False, "bookings": [], "tools_forbidden": ["book"]},
    setup={"block": ["2026-11-03"]})

# --- reschedule -------------------------------------------------------------------------------------------
EXISTING = {"phone": P, "name": "Shlok", "service": "haircut", "date": "2026-11-03", "time": "10:00", "staff": "Asha"}
add("s12", "reschedule", "Move your haircut booked for tomorrow to Thursday afternoon, take the first time offered.",
    ["Hi, I need to reschedule my haircut.", "Can we move it to Thursday afternoon?", "The first one.", "Yes."],
    {"outcome": "rescheduled", "handoff": False, "bookings": [booked("haircut", "2026-11-05", "12:00", name="Shlok")],
     "tools_required": ["find_bookings", "find_slots", "reschedule"]}, setup={"bookings": [EXISTING]},
    persona={"name": "Shlok"})
add("s13", "reschedule", "Move your appointment, which is under 98111 11111 (not the number you're calling from), "
    "to Friday morning.",
    ["I want to move my appointment, it's under 98111 11111.", "Friday morning please.", "The first one.", "Yes."],
    {"outcome": "rescheduled", "handoff": False,
     "bookings": [booked("haircut", "2026-11-06", "10:00", phone="+919811111111")],
     "tools_required": ["find_bookings", "find_slots", "reschedule"]}, caller_phone="+919822222222",
    setup={"bookings": [{"phone": "+919811111111", "name": "Meena", "service": "haircut", "date": "2026-11-03",
                         "time": "11:00", "staff": "Asha"}]}, persona={"name": "Meena", "phone": "98111 11111"})
add("s14", "reschedule", "Try to reschedule; when told there is no booking, book a new haircut tomorrow instead. "
    "Your name is Leo.",
    ["I'd like to reschedule my booking.", "Oh, then can I book a haircut tomorrow? This is Leo.", "The first one.",
     "Yes."],
    {"outcome": "booked", "handoff": False, "bookings": [booked("haircut", "2026-11-03", "10:00", name="Leo")],
     "tools_required": ["find_bookings", "find_slots", "book"]}, persona={"name": "Leo"})

# --- cancel -----------------------------------------------------------------------------------------------
add("s15", "cancel", "Cancel your haircut tomorrow.",
    ["Please cancel my appointment tomorrow.", "Yes, cancel it.", "Yes."],
    {"outcome": "cancelled", "handoff": False,
     "bookings": [booked("haircut", "2026-11-03", "10:00", status="cancelled")],
     "tools_required": ["find_bookings", "cancel"]}, setup={"bookings": [EXISTING]})
add("s16", "cancel", "Cancel your haircut tomorrow, then book a beard trim on Saturday instead.",
    ["I need to cancel my haircut tomorrow.", "Cancel it please.", "Yes.",
     "Actually, can I book a beard trim on Saturday instead?", "The first one.", "Yes."],
    {"outcome": "booked", "handoff": False,
     "bookings": [booked("haircut", "2026-11-03", "10:00", status="cancelled"), booked("beard_trim", "2026-11-07")],
     "tools_required": ["find_bookings", "cancel", "find_slots", "book"]}, setup={"bookings": [EXISTING]},
    persona={"name": "Shlok"})
add("s17", "cancel", "You have two bookings (Tuesday and Thursday). Cancel only the Thursday one.",
    ["I want to cancel one of my bookings.", "The second one, cancel it.", "Yes."],
    {"outcome": "cancelled", "handoff": False,
     "bookings": [booked("haircut", "2026-11-03", "10:00"),
                  booked("beard_trim", "2026-11-05", "15:00", status="cancelled")],
     "tools_required": ["find_bookings", "cancel"]},
    setup={"bookings": [EXISTING, {"phone": P, "name": "Shlok", "service": "beard_trim", "date": "2026-11-05",
                                   "time": "15:00", "staff": "Ravi"}]})

# --- caller changes their mind ----------------------------------------------------------------------------
add("s18", "changes_mind", "Start booking a haircut tomorrow, but when read back say no and ask for Thursday "
    "instead; take Thursday's first time. Your name is Zara.",
    ["Hi, I'm Zara, I'd like a haircut tomorrow.", "The first one.", "Actually no, can we do Thursday instead?",
     "The first one.", "Yes."],
    {"outcome": "booked", "handoff": False, "bookings": [booked("haircut", "2026-11-05", "10:00", name="Zara")],
     "tools_required": ["find_slots", "book"]}, persona={"name": "Zara"})
add("s19", "changes_mind", "Book a manicure on Wednesday; at the read-back correct your name: it is Jon, not John.",
    ["Book a manicure on Wednesday please, my name is John.", "The first one.", "No, my name is Jon, J O N.", "Yes."],
    {"outcome": "booked", "handoff": False, "bookings": [booked("manicure", "2026-11-04", name="Jon")],
     "tools_required": ["find_slots", "book"]}, persona={"name": "Jon"})
add("s20", "changes_mind", "Book a haircut on Friday; at the read-back switch to the second time offered. "
    "Your name is Mia.",
    ["Can I book a haircut on Friday? I'm Mia.", "The first one.", "Hmm, no, the second one instead.", "Yes."],
    {"outcome": "booked", "handoff": False, "bookings": [booked("haircut", "2026-11-06", "11:00", name="Mia")],
     "tools_required": ["find_slots", "book"]}, persona={"name": "Mia"})
add("s21", "changes_mind", "Start booking a colour for Saturday, then decide not to book after all.",
    ["I'd like a colour on Saturday, I'm Riya.", "The first one.", "No, actually, forget it. I'll call back. Bye."],
    {"outcome": "answered", "handoff": False, "bookings": []}, persona={"name": "Riya"})

# --- background noise and messy speech ---------------------------------------------------------------------
add("s22", "background_noise", "Book a haircut tomorrow morning over a noisy line. Your name is Ken.",
    ["[static] hi... uh [noise] can I get a hair cut tomorrow [static] morning? this is Ken", "[noise] the first one",
     "yes [static] that's right"],
    {"outcome": "booked", "handoff": False, "bookings": [booked("haircut", "2026-11-03", "10:00", name="Ken")],
     "tools_required": ["find_slots", "book"]}, persona={"name": "Ken", "noisy": True})
add("s23", "background_noise", "Book a beard trim on Thursday. Your caller ID is hidden. The first time you give "
    "your number it comes out cut off (98765), then you repeat it fully: 98765 12345. Your name is Raj.",
    ["I want a beard trim on Thursday, name is Raj.", "The first one.", "My number is 98765.",
     "Sorry, it's 98765 12345.", "Yes."],
    {"outcome": "booked", "handoff": False,
     "bookings": [booked("beard_trim", "2026-11-05", phone="+919876512345", name="Raj")],
     "tools_required": ["find_slots", "book"]}, caller_phone=None, persona={"name": "Raj", "phone": "98765 12345"})
add("s24", "background_noise", "Hesitantly book a manicure on Saturday. Your name is Lily.",
    ["um so yeah i was wondering if like i could maybe get my nails done on saturday", "the first one i guess",
     "it's Lily", "yeah"],
    {"outcome": "booked", "handoff": False, "bookings": [booked("manicure", "2026-11-07", name="Lily")],
     "tools_required": ["find_slots", "book"]}, persona={"name": "Lily"})

# --- handoff required -------------------------------------------------------------------------------------
add("s25", "angry_caller", "You are angry that nobody picked up earlier. Demand to speak to someone.",
    ["This is ridiculous, I've been trying to call all morning!"],
    {"outcome": "handoff", "handoff": True, "bookings": [], "tools_forbidden": ["book"]})
add("s26", "wants_human", "Ask straight away to speak to a real person.",
    ["Can I speak to a real person please?"],
    {"outcome": "handoff", "handoff": "caller_request", "bookings": [], "tools_forbidden": ["book"]})
add("s27", "wants_human", "Start booking a haircut tomorrow, then ask to be put through to the manager.",
    ["I'd like a haircut tomorrow.", "Actually just put me through to the manager."],
    {"outcome": "handoff", "handoff": "caller_request", "bookings": [], "tools_forbidden": ["book"]})
add("s28", "unknown_request", "Ask for a refund for your last colour, which you say went badly.",
    ["I want a refund for my last colour, it came out really patchy."],
    {"outcome": "handoff", "handoff": True, "bookings": [], "tools_forbidden": ["book", "cancel"]})
add("s29", "repeated_failure", "Say two things that make no sense for a salon call.",
    ["blah blah purple monkey dishwasher", "zzz eeee ooo"],
    {"outcome": "handoff", "handoff": True, "bookings": []})
add("s30", "unknown_request", "Ask whether the salon is hiring stylists.",
    ["Hi, are you hiring stylists at the moment?"],
    {"outcome": "handoff", "handoff": True, "bookings": []})

# --- must NOT hand off ------------------------------------------------------------------------------------
add("s31", "no_false_handoff", "Book a haircut tomorrow; you happen to say 'someone'. Your name is Ana.",
    ["I want someone to cut my hair tomorrow, I'm Ana.", "The first one.", "Yes."],
    {"outcome": "booked", "handoff": False, "bookings": [booked("haircut", "2026-11-03", "10:00", name="Ana")],
     "tools_required": ["find_slots", "book"]}, persona={"name": "Ana"})
add("s32", "no_false_handoff", "Ask whether Meera can do a beard trim on Sunday, then book it. Your name is Vik.",
    ["Can Meera do a beard trim on Sunday? This is Vik.", "Great, the first one.", "Yes please."],
    {"outcome": "booked", "handoff": False,
     "bookings": [booked("beard_trim", "2026-11-08", "11:00", name="Vik", staff="Meera")],
     "tools_required": ["find_slots", "book"]}, persona={"name": "Vik"})

# --- race: two callers want the same slot -----------------------------------------------------------------
add("s33", "race", "Two callers ask for the same first haircut slot tomorrow and both say yes; the second must be "
    "told it was just taken and offered the next time.",
    [], {"outcome": "booked", "handoff": False, "bookings": [
        booked("haircut", "2026-11-03", "10:00", phone="+919811000001", name="Asha Two"),
        booked("haircut", "2026-11-03", phone="+919811000002", name="Bela")]},
    setup={"race": {"a": {"phone": "+919811000001", "turns": ["Haircut tomorrow morning please, I'm Asha Two.",
                                                              "The first one.", "Yes."]},
                    "b": {"phone": "+919811000002", "turns": ["Haircut tomorrow morning please, I'm Bela.",
                                                              "The first one.", "Yes.", "The first one.", "Yes."]}}})

if __name__ == "__main__":
    out = Path(__file__).with_name("scenarios.jsonl")
    out.write_text("\n".join(json.dumps(s) for s in S) + "\n", encoding="utf-8")
    print(f"wrote {len(S)} scenarios to {out.name}")
