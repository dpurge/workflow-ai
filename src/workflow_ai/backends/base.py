"""The backend abstraction every agent runner implements.

A backend receives a fully-specified, history-free invocation and returns the
assistant's final text plus, when the backend can enforce it natively, a
structured JSON object. The engine owns parsing/validation policy so that
backends without native schema support still work — they simply return
`structured=None` and the engine extracts/validates JSON from `text`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, get_args, get_origin

from pydantic import BaseModel, RootModel, create_model


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


# --- RootModel[list] helpers ------------------------------------------------

# OpenAI and Anthropic structured-output APIs require the top-level JSON Schema
# to be ``type: "object"``.  A Pydantic ``RootModel[list[...]]`` produces
# ``type: "array"``, which both APIs reject with HTTP 400.  These helpers detect
# that case and wrap/unwrap the payload so the rest of the engine is unaware.


def is_root_list_schema(schema: type[BaseModel] | None) -> bool:
    """Return True when *schema* is a ``RootModel[list[...]]``."""
    if schema is None or not (isinstance(schema, type) and issubclass(schema, RootModel)):
        return False
    root_field = schema.model_fields.get("root")
    if root_field is None or root_field.annotation is None:
        return False
    return get_origin(root_field.annotation) is list


def wrap_root_list_schema(schema: type[BaseModel]) -> tuple[type[BaseModel], bool]:
    """If *schema* is a ``RootModel[list[...]]`` return a wrapper ``BaseModel``
    with a single ``items`` field (so the JSON Schema is ``type: "object"``)
    plus ``True``.  Otherwise return ``(schema, False)``.
    """
    if not is_root_list_schema(schema):
        return schema, False
    root_annotation = schema.model_fields["root"].annotation  # type: ignore[index]
    wrapper = create_model(schema.__name__, items=(root_annotation, ...), __base__=BaseModel)
    return wrapper, True


def unwrap_root_list_payload(structured: Any, was_wrapped: bool) -> Any:
    """If *was_wrapped*, extract the list from ``{"items": [...]}`` → ``[...]``."""
    if was_wrapped and isinstance(structured, dict) and "items" in structured:
        return structured["items"]
    return structured
