"""WS /api/voice/stream: the live call.

In this build the browser does the speech (Web Speech API recognition and synthesis), so the stream carries
end-of-turn transcripts up and agent sentences down, plus the playback events that matter for latency and
barge-in. A telephony adapter would carry base64 audio frames on the same socket instead.

Client -> server
  {"type": "caller_turn", "text": "..."}                     the caller finished speaking (end of turn)
  {"type": "barge_in"}                                       the caller started talking over the agent
  {"type": "audio_metrics", "seq": 5, "eos_to_audio_ms": 812} measured in the browser for agent turn ``seq``
  {"type": "hangup"}
Server -> client
  {"type": "agent_sentence", "text": "..."}                  speak this now (sentence by sentence)
  {"type": "turn_done", ...TurnOutcome}                      the turn is stored; ``seq`` is the agent turn
  {"type": "error", "code": "...", "message": "..."}
  {"type": "ended", "outcome": "..."}
"""

from __future__ import annotations

import asyncio
import hmac
import logging
import uuid

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy import select, update

from app.agent.engine import CallClosed
from app.agent.factory import get_engine
from app.api.routes import turn_limiter
from app.config import get_settings
from app.db import session_scope
from app.models import Call, Speaker, Turn

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api")


def _run_turn(call_id: uuid.UUID, text: str, emit) -> dict:
    with session_scope() as db:
        return get_engine().handle_turn(db, call_id, text, emit).__dict__


def _mark(call_id: uuid.UUID, seq: int | None, **values) -> None:
    with session_scope() as db:
        if seq is None:
            seq = db.scalar(select(Turn.seq).where(Turn.call_id == call_id, Turn.speaker == Speaker.agent)
                            .order_by(Turn.seq.desc()).limit(1))
        if seq is not None:
            db.execute(update(Turn).where(Turn.call_id == call_id, Turn.seq == seq,
                                          Turn.speaker == Speaker.agent).values(**values))


def _end(call_id: uuid.UUID) -> str:
    with session_scope() as db:
        return get_engine().end_call(db, call_id).outcome.value


def _call_open(call_id: uuid.UUID) -> bool:
    with session_scope() as db:
        call = db.get(Call, call_id)
        return call is not None and call.ended_at is None


@router.websocket("/voice/stream")
async def voice_stream(ws: WebSocket, call_id: str, code: str | None = None):
    expected = get_settings().demo_access_code
    if expected and not hmac.compare_digest((code or "").encode(), expected.encode()):
        await ws.close(code=4401, reason="access code required")
        return
    try:
        cid = uuid.UUID(call_id)
    except ValueError:
        await ws.close(code=4404, reason="no such call")
        return
    if not await asyncio.to_thread(_call_open, cid):
        await ws.close(code=4404, reason="no such call")
        return
    await ws.accept()
    loop = asyncio.get_running_loop()
    inbox: asyncio.Queue[dict] = asyncio.Queue()
    flags = {"interrupted": False, "busy": False}

    async def reader() -> None:
        while True:
            message = await ws.receive_json()
            if message.get("type") == "barge_in":
                # Playback already stopped in the browser; stop sending the rest of this turn's sentences.
                flags["interrupted"] = True
                if not flags["busy"]:
                    await asyncio.to_thread(_mark, cid, None, interrupted=True)
            else:
                await inbox.put(message)

    def emit(sentence: str) -> None:
        if not flags["interrupted"]:
            asyncio.run_coroutine_threadsafe(ws.send_json({"type": "agent_sentence", "text": sentence}), loop).result(10)

    reader_task = asyncio.create_task(reader())
    try:
        while True:
            getter = asyncio.create_task(inbox.get())
            done, _ = await asyncio.wait({getter, reader_task}, return_when=asyncio.FIRST_COMPLETED)
            if reader_task in done:
                getter.cancel()
                break
            message = getter.result()
            kind = message.get("type")
            if kind == "caller_turn":
                text = str(message.get("text") or "").strip()
                if not text:
                    continue
                if not turn_limiter.allow(call_id):
                    await ws.send_json({"type": "error", "code": "rate_limited", "message": "Too many turns."})
                    continue
                flags.update(interrupted=False, busy=True)
                try:
                    outcome = await asyncio.to_thread(_run_turn, cid, text[:500], emit)
                except CallClosed:
                    await ws.send_json({"type": "error", "code": "call_closed", "message": "This call has ended."})
                    break
                finally:
                    flags["busy"] = False
                if flags["interrupted"]:
                    await asyncio.to_thread(_mark, cid, outcome["seq"], interrupted=True)
                await ws.send_json({"type": "turn_done", **outcome})
            elif kind == "audio_metrics":
                ms, seq = message.get("eos_to_audio_ms"), message.get("seq")
                if isinstance(ms, int) and isinstance(seq, int) and 0 <= ms < 120_000:
                    await asyncio.to_thread(_mark, cid, seq, eos_to_audio_ms=ms)
            elif kind == "hangup":
                outcome = await asyncio.to_thread(_end, cid)
                await ws.send_json({"type": "ended", "outcome": outcome})
                break
    except WebSocketDisconnect:
        pass
    except Exception:
        log.exception("voice stream failed", extra={"call_id": call_id})
    finally:
        reader_task.cancel()
        # A closed tab or dropped connection is a hang-up (end_call is idempotent).
        await asyncio.to_thread(_end, cid)
        try:
            await ws.close()
        except RuntimeError:
            pass
