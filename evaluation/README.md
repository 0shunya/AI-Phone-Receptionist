# Evaluation

```bash
# from the repo root, with `docker compose up -d db` running
backend/.venv/Scripts/python evaluation/build_scenarios.py               # rewrite scenarios.jsonl after editing
backend/.venv/Scripts/python evaluation/run_eval.py --brain rules        # offline, free, deterministic
backend/.venv/Scripts/python evaluation/run_eval.py --brain gemini --pace 4 --only s01,s12   # live, uses quota
backend/.venv/Scripts/python evaluation/run_eval.py --brain gemini --caller llm --caller-model gemini-3.1-flash-lite
```

(`.venv/bin/python` on macOS/Linux.) Runs use the `reception_eval` database (`EVAL_DATABASE_URL`), never the app's.

## The scenarios

33 caller scenarios in `scenarios.jsonl`, written by hand in `build_scenarios.py` before the agent ran them. Each has
a goal (for an LLM caller), scripted lines (for a scripted caller), optional setup (existing bookings, a day blocked
solid), and the expected end state: which bookings exist for which phone, on which day/time/staff, with which status,
whether a handoff must (or must not) happen, and which tools must or must not succeed.

| Category | n | What it tests |
|---|---|---|
| simple_booking | 7 | service, day, time of day, a named staff member, a withheld caller ID |
| question_then_booking / question_only | 2 | price and hours answered without tools |
| no_free_slot | 2 | a fully booked day: offer another day, or let the caller go |
| reschedule | 3 | own booking, a booking under another number, nothing to reschedule |
| cancel | 3 | cancel, cancel then rebook, cancel the second of two |
| changes_mind | 4 | "no, Thursday instead", a corrected name, switching slot, backing out |
| background_noise | 3 | `[static]` markers, a cut-off phone number, filler words |
| angry_caller, wants_human, unknown_request, repeated_failure | 6 | must hand off |
| no_false_handoff | 2 | says "someone" or a staff name; must not hand off |
| race | 1 | two calls interleaved turn by turn want the same slot |

The clock is fixed at Monday 2 November 2026, 09:00 Asia/Kolkata, and every scenario starts from an empty calendar,
so expected times are exact.

## What is measured

All from the database, not from what the agent says:

- **End state**: the bookings for the scenario's phone numbers match the label exactly (count, service, day, and
  time/staff/name/status where labelled).
- **Handoff**: happened if and only if required (and with the labelled reason, where one is given).
- **Tools**: required tools succeeded; forbidden ones did not; how many model tool calls the router rejected.
- **Double bookings**: an overlap query over `bookings` after every scenario.
- **Latency**: server time from caller text to the first agent sentence, p50/p95 (nearest rank). This excludes
  speech recognition and synthesis, which happen in the browser; real calls record `eos_to_audio_ms` too.
- **Concurrency** (rules brain): a burst of N callers saying yes to the same slot at the same instant, and 60 full
  booking calls on 8 threads.

## Honest limits

- `RuleBrain` is a reference policy, and its parsing bugs were fixed against these scenarios (first run 29/33).
  Its 33/33 shows the system around the model works; it says nothing about language understanding.
- Scripted callers cannot adapt. When the live model asks things in a different order (for example the phone number
  before the name), a scripted line can land on the wrong question. An LLM caller fixes that, at twice the quota.
- Live runs are single runs at temperature 0.3 on a free-tier key, so results vary from run to run.
- `published/live-v1-…-sample` used prompt v1 on 7 scenarios; it is kept because it found the stale-slot bug.
