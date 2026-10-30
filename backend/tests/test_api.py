"""HTTP and WebSocket API: the roadmap's surface, the error envelope, access code, and the voice stream."""

from datetime import datetime

import pytest

pytestmark = pytest.mark.postgres
PHONE = "+919876543210"


def _slot(client, day="2026-11-03"):
    slots = client.get("/api/availability", params={"service": "haircut", "date_from": day, "date_to": day}).json()
    assert slots, "seeded calendar should have free haircut slots"
    return slots[0]


def test_health_and_reference_data(client):
    health = client.get("/api/health").json()
    assert health["status"] == "ok" and health["database"] == "ok" and health["brain"] == "rules"
    assert {s["code"] for s in client.get("/api/services").json()} == {"haircut", "beard_trim", "colour", "manicure"}
    assert [s["name"] for s in client.get("/api/staff").json()] == ["Asha", "Meera", "Ravi"]


def test_availability_validation(client):
    bad = client.get("/api/availability", params={"service": "haircut", "date_from": "2026-11-05",
                                                  "date_to": "2026-11-03"})
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "invalid_range"
    unknown = client.get("/api/availability", params={"service": "tattoo", "date_from": "2026-11-03",
                                                      "date_to": "2026-11-03"})
    assert unknown.json()["error"]["code"] == "unknown_service"


def test_booking_create_idempotent_conflict_reschedule_cancel(client):
    slot = _slot(client)
    body = {"slot_id": slot["slot_id"], "name": "Shlok", "phone_e164": "98765 43210"}
    created = client.post("/api/bookings", json=body, headers={"Idempotency-Key": "abc"})
    assert created.status_code == 201 and created.json()["phone_e164"] == PHONE
    replay = client.post("/api/bookings", json=body, headers={"Idempotency-Key": "abc"})
    assert replay.status_code == 200 and replay.json()["id"] == created.json()["id"]
    clash = client.post("/api/bookings", json={**body, "name": "Other"})
    assert clash.status_code == 409 and clash.json()["error"]["code"] == "slot_taken"
    booking_id = created.json()["id"]
    later = _slot(client, "2026-11-04")
    moved = client.patch(f"/api/bookings/{booking_id}", json={"action": "reschedule", "new_slot_id": later["slot_id"]})
    assert moved.status_code == 200
    assert datetime.fromisoformat(moved.json()["starts_at"]) == datetime.fromisoformat(later["starts_at"])
    cancelled = client.patch(f"/api/bookings/{booking_id}", json={"action": "cancel", "reason": "test"})
    assert cancelled.json()["status"] == "cancelled"
    assert [m["kind"] for m in client.get("/api/sms").json()] == ["cancelled", "rescheduled", "booked"]
    assert client.get("/api/bookings").json() == []
    assert len(client.get("/api/bookings", params={"include_cancelled": True}).json()) == 1


def test_validation_and_not_found_use_the_error_envelope(client):
    r = client.post("/api/bookings", json={"slot_id": "x" * 12, "name": "<script>", "phone_e164": PHONE})
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_request"
    assert client.get("/api/calls/not-a-uuid").json()["error"]["code"] == "not_found"
    assert client.patch("/api/bookings/00000000-0000-0000-0000-000000000000",
                        json={"action": "cancel"}).status_code == 404


def test_text_call_end_to_end(client):
    """Milestone M1: a chat UI books, and GET /calls/:id shows transcript, tool calls and latency per turn."""
    call = client.post("/api/voice/incoming", json={"caller_phone": PHONE, "channel": "text"}).json()
    assert call["greeting"].startswith("Thanks for calling") and call["state"] == "GREET"
    cid = call["call_id"]
    for line in ["a haircut tomorrow please", "the first one", "Shlok", "yes"]:
        turn = client.post(f"/api/calls/{cid}/turns", json={"text": line})
        assert turn.status_code == 200
    assert turn.json()["outcome"] == "booked"
    detail = client.get(f"/api/calls/{cid}").json()
    assert [t["speaker"] for t in detail["turns"]][:3] == ["agent", "caller", "agent"] and len(detail["turns"]) == 9
    assert [t["tool"] for t in detail["tool_calls"]] == ["find_slots", "book", "book"]
    assert detail["bookings"][0]["customer"] == "Shlok" and detail["latency"]["server_first_p95"] is not None
    ended = client.post(f"/api/calls/{cid}/end").json()
    assert ended["outcome"] == "booked"
    closed = client.post(f"/api/calls/{cid}/turns", json={"text": "hello"})
    assert closed.status_code == 409 and closed.json()["error"]["code"] == "call_closed"
    metrics = client.get("/api/metrics").json()
    assert metrics["calls"] == 1 and metrics["outcomes"] == {"booked": 1} and metrics["tool_calls"]["ok"] == 3


