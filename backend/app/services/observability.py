"""Writes gateway call records to ``model_calls`` and computes latency percentiles from the call log."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from app.config import get_settings
from app.db import session_scope
from app.llm.gateway import CallRecord
from app.models import ModelCall


def record_model_call(record: CallRecord) -> None:
    """Own transaction, so a failed turn's rollback never erases the evidence of the model call."""
    settings = get_settings()
    cost = None
    if settings.model_price_in_per_mtok is not None and record.input_tokens is not None:
        cost = round((record.input_tokens * settings.model_price_in_per_mtok
                      + (record.output_tokens or 0) * (settings.model_price_out_per_mtok or 0)) / 1_000_000, 8)
    with session_scope() as db:
        db.add(ModelCall(trace_id=record.trace_id, call_id=uuid.UUID(record.call_id) if record.call_id else None,
                         feature=record.feature, provider=record.provider, model=record.model,
                         prompt_version=record.prompt_version, attempt=record.attempt,
                         input_tokens=record.input_tokens, output_tokens=record.output_tokens, cost_usd=cost,
                         latency_ms=record.latency_ms, first_token_ms=record.first_token_ms, status=record.status,
                         error_category=record.error_category, error_message=record.error_message))


def under_daily_limit() -> bool:
    limit = get_settings().daily_model_call_limit
    since = datetime.now(timezone.utc) - timedelta(days=1)
    with session_scope() as db:
        used = db.scalar(select(func.count(ModelCall.id)).where(ModelCall.created_at >= since)) or 0
    return used < limit


def percentile(values: list[int], q: float) -> int | None:
    """Nearest-rank percentile (no interpolation), so the reported p95 is a latency that actually happened."""
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, -(-len(ordered) * q // 100))   # ceil(n * q / 100)
    return ordered[int(rank) - 1]
