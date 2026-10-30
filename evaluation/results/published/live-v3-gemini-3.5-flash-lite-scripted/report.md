# Scenario evaluation: live-v3-gemini-3.5-flash-lite-scripted

- Run at 2026-10-30T17:14:22+00:00 (clock fixed at 2026-11-02T09:00:00+05:30)
- Agent brain: `gemini` (gemini-3.5-flash-lite)
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
| Brain tool calls rejected by the router | 3/99 {'phone_not_heard': 2, 'invalid_args': 1} |
| Double bookings in the database | 0 |
| Server turn latency, caller text -> first agent sentence (p50 / p95) | 1367 / 5716 ms (n=106) |
| Model call latency (p50 / p95) | 1254 / 5655 ms (n=107, errors 1) |

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

Simulated calls in this run: 34.

## Failures

None.
