# Scenario evaluation: live-gemini-3.5-flash-lite-scripted

- Run at 2026-10-30T16:34:25+00:00 (clock fixed at 2026-11-02T09:00:00+05:30)
- Agent brain: `gemini` (gemini-3.5-flash-lite)
- Caller: `scripted`
- Scenarios: 7 (6 scored, 1 excluded for quota/infra errors)

| Metric | Value |
|---|---|
| Scenarios passed (all checks) | 5/6 |
| End state correct (DB bookings match the label) | 0.833 |
| Handed off when required | 2/2 |
| Handed off when not required | 1/4 |
| Required tools called successfully | 1.0 |
| Forbidden tool used | 0 |
| Brain tool calls rejected by the router | 2/16 {'unknown_slot': 2} |
| Double bookings in the database | 0 |
| Server turn latency, caller text -> first agent sentence (p50 / p95) | 1306 / 4170 ms (n=16) |
| Model call latency (p50 / p95) | 1225 / 4129 ms (n=17, errors 1) |

## By category

| Category | Passed |
|---|---|
| cancel | 1/1 |
| changes_mind | 0/1 |
| reschedule | 1/1 |
| simple_booking | 1/1 |
| unknown_request | 1/1 |
| wants_human | 1/1 |

Simulated calls in this run: 7.

## Failures

- **s18** (changes_mind): end_state, handoff, outcome
- **s29** (repeated_failure): ; infra: ['rate_limited']
