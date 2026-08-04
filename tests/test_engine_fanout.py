"""Engine execution: fan-out, retry, terminal collection, context isolation.

Uses a synthetic fan-out fixture (tests/fixtures/fanout.yaml) so these engine
capabilities are covered independently of any real workflow's shape.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import BaseModel

from workflow_ai import registry
from workflow_ai.backends.base import AgentInvocation
from workflow_ai.engine import Engine, WorkflowError, dump_run
from workflow_ai.graph import WorkflowGraph
from workflow_ai.models import WorkflowContext

from conftest import ScriptedBackend

FIXTURE = Path(__file__).parent / "fixtures" / "fanout.yaml"


# --- test-only registrations for the fan-out fixture -----------------------


@registry.schema("fo_split_out")
class _FoSplitOut(BaseModel):
    topic: str
    next_states: list[str]


@registry.schema("fo_leaf_out")
class _FoLeafOut(BaseModel):
    findings: list[str]


@registry.updater("fo_append")
def _fo_append(output: BaseModel, context: WorkflowContext) -> WorkflowContext:
    context.data.setdefault("findings", [])
    context.data["findings"].extend(output.findings)
    return context


def _responder(invocation: AgentInvocation) -> dict:
    name = invocation.schema.__name__
    if name == "_FoSplitOut":
        return {"topic": "t", "next_states": ["leaf_a", "leaf_b"]}
    if name == "_FoLeafOut":
        return {"findings": ["a finding"]}
    raise AssertionError(name)


def test_fanout_produces_two_terminal_branches():
    graph = WorkflowGraph.from_yaml(FIXTURE)
    engine = Engine(ScriptedBackend(_responder))
    result = engine.run(graph, "do the thing")

    assert len(result.branches) == 2
    assert {b.terminal_node for b in result.branches} == {"done"}


def test_branches_have_isolated_context():
    graph = WorkflowGraph.from_yaml(FIXTURE)
    engine = Engine(ScriptedBackend(_responder))
    result = engine.run(graph, "x")

    # Each branch only accumulated its own leaf's finding (1), proving the
    # deep-copied per-branch context rather than a shared list.
    for branch in result.branches:
        assert branch.context.data["findings"] == ["a finding"]


def test_retry_then_succeed():
    graph = WorkflowGraph.from_yaml(FIXTURE)
    state = {"split_fails": 2}

    def flaky(invocation: AgentInvocation) -> dict:
        if invocation.schema.__name__ == "_FoSplitOut" and state["split_fails"] > 0:
            state["split_fails"] -= 1
            return {"bad": "payload"}  # fails schema validation
        return _responder(invocation)

    engine = Engine(ScriptedBackend(flaky))
    result = engine.run(graph, "x", retries_override=3)
    assert len(result.branches) == 2  # eventually succeeded


def test_exhausted_retries_raises():
    graph = WorkflowGraph.from_yaml(FIXTURE)

    def always_bad(invocation: AgentInvocation) -> dict:
        if invocation.schema.__name__ == "_FoSplitOut":
            return {"nope": True}
        return _responder(invocation)

    engine = Engine(ScriptedBackend(always_bad))
    with pytest.raises(WorkflowError, match="failed after"):
        engine.run(graph, "x", retries_override=2)


def test_invalid_transition_rejected():
    graph = WorkflowGraph.from_yaml(FIXTURE)

    def bad_transition(invocation: AgentInvocation) -> dict:
        if invocation.schema.__name__ == "_FoSplitOut":
            return {"topic": "t", "next_states": ["ghost_node"]}  # not in `next`
        return _responder(invocation)

    engine = Engine(ScriptedBackend(bad_transition))
    with pytest.raises(WorkflowError, match="invalid state"):
        engine.run(graph, "x")


def test_dump_run_round_trips_unicode(tmp_path):
    """dump_run must persist non-ASCII (em-dash, arrows) on every OS."""

    graph = WorkflowGraph.from_yaml(FIXTURE)

    def unicode_responder(invocation: AgentInvocation) -> dict:
        name = invocation.schema.__name__
        if name == "_FoSplitOut":
            return {"topic": "DAG — déjà vu →", "next_states": ["leaf_a"]}
        if name == "_FoLeafOut":
            return {"findings": ["finding — αβγ"]}
        raise AssertionError(name)

    engine = Engine(ScriptedBackend(unicode_responder))
    result = engine.run(graph, "naïve prompt ñ")
    out = dump_run(result, tmp_path / "run")

    reloaded = json.loads((out / "result.json").read_text(encoding="utf-8"))
    assert reloaded["branches"][0]["context"]["data"]["topic"] == "DAG — déjà vu →"
