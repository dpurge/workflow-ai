"""RunLogger: format, stdout routing, file routing, context-manager cleanup."""

from __future__ import annotations

import json
from io import StringIO
from pathlib import Path

import pytest

from workflow_ai.logger import RunLogger, _format_line


# ---------------------------------------------------------------------------
# _format_line
# ---------------------------------------------------------------------------


def test_format_enter():
    line = _format_line("enter", "classify", {})
    assert "[enter" in line
    assert "classify" in line


def test_format_attempt():
    line = _format_line("attempt", "classify", {"attempt": 2, "retries": 3})
    assert "2/3" in line


def test_format_retry():
    line = _format_line("retry", "classify", {"attempt": 1, "error": "schema mismatch"})
    assert "schema mismatch" in line
    assert "attempt 1" in line


def test_format_output():
    raw = {"topic": "coffee", "next_state": "search_web"}
    line = _format_line("output", "classify", {"raw_output": raw})
    assert "coffee" in line
    assert json.loads(line[line.index("{"):]) == raw


def test_format_context():
    data = {"topic": "tea"}
    line = _format_line("context", "classify", {"context_data": data})
    assert "tea" in line


def test_format_transition():
    line = _format_line("transition", "classify", {"successors": ["search_web", "read_files"]})
    assert "search_web" in line
    assert "read_files" in line


def test_format_terminal():
    line = _format_line("terminal", "done", {"context_data": {"result": "ok"}})
    assert "done" in line
    assert "ok" in line


# ---------------------------------------------------------------------------
# RunLogger routing
# ---------------------------------------------------------------------------


def test_verbose_writes_to_stdout(monkeypatch):
    captured = StringIO()
    monkeypatch.setattr("workflow_ai.logger.sys.stdout", captured)
    with RunLogger(verbose=True) as logger:
        logger("enter", "classify", {})
    assert "classify" in captured.getvalue()


def test_non_verbose_no_stdout(monkeypatch):
    captured = StringIO()
    monkeypatch.setattr("workflow_ai.logger.sys.stdout", captured)
    with RunLogger(verbose=False) as logger:
        logger("enter", "classify", {})
    assert captured.getvalue() == ""


def test_log_file_written(tmp_path):
    log = tmp_path / "run.log"
    with RunLogger(verbose=False, log_file=log) as logger:
        logger("enter", "classify", {})
        logger("output", "classify", {"raw_output": {"x": 1}})
    lines = log.read_text().splitlines()
    assert len(lines) == 2
    assert "classify" in lines[0]
    assert '"x": 1' in lines[1]


def test_log_file_and_verbose_both_written(tmp_path, monkeypatch):
    captured = StringIO()
    monkeypatch.setattr("workflow_ai.logger.sys.stdout", captured)
    log = tmp_path / "run.log"
    with RunLogger(verbose=True, log_file=log) as logger:
        logger("enter", "classify", {})
    assert "classify" in captured.getvalue()
    assert "classify" in log.read_text()


def test_log_file_only_no_stdout(tmp_path, monkeypatch):
    captured = StringIO()
    monkeypatch.setattr("workflow_ai.logger.sys.stdout", captured)
    log = tmp_path / "run.log"
    with RunLogger(verbose=False, log_file=log) as logger:
        logger("enter", "classify", {})
    assert captured.getvalue() == ""
    assert "classify" in log.read_text()


def test_log_file_creates_parent_dirs(tmp_path):
    log = tmp_path / "deep" / "nested" / "run.log"
    with RunLogger(verbose=False, log_file=log) as logger:
        logger("enter", "n", {})
    assert log.exists()


def test_close_is_idempotent(tmp_path):
    log = tmp_path / "run.log"
    logger = RunLogger(verbose=False, log_file=log)
    logger.close()
    logger.close()  # must not raise


def test_data_defaults_to_empty_dict(monkeypatch):
    captured = StringIO()
    monkeypatch.setattr("workflow_ai.logger.sys.stdout", captured)
    with RunLogger(verbose=True) as logger:
        logger("enter", "n")  # no data arg
    assert "n" in captured.getvalue()


# ---------------------------------------------------------------------------
# Engine emits events (integration with fake backend)
# ---------------------------------------------------------------------------


def test_engine_emits_all_events(tmp_path):
    """Engine fires enter/attempt/output/context/transition/terminal for a 2-node graph."""
    from unittest.mock import MagicMock
    from workflow_ai.engine import Engine
    from workflow_ai.graph import WorkflowGraph
    from workflow_ai.backends.base import AgentResult
    from workflow_ai import registry
    from pydantic import BaseModel
    from typing import Literal

    @registry.schema("_TestOut")
    class _TestOut(BaseModel):
        next_state: Literal["done"]

    graph_yaml = """
name: test
start: step
nodes:
  step:
    role: tester
    prompt: go
    schema: _TestOut
    next:
      done: finish
  done:
    terminal: true
"""
    yaml_file = tmp_path / "test_wf.yaml"
    yaml_file.write_text(graph_yaml, encoding="utf-8")
    graph = WorkflowGraph.from_yaml(yaml_file)

    backend = MagicMock()
    backend.run.return_value = AgentResult(
        text='{"next_state": "done"}',
        structured={"next_state": "done"},
    )
    engine = Engine(backend)

    events: list[tuple[str, str]] = []

    def on_event(kind, node_id, data=None):
        events.append((kind, node_id))

    engine.run(graph, "test prompt", on_event=on_event)

    kinds = [e[0] for e in events]
    assert "enter" in kinds
    assert "attempt" in kinds
    assert "output" in kinds
    assert "context" in kinds
    assert "transition" in kinds
    assert "terminal" in kinds
