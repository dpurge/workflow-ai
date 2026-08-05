"""Tests for AnthropicBackend.

The real 'anthropic' package is never installed in the test environment.
We inject a fake module into sys.modules at import time so that the
lazy `import anthropic` inside AnthropicBackend.__init__ resolves to our fake.
"""

from __future__ import annotations

import sys
import types
from dataclasses import dataclass, field
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# Fake anthropic module — must be in sys.modules before any backend import.
# ---------------------------------------------------------------------------

_fake_anthropic = types.ModuleType("anthropic")
if "anthropic" not in sys.modules:
    sys.modules["anthropic"] = _fake_anthropic


# ---------------------------------------------------------------------------
# Fake response dataclasses
# ---------------------------------------------------------------------------


@dataclass
class FakeTextBlock:
    type: str = "text"
    text: str = ""


@dataclass
class FakeToolUseBlock:
    type: str = "tool_use"
    name: str = ""
    id: str = "tid1"
    input: dict = field(default_factory=dict)


@dataclass
class FakeAnthropicResponse:
    content: list


# ---------------------------------------------------------------------------
# Factory: build a fake Anthropic class with scripted responses
# ---------------------------------------------------------------------------


def make_fake_client(responses: list):
    """Return (FakeAnthropicClass, calls_list).

    Each call to FakeMessages.create() pops the next response from `responses`
    and appends the kwargs to `calls`.
    """
    calls: list[dict] = []
    resp_iter = iter(responses)

    class FakeMessages:
        def create(self, **kwargs):
            calls.append(kwargs)
            return next(resp_iter)

    class FakeClient:
        messages = FakeMessages()

    class FakeAnthropicClass:
        def __init__(self, **kw):
            self.messages = FakeClient().messages

    return FakeAnthropicClass, calls


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_text_node_returns_text(monkeypatch):
    from workflow_ai.backends.anthropic_sdk import AnthropicBackend
    from workflow_ai.backends.base import AgentInvocation

    FakeClass, _ = make_fake_client(
        [FakeAnthropicResponse(content=[FakeTextBlock(text="hello world")])]
    )
    sys.modules["anthropic"].Anthropic = FakeClass

    b = AnthropicBackend(model="m")
    inv = AgentInvocation(system_prompt="sys", prompt="user", output_kind="text")
    result = b.run(inv)
    assert result.text == "hello world"
    assert result.structured is None


def test_json_node_emit_result(monkeypatch):
    from pydantic import BaseModel

    from workflow_ai.backends.anthropic_sdk import AnthropicBackend
    from workflow_ai.backends.base import AgentInvocation

    class Out(BaseModel):
        value: str

    FakeClass, _ = make_fake_client(
        [
            FakeAnthropicResponse(
                content=[
                    FakeToolUseBlock(name="emit_result", input={"value": "hello"})
                ]
            )
        ]
    )
    sys.modules["anthropic"].Anthropic = FakeClass

    b = AnthropicBackend(model="m")
    inv = AgentInvocation(
        system_prompt="sys", prompt="user", output_kind="json", schema=Out
    )
    result = b.run(inv)
    assert result.structured == {"value": "hello"}


def test_tool_loop_dispatches_and_continues(monkeypatch):
    import workflow_ai.backends.tools as tools_mod

    from workflow_ai.backends.anthropic_sdk import AnthropicBackend
    from workflow_ai.backends.base import AgentInvocation

    FakeClass, calls = make_fake_client(
        [
            FakeAnthropicResponse(
                content=[FakeToolUseBlock(name="Read", id="t1", input={"path": "f.txt"})]
            ),
            FakeAnthropicResponse(content=[FakeTextBlock(text="done")]),
        ]
    )
    sys.modules["anthropic"].Anthropic = FakeClass
    monkeypatch.setattr(tools_mod, "dispatch", lambda tools, name, args: "file content")

    b = AnthropicBackend(model="m")
    inv = AgentInvocation(
        system_prompt="sys",
        prompt="user",
        output_kind="text",
        allowed_tools=["Read"],
    )
    result = b.run(inv)
    assert result.text == "done"

    # The second API call's messages must include a tool_result entry.
    # The backend appends {"role": "user", "content": [{"type": "tool_result", ...}]}
    second_messages = calls[1]["messages"]
    assert any(
        isinstance(m.get("content"), list)
        and len(m["content"]) > 0
        and isinstance(m["content"][0], dict)
        and m["content"][0].get("type") == "tool_result"
        for m in second_messages
        if isinstance(m, dict)
    )


def test_loop_exhaustion_raises():
    import workflow_ai.backends.tools as tools_mod

    from workflow_ai.backends.anthropic_sdk import AnthropicBackend
    from workflow_ai.backends.base import AgentInvocation, AgentOutputError

    # Always returns a tool_use block — loop never terminates.
    class InfiniteMessages:
        def create(self, **kwargs):
            return FakeAnthropicResponse(
                content=[FakeToolUseBlock(name="Read", id="t1", input={"path": "x"})]
            )

    class InfiniteClient:
        messages = InfiniteMessages()

    class FakeClass:
        def __init__(self, **kw):
            self.messages = InfiniteClient().messages

    sys.modules["anthropic"].Anthropic = FakeClass

    # Patch dispatch so the tool call doesn't actually try to read a file.
    import workflow_ai.backends.tools as tools_mod  # noqa: F811 (already imported above)
    _orig_dispatch = tools_mod.dispatch
    tools_mod.dispatch = lambda tools, name, args: "content"
    try:
        b = AnthropicBackend(model="m")
        inv = AgentInvocation(
            system_prompt="sys",
            prompt="user",
            output_kind="text",
            allowed_tools=["Read"],
            max_turns=2,
        )
        with pytest.raises(AgentOutputError, match="exceeded"):
            b.run(inv)
    finally:
        tools_mod.dispatch = _orig_dispatch


def test_missing_model_raises():
    from workflow_ai.backends.anthropic_sdk import AnthropicBackend
    from workflow_ai.backends.base import AgentInvocation, AgentOutputError

    class FakeClass:
        def __init__(self, **kw):
            self.messages = None

    sys.modules["anthropic"].Anthropic = FakeClass

    b = AnthropicBackend(model=None)
    inv = AgentInvocation(
        system_prompt="s", prompt="p", output_kind="text", model=None
    )
    with pytest.raises(AgentOutputError):
        b.run(inv)


def test_mcp_config_warns(capsys):
    from workflow_ai.backends.anthropic_sdk import AnthropicBackend
    from workflow_ai.backends.base import AgentInvocation

    FakeClass, _ = make_fake_client(
        [FakeAnthropicResponse(content=[FakeTextBlock(text="ok")])]
    )
    sys.modules["anthropic"].Anthropic = FakeClass

    b = AnthropicBackend(model="m")
    inv = AgentInvocation(
        system_prompt="s", prompt="p", output_kind="text", mcp_config="some/path.json"
    )
    b.run(inv)
    assert "mcp_config" in capsys.readouterr().err