def test_manual_handoff_and_queue(client):
    cid = client.post("/api/voice/incoming", json={"caller_phone": PHONE}).json()["call_id"]
    r = client.post(f"/api/calls/{cid}/handoff", json={"reason": "caller_request"}).json()
    assert r["state"] == "HANDOFF" and "\n" in r["summary"]
    queue = client.get("/api/handoffs").json()
    assert queue[0]["id"] == cid and queue[0]["handoff_status"] == "waiting"
    claimed = client.patch(f"/api/calls/{cid}/handoff", json={"status": "claimed"}).json()
    assert claimed["handoff_status"] == "claimed"


def test_invalid_caller_phone_is_rejected(client):
    r = client.post("/api/voice/incoming", json={"caller_phone": "12"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_phone"


def test_voice_stream_sentences_metrics_barge_in_and_hangup(client):
    cid = client.post("/api/voice/incoming", json={"caller_phone": PHONE}).json()["call_id"]
    with client.websocket_connect(f"/api/voice/stream?call_id={cid}") as ws:
        ws.send_json({"type": "caller_turn", "text": "can I book a haircut tomorrow morning"})
        sentences = []
        while (msg := ws.receive_json())["type"] == "agent_sentence":
            sentences.append(msg["text"])
        assert msg["type"] == "turn_done" and msg["state"] == "CHECK_SLOTS" and len(sentences) == 2
        ws.send_json({"type": "audio_metrics", "seq": msg["seq"], "eos_to_audio_ms": 640})
        ws.send_json({"type": "barge_in"})
        ws.send_json({"type": "hangup"})
        while (msg := ws.receive_json())["type"] != "ended":
            pass
        assert msg["outcome"] == "answered"
    detail = client.get(f"/api/calls/{cid}").json()
    agent = [t for t in detail["turns"] if t["speaker"] == "agent" and t["seq"] > 1][0]
    assert agent["eos_to_audio_ms"] == 640 and agent["interrupted"] is True
    assert detail["latency"]["eos_to_audio_p50"] == 640


def test_voice_stream_rejects_unknown_call(client):
    from starlette.websockets import WebSocketDisconnect
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/api/voice/stream?call_id=00000000-0000-0000-0000-000000000000") as ws:
            ws.receive_json()


def test_access_code_guards_api_and_stream(client, settings_override):
    from app.main import create_app
    from fastapi.testclient import TestClient
    settings_override(demo_access_code="letmein")
    with TestClient(create_app()) as guarded:
        assert guarded.get("/api/health").status_code == 200
        assert guarded.get("/api/calls").json()["error"]["code"] == "access_code_required"
        assert guarded.get("/api/calls", headers={"X-Access-Code": "letmein"}).status_code == 200
        cid = guarded.post("/api/voice/incoming", json={}, headers={"X-Access-Code": "letmein"}).json()["call_id"]
        from starlette.websockets import WebSocketDisconnect
        with pytest.raises(WebSocketDisconnect):
            with guarded.websocket_connect(f"/api/voice/stream?call_id={cid}") as ws:
                ws.receive_json()
        with guarded.websocket_connect(f"/api/voice/stream?call_id={cid}&code=letmein") as ws:
            ws.send_json({"type": "hangup"})
            assert ws.receive_json()["type"] == "ended"


def test_call_rate_limit(client):
    from app.api.routes import call_limiter
    call_limiter.limit = 2
    try:
        assert client.post("/api/voice/incoming", json={}).status_code == 201
        assert client.post("/api/voice/incoming", json={}).status_code == 201
        r = client.post("/api/voice/incoming", json={})
        assert r.status_code == 429 and r.json()["error"]["code"] == "rate_limited"
    finally:
        call_limiter.limit = 30


def test_closing_the_stream_ends_the_call(client):
    cid = client.post("/api/voice/incoming", json={"caller_phone": PHONE}).json()["call_id"]
    with client.websocket_connect(f"/api/voice/stream?call_id={cid}") as ws:
        ws.send_json({"type": "caller_turn", "text": "a haircut tomorrow please"})
        while ws.receive_json()["type"] != "turn_done":
            pass
    import time
    for _ in range(20):   # the server notices the disconnect asynchronously
        if client.get(f"/api/calls/{cid}").json()["ended_at"]:
            break
        time.sleep(0.1)
    assert client.get(f"/api/calls/{cid}").json()["ended_at"] is not None
