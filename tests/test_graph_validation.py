"""DAG load-time validation tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from workflow_ai import definitions  # noqa: F401
from workflow_ai.graph import GraphError, WorkflowGraph

WORKFLOWS = Path(__file__).parent.parent / "src" / "workflow_ai" / "workflows"


def test_sample_research_workflow_is_valid():
    graph = WorkflowGraph.from_yaml(WORKFLOWS / "research.yaml")
    assert graph.name == "research"
    assert graph.start == "classify"


def _write(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "wf.yaml"
    p.write_text(body, encoding="utf-8")
    return p


def test_rejects_unknown_transition_target(tmp_path):
    body = """
name: bad
start: a
nodes:
  a:
    role: r
    schema: gather_out
    next: {nowhere: "x"}
  end:
    role: r
    schema: report_out
    terminal: true
"""
    with pytest.raises(GraphError, match="unknown node 'nowhere'"):
        WorkflowGraph.from_yaml(_write(tmp_path, body))


def test_rejects_cycle(tmp_path):
    body = """
name: cyclic
start: a
nodes:
  a:
    role: r
    schema: gather_out
    next: {b: "to b"}
  b:
    role: r
    schema: gather_out
    next: {a: "back to a"}
  end:
    role: r
    schema: report_out
    terminal: true
"""
    with pytest.raises(GraphError, match="not acyclic"):
        WorkflowGraph.from_yaml(_write(tmp_path, body))


def test_rejects_unregistered_schema(tmp_path):
    body = """
name: bad
start: a
nodes:
  a:
    role: r
    schema: does_not_exist
    next: {end: "x"}
  end:
    role: r
    schema: report_out
    terminal: true
"""
    with pytest.raises(GraphError, match="unregistered schema"):
        WorkflowGraph.from_yaml(_write(tmp_path, body))


def test_requires_terminal(tmp_path):
    body = """
name: noterm
start: a
nodes:
  a:
    role: r
    schema: gather_out
    next: {a: "self"}
"""
    # self-loop is also a cycle; either error is acceptable, both are GraphError
    with pytest.raises(GraphError):
        WorkflowGraph.from_yaml(_write(tmp_path, body))
