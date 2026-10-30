"""Scenario evaluation: simulated calls through the real engine, tool router and Postgres calendar.

    # offline: rule-based reference brain + scripted caller (free, deterministic)
    backend/.venv/Scripts/python evaluation/run_eval.py --brain rules --publish rules-scripted
    # live sample: Gemini as the agent, an LLM playing the caller from the scenario's goal
    backend/.venv/Scripts/python evaluation/run_eval.py --brain gemini --caller llm --only s01,s12,s26 --publish live

Every scenario starts from an empty calendar (plus its own setup) in a dedicated database (EVAL_DATABASE_URL,
default reception_eval), with the clock fixed at Monday 2 November 2026, 09:00 Asia/Kolkata. Checks use the
database end state, the tool_calls log and the turns log, never the agent's own claims.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
import threading
import time
import uuid
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
os.environ["DATABASE_URL"] = os.environ.get(
    "EVAL_DATABASE_URL", "postgresql+psycopg://reception:reception@localhost:5434/reception_eval")
os.environ.setdefault("DAILY_MODEL_CALL_LIMIT", "1000")

from sqlalchemy import func, select, text  # noqa: E402

from app.agent.engine import Engine  # noqa: E402
from app.agent.state import State  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db import Base, SessionLocal, get_engine, session_scope  # noqa: E402
from app.llm.prompts import PROMPT_VERSION  # noqa: E402
from app.models import Booking, BookingStatus, Call, Customer, ModelCall, Service, Staff, ToolCall, Turn  # noqa: E402
from app.services import calendar as cal  # noqa: E402
from app.services.observability import percentile  # noqa: E402
from scripts.seed import seed_reference  # noqa: E402

FIXED_NOW = datetime(2026, 11, 2, 3, 30, tzinfo=timezone.utc)   # Mon 2 Nov 2026, 09:00 IST
FILLER_PHONE = "+919899999999"
INVALID_CALL_CODES = {"invalid_args", "unknown_slot", "unknown_booking", "tool_not_allowed", "unknown_tool",
                      "invalid_phone", "invalid_date", "invalid_range", "unknown_service", "unknown_staff"}
BUSINESS_REFUSALS = {"slot_taken", "not_confirmed"}
OVERLAP_SQL = text("""
    SELECT count(*) FROM bookings a JOIN bookings b ON a.staff_id = b.staff_id AND a.id < b.id
    WHERE a.status <> 'cancelled' AND b.status <> 'cancelled'
      AND tstzrange(a.starts_at, a.ends_at, '[)') && tstzrange(b.starts_at, b.ends_at, '[)')""")
INFRA_ERRORS = {"quota_exhausted", "daily_limit", "rate_limited", "auth_error", "config_missing", "model_not_found"}


# --- database ------------------------------------------------------------------------------------------------

def create_schema() -> None:
    engine = get_engine()
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)


def reset_db() -> None:
    tables = ", ".join(t.name for t in reversed(Base.metadata.sorted_tables) if t.name != "model_calls")
    with get_engine().begin() as conn:
        conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
    with session_scope() as db:
        seed_reference(db)


def apply_setup(setup: dict) -> None:
    tz = get_settings().tz
    with session_scope() as db:
        for b in setup.get("bookings", []):
            staff_id = db.scalar(select(Staff.id).where(Staff.name == b["staff"]))
            service = cal.get_service(db, b["service"])
            starts = datetime.fromisoformat(f"{b['date']}T{b['time']}").replace(tzinfo=tz).astimezone(timezone.utc)
            slot = cal.Slot(staff_id, b["staff"], service.code, starts, starts + timedelta(minutes=service.duration_min))
            cal.create_booking(db, slot, name=b["name"], phone_e164=b["phone"], idempotency_key=None)
        for day in setup.get("block", []):
            fill = cal.get_service(db, "beard_trim")   # 15 min pieces leave no gap behind
            start, end = cal.local_range(date.fromisoformat(day), date.fromisoformat(day))
            for slot in cal.find_free_slots(db, fill, start, end, now=FIXED_NOW, limit=1000):
                try:
                    cal.create_booking(db, slot, name="Filler", phone_e164=FILLER_PHONE, idempotency_key=None)
                except cal.SlotTaken:
                    pass


def bookings_snapshot(phones: set[str]) -> list[dict]:
    tz = get_settings().tz
    with SessionLocal() as db:
        rows = db.execute(select(Booking, Customer, Service, Staff).join(Customer).join(Service).join(Staff).where(
            Customer.phone_e164.in_(phones)).order_by(Booking.starts_at)).all()
        return [{"phone": c.phone_e164, "name": c.name, "service": s.code, "staff": st.name,
                 "date": b.starts_at.astimezone(tz).date().isoformat(), "time": b.starts_at.astimezone(tz).strftime("%H:%M"),
                 "status": b.status.value} for b, c, s, st in rows]


def overlaps() -> int:
    with SessionLocal() as db:
        return int(db.scalar(OVERLAP_SQL))


# --- callers -------------------------------------------------------------------------------------------------

class ScriptedCaller:
    def __init__(self, turns: list[str], stop_when: str | None = None):
        self.turns = list(turns)
        self.stop_when = stop_when

    def next(self, agent_text: str) -> str | None:
        if self.stop_when and self.stop_when in agent_text:
            return None
        return self.turns.pop(0) if self.turns else None


class LlmCaller:
    """An LLM plays the caller from the scenario's goal (the roadmap's simulated-call setup)."""

    def __init__(self, gateway, scenario: dict, max_turns: int = 8):
        from app.llm.providers import ChatRequest
        self.gateway, self.max_turns, self.turns = gateway, max_turns, 0
        persona = scenario.get("persona") or {}
        facts = [f"Your name is {persona['name']}." if persona.get("name") else "",
                 f"If asked for your phone number, it is {persona['phone']}." if persona.get("phone") else "",
                 "The line is noisy: now and then drop a word or add 'sorry, bad line'." if persona.get("noisy") else ""]
        self.system = ("You are a customer phoning a hair salon. Stay in character and speak one short, natural "
                       "line at a time, like a real phone call. Never describe yourself as an AI. "
                       f"Your goal: {scenario['goal']} {' '.join(f for f in facts if f)} "
                       "When your goal is complete, you have been transferred to a person, or you have given up, "
                       "reply with exactly: BYE")
        self.history: list[tuple[str, str]] = []
        self.ChatRequest = ChatRequest

    def next(self, agent_text: str) -> str | None:
        if self.turns >= self.max_turns:
            return None
        time.sleep(PACE_SECONDS)
        self.history.append(("user", agent_text))     # the agent's words are the "user" input to the caller model
        reply = self.gateway.chat_stream(self.ChatRequest(self.system, self.history, [], temperature=0.7,
                                                          max_output_tokens=120), on_text=lambda _t: None,
                                         feature="caller_simulator")
        line = reply.text.strip().strip('"')
        if not line or line.upper().startswith("BYE"):
            return None
        self.history.append(("model", line))
        self.turns += 1
        return line


# --- running one call ----------------------------------------------------------------------------------------

PACE_SECONDS = 0.0   # live runs: pause between turns to stay under the free tier's requests-per-minute limit


def run_call(engine: Engine, caller_phone: str | None, caller, transcript: list) -> str:
    with session_scope() as db:
        call, greeting = engine.start_call(db, caller_phone=caller_phone, channel="simulation")
        call_id, agent_text = call.id, greeting.text
    transcript.append(("agent", agent_text, None))
    while (line := caller.next(agent_text)) is not None:
        time.sleep(PACE_SECONDS)   # outside every timed region
        with session_scope() as db:
            out = engine.handle_turn(db, call_id, line)
        transcript.append(("caller", line, None))
        transcript.append(("agent", out.agent_text, {"state": out.state, "tools": out.tools,
                                                     "server_first_ms": out.server_first_ms,
                                                     "server_total_ms": out.server_total_ms}))
        agent_text = out.agent_text
        if out.state == State.HANDOFF:
            break
    with session_scope() as db:
        engine.end_call(db, call_id)
    return str(call_id)


def call_facts(call_ids: list[str]) -> dict:
    with SessionLocal() as db:
        calls = [db.get(Call, uuid.UUID(c)) for c in call_ids]
        tools = list(db.scalars(select(ToolCall).where(ToolCall.call_id.in_([c.id for c in calls]))
                                .order_by(ToolCall.created_at)))
        turns = list(db.scalars(select(Turn).where(Turn.call_id.in_([c.id for c in calls]), Turn.seq > 1,
                                                   Turn.speaker == "agent")))
        models = list(db.scalars(select(ModelCall).where(ModelCall.call_id.in_([c.id for c in calls]))))
        return {
            "outcomes": [c.outcome.value for c in calls], "handoff_reasons": [c.handoff_reason for c in calls],
            "summaries": [c.summary for c in calls],
            "tools": [{"tool": t.tool, "ok": t.ok, "error_code": t.error_code, "state": t.state,
                       "actor": (t.result or {}).get("actor"), "args": t.args} for t in tools],
            "server_first_ms": [t.server_first_ms for t in turns if t.server_first_ms is not None],
            "server_total_ms": [t.server_total_ms for t in turns if t.server_total_ms is not None],
            "model_calls": [{"status": m.status, "latency_ms": m.latency_ms, "first_token_ms": m.first_token_ms,
                             "error_category": m.error_category, "input_tokens": m.input_tokens,
                             "output_tokens": m.output_tokens} for m in models],
        }


def _match(expected: list[dict], actual: list[dict]) -> bool:
    if len(expected) != len(actual):
        return False
    remaining = list(actual)
    for want in expected:
        hit = next((a for a in remaining if all(a.get(k) == v for k, v in want.items())), None)
        if hit is None:
            return False
        remaining.remove(hit)
    return True


def run_scenario(scenario: dict, make_engine, make_caller) -> dict:
    reset_db()
    apply_setup(scenario["setup"])
    expect = scenario["expect"]
    phones = {b["phone"] for b in expect["bookings"]} | {b["phone"] for b in scenario["setup"].get("bookings", [])}
    if scenario["caller_phone"]:
        phones.add(scenario["caller_phone"])
    transcripts: list[list] = []
    started = time.perf_counter()
    engine = make_engine()
    if "race" in scenario["setup"]:
        race = scenario["setup"]["race"]
        call_ids, transcript = run_race(engine, race), []
        phones |= {race["a"]["phone"], race["b"]["phone"]}
        transcripts.append(transcript)
    else:
        transcript: list = []
        call_ids = [run_call(engine, scenario["caller_phone"], make_caller(scenario), transcript)]
        transcripts.append(transcript)
    facts = call_facts(call_ids)
    actual = bookings_snapshot(phones)
    infra = sorted({m["error_category"] for m in facts["model_calls"] if m["error_category"] in INFRA_ERRORS})
    handed_off = "handoff" in facts["outcomes"]
    want_handoff = expect["handoff"]
    ok_tools = {t["tool"] for t in facts["tools"] if t["ok"]}
    checks = {
        "end_state": _match(expect["bookings"], actual),
        "handoff": handed_off == bool(want_handoff) and (
            not isinstance(want_handoff, str) or want_handoff in facts["handoff_reasons"]),
        "outcome": expect["outcome"] in facts["outcomes"] if "race" not in scenario["setup"] else True,
        "tools_required": all(t in ok_tools for t in expect.get("tools_required", [])),
        "tools_forbidden": not any(t in ok_tools for t in expect.get("tools_forbidden", [])),
        "no_double_booking": overlaps() == 0,
    }
    agent_lines = [t[1] for t in transcripts[0] if t[0] == "agent"][1:]
    for index, needle in (expect.get("agent_says") or {}).items():
        checks[f"agent_says_{index}"] = int(index) < len(agent_lines) and needle in agent_lines[int(index)]
    return {"id": scenario["id"], "category": scenario["category"], "passed": all(checks.values()) and not infra,
            "infra_error": infra or None, "checks": checks, "expected_bookings": expect["bookings"],
            "actual_bookings": actual, "outcomes": facts["outcomes"], "handoff_reasons": facts["handoff_reasons"],
            "summaries": facts["summaries"], "tools": facts["tools"], "server_first_ms": facts["server_first_ms"],
            "server_total_ms": facts["server_total_ms"], "model_calls": facts["model_calls"],
            "wall_ms": round((time.perf_counter() - started) * 1000),
            "transcript": [{"speaker": s, "text": t, **({"meta": m} if m else {})} for tr in transcripts for s, t, m in tr]}


def run_race(engine: Engine, race: dict) -> list[str]:
    """Two calls interleaved turn by turn, so both are offered (and read back) the same slot before either commits."""
    ids, lines = {}, {k: list(v["turns"]) for k, v in race.items()}
    for key in ("a", "b"):
        with session_scope() as db:
            call, _ = engine.start_call(db, caller_phone=race[key]["phone"], channel="simulation")
            ids[key] = call.id
    while any(lines.values()):
        for key in ("a", "b"):
            if lines[key]:
                with session_scope() as db:
                    engine.handle_turn(db, ids[key], lines[key].pop(0))
    for key in ("a", "b"):
        with session_scope() as db:
            engine.end_call(db, ids[key])
    return [str(ids["a"]), str(ids["b"])]


# --- concurrency phases (rules brain only: they test the database, not the model) ---------------------------

def burst_same_slot(n: int) -> dict:
    """n callers are read back the same slot, then all say yes at the same instant."""
    from app.agent.rules import RuleBrain
    reset_db()
    engine = Engine(RuleBrain(), clock=lambda: FIXED_NOW)
    ids = []
    for i in range(n):
        with session_scope() as db:
            call, _ = engine.start_call(db, caller_phone=f"+9198100{i:05d}", channel="simulation")
            ids.append(call.id)
            engine.handle_turn(db, call.id, f"Haircut tomorrow morning please, I'm Burst{chr(65 + i % 26)}")
            engine.handle_turn(db, call.id, "The first one.")
    barrier, results = threading.Barrier(n), []

    def say_yes(call_id):
        barrier.wait()
        with session_scope() as db:
            out = engine.handle_turn(db, call_id, "Yes.")
        results.append((out.outcome, out.tools[0]["error_code"] if out.tools else None))

    threads = [threading.Thread(target=say_yes, args=(cid,)) for cid in ids]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    counts = Counter(r[0] for r in results)
    return {"calls": n, "booked": counts.get("booked", 0), "slot_taken": sum(1 for r in results if r[1] == "slot_taken"),
            "overlaps": overlaps(), "passed": counts.get("booked", 0) == 1 and overlaps() == 0}


def random_load(n: int, workers: int, seed: int = 7) -> dict:
    """n complete booking calls on overlapping days from a thread pool; the calendar must stay consistent."""
    from app.agent.rules import RuleBrain
    reset_db()
    engine = Engine(RuleBrain(), clock=lambda: FIXED_NOW)
    rng = random.Random(seed)
    plans = [(f"+9198200{i:05d}", rng.choice(["haircut", "beard trim", "colour", "manicure"]),
              rng.choice(["tomorrow", "Wednesday", "Thursday"]), rng.choice(["first", "second"])) for i in range(n)]

    def one(plan):
        phone, service, day, pick = plan
        transcript: list = []
        caller = ScriptedCaller([f"Can I book a {service} {day}? I'm Load{phone[-3:]}.", f"The {pick} one.", "Yes.",
                                 "The first one.", "Yes.", "The first one.", "Yes."], stop_when="You're booked")
        return run_call(engine, phone, caller, transcript)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        call_ids = list(pool.map(one, plans))
    facts = call_facts(call_ids)
    with SessionLocal() as db:
        confirmed = db.scalar(select(func.count(Booking.id)).where(Booking.status == BookingStatus.confirmed))
    taken = sum(1 for t in facts["tools"] if t["error_code"] == "slot_taken")
    return {"calls": n, "workers": workers, "confirmed_bookings": confirmed,
            "booked_calls": facts["outcomes"].count("booked"), "slot_taken_events": taken, "overlaps": overlaps(),
            "server_total_ms": {"p50": percentile(facts["server_total_ms"], 50),
                                "p95": percentile(facts["server_total_ms"], 95)},
            "passed": overlaps() == 0 and confirmed == facts["outcomes"].count("booked")}


# --- aggregation and report ----------------------------------------------------------------------------------

def aggregate(cases: list[dict]) -> dict:
    scored = [c for c in cases if not c["infra_error"]]
    by_cat: dict[str, list[bool]] = defaultdict(list)
    for c in scored:
        by_cat[c["category"]].append(c["passed"])
    tools = [t for c in scored for t in c["tools"] if t["actor"] == "model"]
    invalid = [t for t in tools if t["error_code"] in INVALID_CALL_CODES]
    need = [c for c in scored if c["category"] in ("angry_caller", "wants_human", "unknown_request", "repeated_failure")]
    no_need = [c for c in scored if c not in need]
    first = [ms for c in scored for ms in c["server_first_ms"]]
    total = [ms for c in scored for ms in c["server_total_ms"]]
    models = [m for c in cases for m in c["model_calls"]]
    ok_models = [m for m in models if m["status"] == "success"]
    return {
        "scenarios": len(cases), "scored": len(scored), "infra_errors": len(cases) - len(scored),
        "passed": sum(c["passed"] for c in scored),
        "pass_rate": round(sum(c["passed"] for c in scored) / len(scored), 3) if scored else None,
        "end_state_accuracy": round(sum(c["checks"]["end_state"] for c in scored) / len(scored), 3) if scored else None,
        "handoff_when_required": f"{sum('handoff' in c['outcomes'] for c in need)}/{len(need)}",
        "false_handoffs": f"{sum('handoff' in c['outcomes'] for c in no_need)}/{len(no_need)}",
        "required_tools_called": round(sum(c["checks"]["tools_required"] for c in scored) / len(scored), 3) if scored else None,
        "forbidden_tool_violations": sum(not c["checks"]["tools_forbidden"] for c in scored),
        "model_tool_calls": len(tools), "invalid_model_tool_calls": len(invalid),
        "invalid_by_code": dict(Counter(t["error_code"] for t in invalid)),
        "business_refusals": dict(Counter(t["error_code"] for t in tools if t["error_code"] in BUSINESS_REFUSALS)),
        "double_bookings": sum(not c["checks"]["no_double_booking"] for c in cases),
        "server_first_ms": {"n": len(first), "p50": percentile(first, 50), "p95": percentile(first, 95)},
        "server_total_ms": {"n": len(total), "p50": percentile(total, 50), "p95": percentile(total, 95)},
        "model_calls": {"n": len(models), "errors": len(models) - len(ok_models),
                        "latency_ms": {"p50": percentile([m["latency_ms"] for m in ok_models], 50),
                                       "p95": percentile([m["latency_ms"] for m in ok_models], 95)},
                        "first_token_ms": {"p50": percentile([m["first_token_ms"] for m in ok_models if m["first_token_ms"]], 50),
                                           "p95": percentile([m["first_token_ms"] for m in ok_models if m["first_token_ms"]], 95)},
                        "input_tokens_avg": round(sum(m["input_tokens"] or 0 for m in ok_models) / len(ok_models)) if ok_models else None},
        "by_category": {k: f"{sum(v)}/{len(v)}" for k, v in sorted(by_cat.items())},
    }


def write_report(out_dir: Path, meta: dict, summary: dict, cases: list[dict], concurrency: dict | None) -> None:
    lines = [f"# Scenario evaluation: {meta['label']}", "",
             f"- Run at {meta['run_at']} (clock fixed at {meta['clock']})",
             f"- Agent brain: `{meta['brain']}`" + (f" ({meta['model']})" if meta.get("model") else ""),
             f"- Caller: `{meta['caller']}`" + (f" ({meta['caller_model']})" if meta.get("caller_model") else ""),
             f"- Scenarios: {summary['scenarios']} ({summary['scored']} scored, {summary['infra_errors']} excluded for "
             "quota/infra errors)", "",
             "| Metric | Value |", "|---|---|",
             f"| Scenarios passed (all checks) | {summary['passed']}/{summary['scored']} |",
             f"| End state correct (DB bookings match the label) | {summary['end_state_accuracy']} |",
             f"| Handed off when required | {summary['handoff_when_required']} |",
             f"| Handed off when not required | {summary['false_handoffs']} |",
             f"| Required tools called successfully | {summary['required_tools_called']} |",
             f"| Forbidden tool used | {summary['forbidden_tool_violations']} |",
             f"| Brain tool calls rejected by the router | {summary['invalid_model_tool_calls']}/{summary['model_tool_calls']} "
             f"{summary['invalid_by_code'] or ''} |",
             f"| Double bookings in the database | {summary['double_bookings']} |",
             f"| Server turn latency, caller text -> first agent sentence (p50 / p95) | "
             f"{summary['server_first_ms']['p50']} / {summary['server_first_ms']['p95']} ms (n={summary['server_first_ms']['n']}) |",
             f"| Model call latency (p50 / p95) | {summary['model_calls']['latency_ms']['p50']} / "
             f"{summary['model_calls']['latency_ms']['p95']} ms (n={summary['model_calls']['n']}, errors {summary['model_calls']['errors']}) |",
             "", "## By category", "", "| Category | Passed |", "|---|---|",
             *[f"| {k} | {v} |" for k, v in summary["by_category"].items()], ""]
    if concurrency:
        b, r = concurrency["burst"], concurrency["load"]
        lines += ["## Concurrency (rules brain; tests the database, not the model)", ""]
        if b:
            lines.append(f"- Same-slot burst: {b['calls']} callers said yes to one slot at the same instant -> "
                         f"{b['booked']} booked, {b['slot_taken']} told it was just taken, {b['overlaps']} overlaps.")
        if r:
            lines.append(f"- Random load: {r['calls']} full booking calls on {r['workers']} threads -> "
                         f"{r['confirmed_bookings']} confirmed bookings ({r['booked_calls']} calls ended booked), "
                         f"{r['slot_taken_events']} slot-taken refusals handled, {r['overlaps']} overlaps (server turn "
                         f"p50/p95 {r['server_total_ms']['p50']}/{r['server_total_ms']['p95']} ms under load).")
        lines.append("")
    lines += [f"Simulated calls in this run: {summary['simulated_calls_total']}.", ""]
    failed = [c for c in cases if not c["passed"]]
    lines += ["## Failures", ""] + ([f"- **{c['id']}** ({c['category']}): " + ", ".join(
        k for k, v in c["checks"].items() if not v) + (f"; infra: {c['infra_error']}" if c["infra_error"] else "")
        for c in failed] or ["None."]) + [""]
    (out_dir / "report.md").write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--brain", choices=["rules", "gemini"], default="rules")
    parser.add_argument("--model", default=None, help="agent model (default GEMINI_MODEL)")
    parser.add_argument("--caller", choices=["scripted", "llm"], default="scripted")
    parser.add_argument("--caller-model", default="gemma-4-26b-a4b-it")
    parser.add_argument("--only", default="", help="comma-separated scenario ids")
    parser.add_argument("--burst", type=int, default=10, help="callers in the same-slot burst (0 to skip)")
    parser.add_argument("--load", type=int, default=60, help="calls in the random-load phase (0 to skip)")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--publish", default="", help="also copy results to results/published/<name>")
    parser.add_argument("--pace", type=float, default=0.0, help="seconds to wait before each turn (live runs)")
    parser.add_argument("--stop-on-quota", action="store_true", default=True)
    args = parser.parse_args()
    global PACE_SECONDS
    PACE_SECONDS = args.pace

    scenarios = [json.loads(line) for line in (ROOT / "evaluation" / "scenarios.jsonl").read_text("utf-8").splitlines() if line]
    if args.only:
        wanted = args.only.split(",")
        scenarios = [s for s in scenarios if s["id"] in wanted]
    create_schema()

    from app.agent.rules import RuleBrain
    from app.services.observability import record_model_call
    settings = get_settings()
    agent_model = args.model or settings.gemini_model
    gateway_cache = {}

    def gateway(model: str):
        if model not in gateway_cache:
            from app.llm.gateway import ModelGateway
            from app.llm.providers import GeminiProvider
            provider = GeminiProvider(settings.gemini_api_key, model, timeout_seconds=settings.model_timeout_seconds,
                                      thinking_level=settings.gemini_thinking_level)
            gateway_cache[model] = ModelGateway(provider, record_model_call, max_attempts=settings.model_max_attempts)
        return gateway_cache[model]

    def make_engine():
        if args.brain == "rules":
            return Engine(RuleBrain(), clock=lambda: FIXED_NOW)
        from app.agent.gemini_brain import GeminiBrain
        return Engine(GeminiBrain(gateway(agent_model)), clock=lambda: FIXED_NOW)

    def make_caller(scenario):
        if args.caller == "scripted":
            return ScriptedCaller(scenario["turns"])
        return LlmCaller(gateway(args.caller_model), scenario)

    cases = []
    for scenario in scenarios:
        if scenario["category"] == "race" and args.caller == "llm":
            continue   # the race is a scripted interleaving by construction
        case = run_scenario(scenario, make_engine, make_caller)
        cases.append(case)
        status = "PASS" if case["passed"] else ("INFRA" if case["infra_error"] else "FAIL")
        print(f"{scenario['id']:>4} {scenario['category']:<22} {status:<5} "
              + ", ".join(k for k, v in case["checks"].items() if not v), flush=True)
        if case["infra_error"] and args.stop_on_quota and set(case["infra_error"]) & {"quota_exhausted", "daily_limit"}:
            print("stopping: model quota exhausted", flush=True)
            break
    concurrency = None
    if args.brain == "rules" and (args.burst or args.load):
        concurrency = {"burst": burst_same_slot(args.burst) if args.burst else None,
                       "load": random_load(args.load, args.workers) if args.load else None}
        print("concurrency:", json.dumps(concurrency), flush=True)
    summary = aggregate(cases)
    simulated = sum(len(c["outcomes"]) for c in cases) + sum(
        phase["calls"] for phase in (concurrency or {}).values() if phase)
    summary["simulated_calls_total"] = simulated
    label = args.publish or f"{args.brain}-{args.caller}"
    meta = {"label": label, "run_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "clock": FIXED_NOW.astimezone(settings.tz).isoformat(), "brain": args.brain,
            "model": agent_model if args.brain == "gemini" else None, "caller": args.caller,
            "caller_model": args.caller_model if args.caller == "llm" else None,
            "prompt_version": PROMPT_VERSION}
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    targets = [ROOT / "evaluation" / "results" / "runs" / f"{stamp}-{label}"]
    if args.publish:
        targets.append(ROOT / "evaluation" / "results" / "published" / args.publish)
    for out in targets:
        out.mkdir(parents=True, exist_ok=True)
        (out / "results.json").write_text(json.dumps({"meta": meta, "summary": summary, "concurrency": concurrency},
                                                     indent=2), encoding="utf-8")
        (out / "cases.jsonl").write_text("\n".join(json.dumps(c, default=str) for c in cases) + "\n", encoding="utf-8")
        write_report(out, meta, summary, cases, concurrency)
    print(json.dumps(summary, indent=2))
    print(f"wrote {targets[-1]}")


if __name__ == "__main__":
    main()
