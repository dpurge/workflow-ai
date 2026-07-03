"""The backend abstraction every agent runner implements.

A backend receives a fully-specified, history-free invocation and returns the
assistant's final text plus, when the backend can enforce it natively, a
structured JSON object. The engine owns parsing/validation policy so that
backends without native schema support (e.g. Pi) still work — they simply
return `structured=None` and the engine extracts/validates JSON from `text`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from pydantic import BaseModel


class AgentOutputError(RuntimeError):
    """Raised when the agent cannot be invoked or its output cannot be obtained."""


@dataclass
class AgentResult:
    text: str
    structured: dict[str, Any] | None = None
    cost_usd: float | None = None


@dataclass
class AgentInvocation:
    """Everything needed to run one node in a clean context."""

    system_prompt: str
    prompt: str
    output_kind: str = "json"  # "json" | "text"
    schema: type[BaseModel] | None = None  # set for json nodes (native enforcement)
    allowed_tools: list[str] = field(default_factory=list)
    skills: list[str] = field(default_factory=list)
    mcp_config: str | None = None
    model: str | None = None
    max_turns: int | None = None


class AgentBackend(Protocol):
    def run(self, invocation: AgentInvocation) -> AgentResult:
        """Execute one node and return its output. Raises AgentOutputError."""
        ...
