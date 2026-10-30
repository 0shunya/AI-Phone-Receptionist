# Interview questions

Answers tied to the code in this repository. Numbers come from `evaluation/results/published/`.

## The three from the roadmap

### Where do the milliseconds go in one turn?

Per turn, in order (browser call):

1. **End of speech detection** in the browser: Chrome's recogniser decides the caller is done and emits a final
   result. Not measured separately; it is inside `eos_to_audio_ms`.
2. **Socket + engine overhead**: lock the call row, insert the caller turn, build the prompt
   (`backend/app/agent/engine.py`). Offline, with the rule brain and no model, the whole server turn is
   p50 26 ms / p95 61 ms (`published/offline-rules-scripted`), which is the floor for everything except the model.
3. **The model call**: the dominant cost. Live, `gemini-3.5-flash-lite` with `thinking_level=minimal`: model call p50 1254 ms / p95 5655 ms, and caller text → first agent sentence p50 1367 ms / p95 5716 ms (`live-v3`, n=106 turns).
   The p95 is driven by occasional multi-second responses on the free tier.
4. **Tool execution**: `find_slots` and `book` are single-digit to tens of milliseconds (each `tool_calls` row stores
   `latency_ms`).
5. **TTS start**: the first sentence is spoken as soon as it exists (`SentenceStream` in `engine.py`), not when the
   whole reply is done.

What I did about it: one model call per turn (tool results are spoken directly, no second model round-trip),
streaming with sentence-by-sentence emit, the greeting is code (no model call to answer), minimal thinking, short
prompts and short tool results, and a fixed 20 s timeout with a single retry that is never attempted after words were
already spoken. What I would do next: an end-of-turn model that is faster than browser endpointing, a filler line if
the model has not produced text within ~700 ms, and a speech-to-speech model (Gemini Live) to remove the STT/TTS hops.

The roadmap's metric is *end of caller speech → first agent audio*. Browser calls record it in
`turns.eos_to_audio_ms`; simulated calls cannot (no audio), so the evaluation reports the server-side part
(`server_first_ms`) and labels it as such.

### What happens when the caller interrupts the agent?

`frontend/src/lib/useCall.ts`: while utterances are queued, an interim recognition result of two or more words calls
`interrupt()`, which runs `speechSynthesis.cancel()` immediately (that is where the audio is), marks the agent line
interrupted, and sends `{"type":"barge_in"}`. `backend/app/api/voice.py` sets a flag so the rest of that turn's
sentences are not sent, and marks the agent turn `interrupted = true` (if the turn is still being generated, it is
marked as soon as it is stored). The caller's words become the next turn as usual. The agent's own voice leaking into
the mic is filtered by `isEcho` (mostly the same words as the sentence being spoken); headphones are still advised.
With telephony, the same `barge_in` would send Twilio's `clear` message to flush buffered audio.

Tested: `test_voice_stream_sentences_metrics_barge_in_and_hangup` in `backend/tests/test_api.py` drives the socket and
checks `interrupted` and `eos_to_audio_ms` in the database; `isEcho` has Vitest cases. Barge-in with a real microphone
was not tested in an automated way.

### How do you make double booking impossible?

The database refuses it, not the prompt and not the availability check:
`EXCLUDE USING gist (staff_id WITH =, tstzrange(starts_at, ends_at, '[)') WITH &&) WHERE (status <> 'cancelled')`
(`backend/migrations/versions/0001_initial_schema.py`, mirrored in `backend/app/models.py`). Half-open ranges let
10:00–10:30 sit next to 10:30–11:00. The app catches the violation inside a savepoint (`create_booking` in
`backend/app/services/calendar.py`), and the agent says "that time was just taken" and offers the next real slots.

Evidence: 8 threads booking one slot → exactly one wins (`test_parallel_bookings_for_one_slot_exactly_one_wins`);
a raw insert that skips the app still fails; the evaluation's burst (10 callers say yes at the same instant → 1
booked, 9 told it was taken) and load phase (60 calls on 8 threads → 56 bookings, 44 refusals handled, 0 overlaps);
an overlap query after every scenario. Total simulated calls in the offline run: 104, with 0 double bookings.

The load phase also found a real problem: concurrent inserts of *overlapping* rows deadlocked inside the exclusion
check (`DeadlockDetected … while checking exclusion constraint`). Each transaction waited on the other's uncommitted
row. Fix: `lock_staff_calendar` takes a per-staff `pg_advisory_xact_lock` before writing, so the second writer waits
and gets a clean `23P01`. The constraint is still the guarantee; the lock just makes failures clean and fast
(a deadlock costs `deadlock_timeout`, 1 s by default).

## Tool calling and state

**Why a state machine around the model?** The model is good at words and bad at invariants. The code decides which
step the call is in (`calls.state`), which tools exist in that step (`ALLOWED_TOOLS` in `agent/state.py`), and
when a commit may happen. The model cannot book from INTENT because `book` is neither declared to it nor accepted by
the router there (`test_router_enforces_state_tools_and_offered_aliases`, `test_gemini_disallowed_tool_is_rejected_by_the_router`).

