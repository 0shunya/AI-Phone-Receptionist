# Scenario evaluation: live-v3-handoff-recheck

- Run at 2026-10-30T17:16:04+00:00 (clock fixed at 2026-11-02T09:00:00+05:30)
- Agent brain: `gemini` (gemini-3.5-flash-lite)
- Caller: `scripted`
- Scenarios: 6 (6 scored, 0 excluded for quota/infra errors)

| Metric | Value |
|---|---|
| Scenarios passed (all checks) | 6/6 |
| End state correct (DB bookings match the label) | 1.0 |
| Handed off when required | 6/6 |
| Handed off when not required | 0/0 |
| Required tools called successfully | 1.0 |
| Forbidden tool used | 0 |
| Brain tool calls rejected by the router | 0/4  |
| Double bookings in the database | 0 |
| Server turn latency, caller text -> first agent sentence (p50 / p95) | 750 / 2983 ms (n=7) |
| Model call latency (p50 / p95) | 1165 / 2946 ms (n=4, errors 0) |

## By category

| Category | Passed |
|---|---|
| angry_caller | 1/1 |
| repeated_failure | 1/1 |
| unknown_request | 2/2 |
| wants_human | 2/2 |

Simulated calls in this run: 6.

## Failures

None.
