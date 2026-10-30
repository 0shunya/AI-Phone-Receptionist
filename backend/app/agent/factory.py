"""Builds the engine from configuration. AGENT_BRAIN picks the brain; there is no silent fallback between them."""

from __future__ import annotations

from collections.abc import Iterator
from functools import lru_cache

from app.agent.engine import Engine
from app.agent.gemini_brain import GeminiBrain
from app.agent.rules import RuleBrain
from app.config import get_settings
from app.llm.gateway import ModelGateway, build_provider
from app.llm.providers import ChatRequest, ProviderError, StreamEvent
from app.services.observability import record_model_call, under_daily_limit


class UnconfiguredProvider:
    """Stands in when GEMINI_API_KEY is missing: every turn fails fast and the call is handed to a human."""

    name = "google"

    def __init__(self, model: str):
        self.model = model

    def stream(self, request: ChatRequest) -> Iterator[StreamEvent]:
        raise ProviderError("config_missing", "GEMINI_API_KEY is not set on the server.", retryable=False)


def build_gateway() -> ModelGateway:
    settings = get_settings()
    provider = build_provider(settings) if settings.gemini_configured else UnconfiguredProvider(settings.gemini_model)
    return ModelGateway(provider, record_model_call, max_attempts=settings.model_max_attempts,
                        under_daily_limit=under_daily_limit)


_override: Engine | None = None


def override_engine(engine: Engine | None) -> None:
    """Tests and the evaluation swap in an engine with a fixed clock or a scripted provider."""
    global _override
    _override = engine


@lru_cache
def _configured_engine() -> Engine:
    if get_settings().agent_brain == "rules":
        return Engine(RuleBrain())
    return Engine(GeminiBrain(build_gateway()))


def get_engine() -> Engine:
    return _override or _configured_engine()
