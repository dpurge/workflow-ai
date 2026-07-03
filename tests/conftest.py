"""Shared test fixtures: a scripted in-memory backend and graph builders."""

from __future__ import annotations

from typing import Callable

import json

from workflow_ai import definitions  # noqa: F401 (populates registry)
from workflow_ai.backends.base import AgentInvocation, AgentResult


class ScriptedBackend:
    """A backend whose per-node output is supplied by a callable.

    `responder(invocation)` returns a dict (treated as native structured output)
    or a str (treated as text output). The engine performs schema validation,
    so returning a malformed dict exercises the retry path.
    """

    def __init__(self, responder: Callable[[AgentInvocation], object]) -> None:
        self.responder = responder
        self.calls: list[AgentInvocation] = []

    def run(self, invocation: AgentInvocation) -> AgentResult:
        self.calls.append(invocation)
        payload = self.responder(invocation)
        if isinstance(payload, str):
            return AgentResult(text=payload, structured=None, cost_usd=0.0)
        return AgentResult(text=json.dumps(payload), structured=payload, cost_usd=0.0)
