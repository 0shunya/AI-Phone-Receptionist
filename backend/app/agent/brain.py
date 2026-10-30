"""What the engine asks of a "brain": given the turn context, say something and/or pick one tool.

Two implementations: ``GeminiBrain`` (the real model, through the gateway) and ``RuleBrain`` (a deterministic
reference policy used by the offline evaluation and as a no-key demo fallback). The engine, state machine, tool
router and database are identical for both, which is what lets the offline eval measure them honestly.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol


@dataclass
class TurnContext:
    call_id: str
    state: str
    allowed_tools: list[str]
    tool_schemas: list[dict]
    session: dict
    history: list[tuple[str, str]]      # (speaker, text), oldest first, excluding the current caller turn
    caller_text: str
    now_local: datetime
    caller_phone: str | None
    services: list[dict]                 # {code, name, duration_min, price}
    hours_text: str
    business_name: str
    staff_names: list[str] = field(default_factory=list)


@dataclass
class Decision:
    text: str = ""
    tool: str | None = None
    args: dict = field(default_factory=dict)
    understood: bool = True
    error_category: str | None = None   # set when the model call itself failed
    fatal: bool = False                  # the brain cannot continue (quota, auth): hand off now
    model: str | None = None
    notes: dict | None = None            # RuleBrain's own memory, stored by the engine in session["brain_notes"]


class Brain(Protocol):
    name: str

    def decide(self, ctx: TurnContext, on_text: Callable[[str], None]) -> Decision: ...
