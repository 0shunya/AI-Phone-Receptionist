"""The model gateway: one door for every model call.

It streams the reply (so the first sentence can be spoken before the model finishes), keeps at most one tool
call per turn, retries only what is worth retrying and only before anything has been spoken, enforces a daily
call cap, and records every attempt through ``record_call``. Business code never touches a vendor SDK.
"""

from __future__ import annotations

import logging
import random
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field

from app.config import Settings, get_settings
from app.llm.prompts import PROMPT_VERSION
from app.llm.providers import ChatRequest, GeminiProvider, Provider, ProviderError

log = logging.getLogger(__name__)
FEATURE = "receptionist_turn"


@dataclass
class CallRecord:
    trace_id: str
    call_id: str | None
    feature: str
    provider: str
    model: str
    prompt_version: str
    attempt: int
    status: str                 # success | error
    latency_ms: int
    first_token_ms: int | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    error_category: str | None = None
    error_message: str | None = None


@dataclass
class TurnReply:
    text: str
    tool: str | None
    args: dict
    attempts: int
    latency_ms: int
    first_token_ms: int | None
    model: str
    input_tokens: int | None = None
    output_tokens: int | None = None


def build_provider(settings: Settings | None = None) -> Provider:
    settings = settings or get_settings()
    if not settings.gemini_api_key:
        raise ProviderError("config_missing", "GEMINI_API_KEY is not set on the server.", retryable=False)
    return GeminiProvider(settings.gemini_api_key, settings.gemini_model,
                          timeout_seconds=settings.model_timeout_seconds, thinking_level=settings.gemini_thinking_level)


@dataclass
class ModelGateway:
    provider: Provider
    record_call: Callable[[CallRecord], None]
    max_attempts: int = 2
    under_daily_limit: Callable[[], bool] = lambda: True
    sleep: Callable[[float], None] = field(default=time.sleep)

    def chat_stream(self, request: ChatRequest, *, on_text: Callable[[str], None], call_id: str | None = None,
                    trace_id: str | None = None, feature: str = FEATURE) -> TurnReply:
        trace_id = trace_id or str(uuid.uuid4())
        if not self.under_daily_limit():
            error = ProviderError("daily_limit", "The daily model-call limit for this deployment is reached.",
                                  retryable=False)
            self._record(trace_id, call_id, feature, 1, "error", 0, None, None, error)
            raise error
        total = 0
        for attempt in range(1, self.max_attempts + 1):
            started = time.perf_counter()
            first_token: int | None = None
            texts: list[str] = []
            tool: tuple[str, dict] | None = None
            usage = (None, None)
            try:
                for event in self.provider.stream(request):
                    if event.kind == "text":
                        if first_token is None:
                            first_token = round((time.perf_counter() - started) * 1000)
                        texts.append(event.text)
                        on_text(event.text)
                    elif event.kind == "tool":
                        if first_token is None:
                            first_token = round((time.perf_counter() - started) * 1000)
                        tool = tool or (event.tool or "", event.args)   # one tool per turn: keep the first
                    elif event.kind == "usage":
                        usage = (event.input_tokens, event.output_tokens)
            except ProviderError as exc:
                latency = round((time.perf_counter() - started) * 1000)
                total += latency
                self._record(trace_id, call_id, feature, attempt, "error", latency, first_token, usage, exc)
                # Never retry once words have been spoken: the caller would hear them twice.
                if not exc.retryable or texts or attempt == self.max_attempts:
                    raise
                self.sleep(min(2.0, 0.4 * attempt + random.uniform(0, 0.2)))
                continue
            latency = round((time.perf_counter() - started) * 1000)
            total += latency
            self._record(trace_id, call_id, feature, attempt, "success", latency, first_token, usage, None)
            return TurnReply("".join(texts).strip(), tool[0] if tool else None, tool[1] if tool else {}, attempt,
                             total, first_token, self.provider.model, usage[0], usage[1])
        raise ProviderError("provider_error", "No attempts were made.", retryable=False)

    def _record(self, trace_id, call_id, feature, attempt, status, latency, first_token, usage, error) -> None:
        record = CallRecord(trace_id=trace_id, call_id=call_id, feature=feature, provider=self.provider.name,
                            model=self.provider.model, prompt_version=PROMPT_VERSION, attempt=attempt,
                            status=status, latency_ms=latency, first_token_ms=first_token,
                            input_tokens=(usage or (None, None))[0], output_tokens=(usage or (None, None))[1],
                            error_category=getattr(error, "category", None),
                            error_message=(getattr(error, "message", None) or None) and error.message[:300])
        try:
            self.record_call(record)
        except Exception:  # observability must never break a live call
            log.exception("failed to record model call", extra={"trace_id": trace_id})
