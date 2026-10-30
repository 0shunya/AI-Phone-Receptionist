# Scenario evaluation: offline-rules-scripted

- Run at 2026-10-30T17:15:27+00:00 (clock fixed at 2026-11-02T09:00:00+05:30)
- Agent brain: `rules`
- Caller: `scripted`
- Scenarios: 33 (33 scored, 0 excluded for quota/infra errors)

| Metric | Value |
|---|---|
| Scenarios passed (all checks) | 33/33 |
| End state correct (DB bookings match the label) | 1.0 |
| Handed off when required | 6/6 |
| Handed off when not required | 0/27 |
| Required tools called successfully | 1.0 |
| Forbidden tool used | 0 |
| Brain tool calls rejected by the router | 0/95  |
| Double bookings in the database | 0 |
| Server turn latency, caller text -> first agent sentence (p50 / p95) | 26 / 61 ms (n=110) |
| Model call latency (p50 / p95) | None / None ms (n=0, errors 0) |

## By category

| Category | Passed |
|---|---|
| angry_caller | 1/1 |
| background_noise | 3/3 |
| cancel | 3/3 |
| changes_mind | 4/4 |
| no_false_handoff | 2/2 |
| no_free_slot | 2/2 |
| question_only | 1/1 |
| question_then_booking | 1/1 |
| race | 1/1 |
| repeated_failure | 1/1 |
| reschedule | 3/3 |
| simple_booking | 7/7 |
| unknown_request | 2/2 |
| wants_human | 2/2 |

## Concurrency (rules brain; tests the database, not the model)

- Same-slot burst: 10 callers said yes to one slot at the same instant -> 1 booked, 9 told it was just taken, 0 overlaps.
- Random load: 60 full booking calls on 8 threads -> 56 confirmed bookings (56 calls ended booked), 44 slot-taken refusals handled, 0 overlaps (server turn p50/p95 110/240 ms under load).

Simulated calls in this run: 104.

## Failures

None.
