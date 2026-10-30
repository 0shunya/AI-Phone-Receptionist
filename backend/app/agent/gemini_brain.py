"""GeminiBrain: the real model, called only through the gateway, with only the current state's tools declared."""

from __future__ import annotations

from collections.abc import Callable

from app.agent.brain import Decision, TurnContext
from app.config import get_settings
from app.llm.gateway import ModelGateway
from app.llm.prompts import SYSTEM_TEMPLATE, render_facts
from app.llm.providers import ChatRequest, ProviderError

FATAL = {"quota_exhausted", "auth_error", "config_missing", "model_not_found", "daily_limit", "bad_request",
         "rate_limited"}


def build_request(ctx: TurnContext) -> ChatRequest:
    settings = get_settings()
    services = "; ".join(f"{s['code']}: {s['name']}, {s['duration_min']} min, {s['price']}" for s in ctx.services)
    system = SYSTEM_TEMPLATE.format(
        business=ctx.business_name, now_local=ctx.now_local.strftime("%A %d %B %Y, %H:%M"),
        timezone=settings.business_timezone, caller_phone=ctx.caller_phone or "withheld", services=services,
        hours=ctx.hours_text, staff=", ".join(ctx.staff_names) or "unknown", state=ctx.state, tools=", ".join(ctx.allowed_tools) or "none",
        facts=render_facts(ctx.session))
    messages: list[tuple[str, str]] = [("user", "(The call has connected.)")]
    for speaker, text in [*ctx.history[-settings.agent_history_turns:], ("caller", ctx.caller_text)]:
        role = "user" if speaker == "caller" else "model"
        if messages[-1][0] == role:   # the API wants alternating roles; merge consecutive turns
            messages[-1] = (role, f"{messages[-1][1]}\n{text}")
        else:
            messages.append((role, text))
    return ChatRequest(system, messages, ctx.tool_schemas, temperature=settings.agent_temperature,
                       max_output_tokens=settings.agent_max_output_tokens)


class GeminiBrain:
    def __init__(self, gateway: ModelGateway):
        self.gateway = gateway
        self.name = f"gemini:{gateway.provider.model}"

    def decide(self, ctx: TurnContext, on_text: Callable[[str], None]) -> Decision:
        try:
            reply = self.gateway.chat_stream(build_request(ctx), on_text=on_text, call_id=ctx.call_id)
        except ProviderError as exc:
            return Decision(understood=False, error_category=exc.category, fatal=exc.category in FATAL,
                            model=self.gateway.provider.model)
        return Decision(text=reply.text, tool=reply.tool, args=reply.args, understood=bool(reply.text or reply.tool),
                        model=reply.model)
