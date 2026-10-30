"""Provider adapters. Only this module imports a vendor SDK; the rest of the app talks to ``Provider``."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Protocol


class ProviderError(Exception):
    """A categorised provider failure. ``retryable`` decides whether the gateway may try again."""

    def __init__(self, category: str, message: str, *, retryable: bool, status_code: int | None = None):
        super().__init__(message)
        self.category = category
        self.message = message
        self.retryable = retryable
        self.status_code = status_code


@dataclass
class ChatRequest:
    system_instruction: str
    messages: list[tuple[str, str]]     # ("user" | "model", text), oldest first; the last one is the caller
    tools: list[dict]
    temperature: float = 0.3
    max_output_tokens: int = 400


@dataclass
class StreamEvent:
    kind: str                            # "text" | "tool" | "usage"
    text: str = ""
    tool: str | None = None
    args: dict = field(default_factory=dict)
    input_tokens: int | None = None
    output_tokens: int | None = None


class Provider(Protocol):
    name: str
    model: str

    def stream(self, request: ChatRequest) -> Iterator[StreamEvent]: ...


def classify_status(code: int | None, message: str, details: object = None) -> ProviderError:
    text = (message or "")[:200]
    blob = json.dumps(details, default=str) + text
    if code == 429 and "PerDay" in blob:
        return ProviderError("quota_exhausted", "The daily Gemini quota for this key and model is used up.",
                             retryable=False, status_code=code)
    if code == 429:
        return ProviderError("rate_limited", f"Rate limited: {text}", retryable=False, status_code=code)
    if code in (401, 403):
        return ProviderError("auth_error", "The Gemini API key was rejected.", retryable=False, status_code=code)
    if code == 404:
        return ProviderError("model_not_found", f"Model not available to this key: {text}", retryable=False,
                             status_code=code)
    if code in (400, 413, 422):
        return ProviderError("bad_request", f"Request rejected: {text}", retryable=False, status_code=code)
    if code is not None and code >= 500:
        return ProviderError("server_error", f"Provider server error {code}.", retryable=True, status_code=code)
    return ProviderError("provider_error", f"Unexpected provider error: {text}", retryable=False, status_code=code)


class GeminiProvider:
    name = "google"

    def __init__(self, api_key: str, model: str, *, timeout_seconds: float, thinking_level: str = "minimal"):
        from google import genai
        from google.genai import types

        self._types = types
        self.model = model
        self.thinking_level = thinking_level
        self._client = genai.Client(
            api_key=api_key,
            # The gateway owns retries so every attempt is logged; disable the SDK's own retry loop.
            http_options=types.HttpOptions(timeout=int(timeout_seconds * 1000),
                                           retry_options=types.HttpRetryOptions(attempts=1)),
        )

    def _thinking(self):
        types = self._types
        if self.model.startswith("gemini-2.5"):
            return types.ThinkingConfig(thinking_budget=0 if "pro" not in self.model else 128)
        if self.model.startswith("gemma"):
            return None
        return types.ThinkingConfig(thinking_level=self.thinking_level)

    def stream(self, request: ChatRequest) -> Iterator[StreamEvent]:
        from google.genai import errors

        types = self._types
        contents = [types.Content(role=role, parts=[types.Part.from_text(text=text)]) for role, text in request.messages]
        tools = [types.Tool(function_declarations=[
            types.FunctionDeclaration(name=t["name"], description=t["description"],
                                      parameters_json_schema=t["parameters"]) for t in request.tools])] if request.tools else None
        config = types.GenerateContentConfig(
            system_instruction=request.system_instruction, tools=tools,
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
            temperature=request.temperature, max_output_tokens=request.max_output_tokens,
            thinking_config=self._thinking(),
        )
        try:
            usage = None
            for chunk in self._client.models.generate_content_stream(model=self.model, contents=contents, config=config):
                usage = chunk.usage_metadata or usage
                for candidate in chunk.candidates or []:
                    for part in (candidate.content.parts if candidate.content else None) or []:
                        if getattr(part, "thought", False):
                            continue
                        if part.function_call is not None:
                            yield StreamEvent("tool", tool=part.function_call.name,
                                              args=dict(part.function_call.args or {}))
                        elif part.text:
                            yield StreamEvent("text", text=part.text)
            yield StreamEvent("usage", input_tokens=getattr(usage, "prompt_token_count", None),
                              output_tokens=getattr(usage, "candidates_token_count", None))
        except errors.APIError as exc:
            raise classify_status(exc.code, exc.message or str(exc), getattr(exc, "details", None)) from exc
        except ProviderError:
            raise
        except Exception as exc:  # network errors, timeouts
            name = type(exc).__name__.lower()
            if "timeout" in name or "timeout" in str(exc).lower():
                raise ProviderError("timeout", "The model call timed out.", retryable=True) from exc
            if any(k in name for k in ("connect", "network", "remoteprotocol", "readerror")):
                raise ProviderError("network_error", f"Network error ({type(exc).__name__}).", retryable=True) from exc
            raise ProviderError("provider_error", f"Unexpected error: {type(exc).__name__}", retryable=False) from exc


@dataclass
class ScriptedProvider:
    """Deterministic provider for tests: each queued item is a list of events, an error, or a callable."""

    script: list[list[StreamEvent] | ProviderError | Callable[[ChatRequest], list[StreamEvent]]] = field(
        default_factory=list)
    name: str = "scripted"
    model: str = "scripted-model"
    requests: list[ChatRequest] = field(default_factory=list)

    def stream(self, request: ChatRequest) -> Iterator[StreamEvent]:
        self.requests.append(request)
        if not self.script:
            raise ProviderError("provider_error", "ScriptedProvider has no more responses.", retryable=False)
        item = self.script.pop(0)
        if isinstance(item, ProviderError):
            raise item
        yield from (item(request) if callable(item) else item)

