"""Tests for OpenAIBackend.

The real 'openai' package is never installed in the test environment.
We inject a fake module into sys.modules at import time so that the
lazy `import openai` inside OpenAIBackend.__init__ resolves to our fake.
"""

from __future__ import annotations

import json
import sys
import types
from dataclasses import dataclass, field
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# Fake openai module — must be in sys.modules before any backend import.
# ---------------------------------------------------------------------------

_fake_openai = types.ModuleType("openai")
if "openai" not in sys.modules:
    sys.modules["openai"] = _fake_openai


# ---------------------------------------------------------------------------
# Fake response dataclasses
# ---------------------------------------------------------------------------


@dataclass
class FakeFunction:
    name: str
    arguments: str  # JSON string


@dataclass
class FakeToolCall:
    id: str
    function: FakeFunction


@dataclass
class FakeMessage:
    content: str | None
    tool_calls: list | None
    parsed: Any = None


@dataclass
class FakeChoice:
    message: FakeMessage


@dataclass
class FakeResponse:
    choices: list


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def make_fake_openai_client(responses: list, constructed: dict | None = None):
    """Return (FakeOpenAIClass, FakeAzureClass, captured_calls).

    Both classes share the same FakeCompletions so either path produces the
    same scripted responses.
    """
    resp_iter = iter(responses)
    captured_calls: list[dict] = []

    class FakeCompletions:
        def parse(self, **kwargs):
            captured_calls.append(kwargs)
            return next(resp_iter)

        def create(self, **kwargs):
            captured_calls.append(kwargs)
            return next(resp_iter)

    class FakeChat:
        completions = FakeCompletions()

    class FakeOpenAIClass:
        def __init__(self, **kw):
            if constructed is not None:
                constructed.update(kw)
            self.chat = FakeChat()

    class FakeAzureClass:
        def __init__(self, **kw):
            if constructed is not None:
                constructed.update(kw)
            self.chat = FakeChat()

    return FakeOpenAIClass, FakeAzureClass, captured_calls


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_text_node(monkeypatch):
    from workflow_ai.backends.base import AgentInvocation
    from workflow_ai.backends.openai_sdk import OpenAIBackend

    FakeOAI, FakeAZ, _ = make_fake_openai_client(
        [FakeResponse(choices=[FakeChoice(FakeMessage(content="hello", tool_calls=None))])]
    )
    sys.modules["openai"].OpenAI = FakeOAI
    sys.modules["openai"].AzureOpenAI = FakeAZ

    b = OpenAIBackend(model="m")
    inv = AgentInvocation(system_prompt="sys", prompt="user", output_kind="text")
    result = b.run(inv)
    assert result.text == "hello"
    assert result.structured is None


def test_json_node_parsed(monkeypatch):
    from pydantic import BaseModel

    from workflow_ai.backends.base import AgentInvocation
    from workflow_ai.backends.openai_sdk import OpenAIBackend

    class Out(BaseModel):
        value: str

    FakeOAI, FakeAZ, _ = make_fake_openai_client(
        [
            FakeResponse(
                choices=[
                    FakeChoice(
                        FakeMessage(
                            content=None,
                            tool_calls=None,
                            parsed=Out(value="world"),
                        )
                    )
                ]
            )
        ]
    )
    sys.modules["openai"].OpenAI = FakeOAI
    sys.modules["openai"].AzureOpenAI = FakeAZ

    b = OpenAIBackend(model="m")
    inv = AgentInvocation(
        system_prompt="sys", prompt="user", output_kind="json", schema=Out
    )
    result = b.run(inv)
    assert result.structured == {"value": "world"}


def test_json_node_parsed_none_fallback(monkeypatch):
    from pydantic import BaseModel

    from workflow_ai.backends.base import AgentInvocation
    from workflow_ai.backends.openai_sdk import OpenAIBackend

    class Out(BaseModel):
        value: str

    FakeOAI, FakeAZ, _ = make_fake_openai_client(
        [
            FakeResponse(
                choices=[
                    FakeChoice(
                        FakeMessage(
                            content='{"value":"x"}',
                            tool_calls=None,
                            parsed=None,
                        )
                    )
                ]
            )
        ]
    )
    sys.modules["openai"].OpenAI = FakeOAI
    sys.modules["openai"].AzureOpenAI = FakeAZ

    b = OpenAIBackend(model="m")
    inv = AgentInvocation(
        system_prompt="sys", prompt="user", output_kind="json", schema=Out
    )
    result = b.run(inv)
    assert result.structured is None
    assert result.text == '{"value":"x"}'


