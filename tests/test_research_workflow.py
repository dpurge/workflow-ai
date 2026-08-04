"""research workflow: classify → deterministic gather → synthesize → report.

The backend is scripted; the evidence tools are stubbed via `_run_tool`, so the
test is hermetic (no subprocess, no network). Asserts RAG-first precedence, the
external fallthrough order, and the standard report shape.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from workflow_ai import research  # noqa: F401 (registers research logic)
from workflow_ai.backends.base import AgentInvocation
from workflow_ai.engine import Engine
from workflow_ai.graph import WorkflowGraph
from workflow_ai.research import definitions as defs

WORKFLOWS = Path(__file__).parent.parent / "src" / "workflow_ai"


def _responder(inv: AgentInvocation):
    name = inv.schema.__name__ if inv.schema is not None else None
    if name == "ClassifyOut":
        return {"topic": "Transformers", "rationale": "why", "depth": "quick"}
    if name == "SynthesizeOut":
        return {
            "title": "Transformers",
            "summary": "A short summary.",
            "sections": [{"heading": "Overview", "body": "Body text [k]."}],
            "open_questions": ["What next?"],
            "confidence": "High",
        }
    raise AssertionError(f"unexpected node: {name}")


def _install_tools(monkeypatch, canned: dict[str, list]):
    calls: list[str] = []

    def fake_run_tool(name, query, *extra):
        calls.append(name)
        return canned.get(name, [])

    monkeypatch.setattr(defs, "_run_tool", fake_run_tool)
    return calls


def _run(tmp_path, monkeypatch, canned):
    calls = _install_tools(monkeypatch, canned)
    graph = WorkflowGraph.from_yaml(WORKFLOWS / "research" / "workflow.yaml")
    engine = Engine(_ScriptedBackend())
    result = engine.run(
        graph,
        "Research transformers",
        initial_data={"as_of": "2026-08-04", "report_dir": str(tmp_path)},
    )
    return result, calls


class _ScriptedBackend:
    def run(self, invocation):
        from workflow_ai.backends.base import AgentResult
        import json

        payload = _responder(invocation)
        return AgentResult(text=json.dumps(payload), structured=payload, cost_usd=0.0)


def test_graph_is_valid():
    graph = WorkflowGraph.from_yaml(WORKFLOWS / "research" / "workflow.yaml")
    assert graph.name == "research" and graph.start == "classify"


def test_rag_takes_precedence_and_skips_web(tmp_path, monkeypatch):
    canned = {"rag-query.py": [{"file": "doc.md", "text": "Local chunk about X."}]}
    result, calls = _run(tmp_path, monkeypatch, canned)

    # only the RAG tool ran; the web tiers were skipped
    assert calls == ["rag-query.py"]
    findings = result.branches[0].context.data["findings"]
    assert findings and all(f["key"].startswith("local:") for f in findings)


def test_fallthrough_to_web_when_rag_empty(tmp_path, monkeypatch):
    canned = {
        "rag-query.py": [],
        "wikipedia-search.py": [{"title": "T", "summary": "Wiki summary.", "url": "http://w"}],
        "arxiv-search.py": [{"id": "2401.1", "abstract": "Abstract.", "pdf_url": "http://a"}],
        "web-search.py": [{"title": "W", "snippet": "Snippet.", "url": "http://web"}],
    }
    result, calls = _run(tmp_path, monkeypatch, canned)

    assert calls == ["rag-query.py", "wikipedia-search.py", "arxiv-search.py", "web-search.py"]
    keys = [f["key"] for f in result.branches[0].context.data["findings"]]
    assert keys == ["wiki:http://w", "arxiv:2401.1", "web:http://web"]


def test_report_has_standard_shape(tmp_path, monkeypatch):
    canned = {"rag-query.py": [{"file": "doc.md", "text": "Local chunk."}]}
    result, _ = _run(tmp_path, monkeypatch, canned)

    report_path = Path(result.branches[0].context.data["report_path"])
    assert report_path.name == "report.md"
    text = report_path.read_text(encoding="utf-8")
    assert text.startswith("# Transformers")
    assert "## Summary" in text
    assert "## Open questions" in text
    assert "## References" in text
    assert "[local:doc.md]" in text  # reference resolved from the finding key
    assert "_Confidence: High._" in text