**How do you stop the model inventing slots?** It never sees a slot it could invent: `find_slots` returns aliases
`slot_1…slot_6`, and `book` only accepts an alias from this call's latest search. Even a real slot id it was not
offered is refused (`unknown_slot`). The slot is then re-validated against shifts and live bookings, and finally the
constraint decides.

**How does "read it back before you commit" work?** Two-phase tools (`_two_phase` in `agent/tools.py`). The first
`book` call validates and speaks the read-back (service, spoken date, local time, staff, name spelled out, last four
digits of the phone) and moves to CONFIRM. Only an identical second call, after a deterministic clear yes
(`is_clear_yes`: "yes but at 11" is not one), commits.

**A bug the live evaluation found in this.** Prompt v1 showed the pending read-back to the model with its raw slot
id. In s18 ("no, can we do Thursday instead?") the model searched Thursday, then called `book` with Tuesday's raw id
from the stale pending entry. The router refused it twice (it was no longer offered), so nothing wrong was booked,
but the call failed into a handoff. Fix: a new search voids the pending action, and the facts block shows only
aliases (`render_facts`, prompt v2). Regression test: `test_new_search_voids_the_pending_read_back_and_facts_show_aliases`.

**Another one.** With caller ID withheld (s23), the model filled in a phone number nobody had said (the example
number in the tool schema). The read-back exposed it, but the right fix is in code: `_heard_phone` only accepts the
caller ID or digits that appear in the caller's own words (`phone_not_heard`). The schema no longer carries a
plausible example number, and prompt v3 also tells the model to ask. Regression test:
`test_book_refuses_a_phone_number_nobody_said`.

**Why one tool per turn and speak its result directly?** Latency and quota: it halves model calls on tool turns.
The cost is that the model does not phrase tool results itself; I accept that because the results are templated,
correct (times spoken from the database), and short.

**How do Build 2's schemas show up here?** The roadmap says "Build 3 turns Build 2's schemas into tool arguments".
Build 2 validated model output with Pydantic and a JSON schema; here the same idea guards tool arguments: strict
Pydantic models with `extra="forbid"`, enums from the database, E.164 phones, alias ids
(`ARG_MODELS` and `tool_schemas` in `agent/tools.py`), and a router that re-validates everything the model sends.

## Handoff

**When does it hand off?** The caller asks for a person (deterministic `asks_for_human`, before the model runs),
anger (`sounds_angry`), two consecutive failed turns, the model judges the request out of scope
(`transfer_to_human` with `unknown_request`), or the model is unavailable (quota, auth, rate limit): the caller is
never left in silence. Evaluation: handed off in 6/6 scenarios that required it and 0/27 that did not, offline and in
the v2 live run; s31 ("I want *someone* to cut my hair") and s32 (a staff member's name) exist to catch false
positives.

**A handoff bug the evaluation could not see.** Driving the UI showed that a guard-triggered handoff ("can I talk to a
person") set the state and queued the call but never *spoke* the transfer line: the caller heard silence. The
evaluation passed because it only read the database. Fix: `hand_off` in `engine.py` speaks the transfer, and the
evaluation now has a `handoff_spoken` check (`live-v3-handoff-recheck`: 6/6).

**What does the human get?** A two-line summary built from session state, not generated (`summarize` in
`agent/engine.py`), plus the transcript and tool log; the Handoffs page has claim/resolve. On a real line,
`transfer_to_human` would also ask the telephony provider to move the call.

## Evaluation

**How do you know it works?** 33 hand-labelled scenarios, checked against the database end state. Offline (rules
brain, scripted caller): 33/33, 0 double bookings over 104 simulated calls; this measures the system, not language
understanding, and the rule policy was fixed against these scenarios (first run 29/33). Live (Gemini
3.5 Flash-Lite, scripted caller): prompt v2 31/33, prompt v3 33/33 in one run (34 calls, 0 double bookings, 3/99 tool calls refused by the router, 2 of them the new phone guard catching an invented number). One run is one sample; I would repeat it to report variance before claiming a rate.

**Why not trust the rules number?** It is not a model; it was tuned on the test set. It proves the state machine,
tools, constraint and handoff rules behave, and it costs nothing so it runs in CI nightly. The model's quality is
only measured by the live runs, which are single runs on a free tier and vary.

**Mocked vs live?** Unit and API tests use `ScriptedProvider` or the rule brain and never touch the network
(`GEMINI_API_KEY` is blanked in `tests/conftest.py`). Live runs are explicit (`--brain gemini`) and publish to
folders whose names say so.

## Trade-offs I would defend

- **Browser speech instead of telephony.** No paid account, testable anywhere; costs: no real phone number (M4 not
  built), browser-dependent STT quality, no μ-law/8 kHz handling. `DEPLOYMENT.md` lists what M4 would add.
- **Built-in calendar instead of Google Calendar.** The constraint is the truth either way; an external calendar
  would be a mirror behind `CalendarSync`, and adding it means handling sync failures and external edits.
- **The model call runs inside the turn's database transaction** (the call row is locked `FOR UPDATE` for 1–2 s).
  That serialises turns per call, which is what I want, and calls never share that lock. At high volume I would move
  the model call outside the transaction.
- **In-memory rate limits.** One instance only; Redis if it ever scales out.