def test_tool_loop(monkeypatch):
    import workflow_ai.backends.tools as tools_mod

    from workflow_ai.backends.base import AgentInvocation
    from workflow_ai.backends.openai_sdk import OpenAIBackend

    tc = FakeToolCall("tc1", FakeFunction("Read", json.dumps({"path": "f.txt"})))
    FakeOAI, FakeAZ, captured = make_fake_openai_client(
        [
            FakeResponse(choices=[FakeChoice(FakeMessage(content=None, tool_calls=[tc]))]),
            FakeResponse(
                choices=[FakeChoice(FakeMessage(content="done", tool_calls=None))]
            ),
        ]
    )
    sys.modules["openai"].OpenAI = FakeOAI
    sys.modules["openai"].AzureOpenAI = FakeAZ
    monkeypatch.setattr(tools_mod, "dispatch", lambda tools, name, args: "file data")

    b = OpenAIBackend(model="m")
    inv = AgentInvocation(
        system_prompt="sys",
        prompt="user",
        output_kind="text",
        allowed_tools=["Read"],
    )
    result = b.run(inv)
    assert result.text == "done"

    second_msgs = captured[1]["messages"]
    assert any(
        m.get("role") == "tool" for m in second_msgs if isinstance(m, dict)
    )


def test_azure_constructor():
    from workflow_ai.backends.openai_sdk import OpenAIBackend

    constructed: dict = {}
    FakeOAI, FakeAZ, _ = make_fake_openai_client([], constructed=constructed)
    sys.modules["openai"].OpenAI = FakeOAI
    sys.modules["openai"].AzureOpenAI = FakeAZ

    OpenAIBackend(
        azure_endpoint="https://myres.openai.azure.com",
        api_version="2024-10-21",
        model="dep1",
        api_key="k",
    )
    assert constructed.get("azure_endpoint") == "https://myres.openai.azure.com"
    assert constructed.get("api_version") == "2024-10-21"


def test_loop_exhaustion_raises():
    import workflow_ai.backends.tools as tools_mod

    from workflow_ai.backends.base import AgentInvocation, AgentOutputError
    from workflow_ai.backends.openai_sdk import OpenAIBackend

    tc = FakeToolCall("tc1", FakeFunction("Read", json.dumps({"path": "x"})))

    class InfiniteCompletions:
        def create(self, **kwargs):
            return FakeResponse(
                choices=[FakeChoice(FakeMessage(content=None, tool_calls=[tc]))]
            )

        def parse(self, **kwargs):
            return FakeResponse(
                choices=[FakeChoice(FakeMessage(content=None, tool_calls=[tc]))]
            )

    class FakeOAI:
        def __init__(self, **kw):
            self.chat = type("C", (), {"completions": InfiniteCompletions()})()

    sys.modules["openai"].OpenAI = FakeOAI

    # Patch dispatch so tool calls don't hit the filesystem.
    _orig_dispatch = tools_mod.dispatch
    tools_mod.dispatch = lambda tools, name, args: "content"
    try:
        b = OpenAIBackend(model="m")
        inv = AgentInvocation(
            system_prompt="s",
            prompt="p",
            output_kind="text",
            allowed_tools=["Read"],
            max_turns=2,
        )
        with pytest.raises(AgentOutputError, match="exceeded"):
            b.run(inv)
    finally:
        tools_mod.dispatch = _orig_dispatch


def test_missing_model_raises():
    from workflow_ai.backends.base import AgentInvocation, AgentOutputError
    from workflow_ai.backends.openai_sdk import OpenAIBackend

    class FakeOAI:
        def __init__(self, **kw):
            self.chat = None

    sys.modules["openai"].OpenAI = FakeOAI

    b = OpenAIBackend(model=None)
    inv = AgentInvocation(
        system_prompt="s", prompt="p", output_kind="text", model=None
    )
    with pytest.raises(AgentOutputError):
        b.run(inv)


def test_mcp_config_warns(capsys):
    from workflow_ai.backends.base import AgentInvocation
    from workflow_ai.backends.openai_sdk import OpenAIBackend

    FakeOAI, FakeAZ, _ = make_fake_openai_client(
        [FakeResponse(choices=[FakeChoice(FakeMessage(content="ok", tool_calls=None))])]
    )
    sys.modules["openai"].OpenAI = FakeOAI
    sys.modules["openai"].AzureOpenAI = FakeAZ

    b = OpenAIBackend(model="m")
    inv = AgentInvocation(
        system_prompt="s", prompt="p", output_kind="text", mcp_config="x"
    )
    b.run(inv)
    assert "mcp_config" in capsys.readouterr().err
