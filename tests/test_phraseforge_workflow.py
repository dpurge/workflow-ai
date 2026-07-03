"""phraseforge workflow: DAG, script-branch router, text/action nodes, save assembly.

The backend is scripted and the external Pi mdx-export.py is stubbed, so the test
is hermetic (no network, no live agent, no uv/jinja).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from workflow_ai import lessons  # noqa: F401 (registers phraseforge logic)
from workflow_ai.backends.base import AgentInvocation
from workflow_ai.engine import Engine
from workflow_ai.graph import WorkflowGraph
from workflow_ai.lessons import definitions as defs

from conftest import ScriptedBackend

WORKFLOWS = Path(__file__).parent.parent / "src" / "workflow_ai" / "workflows"


def _responder_for(script: str):
    """Build a scripted responder; `script` drives the detect node's branch."""

    def responder(inv: AgentInvocation):
        if inv.schema is not None:  # json node
            name = inv.schema.__name__
            if name == "DetectOut":
                return {"language": "deu", "script": script, "title": "Lekcja"}
            if name == "VocabularyList":
                return [{"headword": f"w{i}", "translation": f"t{i}"} for i in range(12)]
            if name == "ModelList":
                return [{"pattern": f"p{i}", "translation": f"t{i}"} for i in range(4)]
            if name == "QuestionList":
                return [f"Frage {i}?" for i in range(4)]
            if name == "ExerciseList":
                return [
                    {"type": "translation", "instruction": "Przetłumacz", "items": ["a"]},
                    {"type": "fill-gaps", "instruction": "Uzupełnij", "items": ["b ___"]},
                    {"type": "word-order", "instruction": "Ułóż", "items": ["a / b"]},
                    {"type": "multiple-choice", "instruction": "Wybierz", "items": ["x"]},
                ]
            raise AssertionError(name)
        # text node — branch on the prompt
        p = inv.prompt
        if p.startswith("Source:"):
            return "Cleaned German text."
        if p.startswith("Transcribe"):
            return "romanized"
        if p.startswith("Translate to Polish"):
            return "Polskie tłumaczenie."
        if p.startswith("Explain the key grammar"):
            return "## Gramatyka\nProsta."
        raise AssertionError(f"unexpected text prompt: {p[:40]}")

    return responder


@pytest.fixture
def stub_export(monkeypatch, tmp_path):
    """Stub the external mdx-export.py call; capture the assembled lesson JSON."""

    captured: dict = {}
    fake_export = tmp_path / "mdx-export.py"
    fake_export.write_text("# stub", encoding="utf-8")

    monkeypatch.setattr(defs, "_MDX_EXPORT", fake_export)
    monkeypatch.setattr(defs.shutil, "which", lambda _e: "/usr/bin/true")

    class _Proc:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(argv, input=None, **kwargs):
        captured["lesson"] = json.loads(input)
        captured["argv"] = argv
        # mimic the exporter writing the --out file
        out = Path(argv[argv.index("--out") + 1])
        out.write_text("---\ntitle: stub\n---\n", encoding="utf-8")
        return _Proc()

    monkeypatch.setattr(defs.subprocess, "run", fake_run)
    return captured


def _run(tmp_path, script: str, stub_export):
    src = tmp_path / "article.txt"
    src.write_text("Ein deutscher Artikel über etwas Wichtiges.", encoding="utf-8")
    graph = WorkflowGraph.from_yaml(WORKFLOWS / "phraseforge.yaml")
    engine = Engine(ScriptedBackend(_responder_for(script)))
    return engine.run(
        graph,
        "",
        initial_data={
            "source_ref": {"kind": "file", "value": str(src)},
            "level": "b1",
            "translation_lang": "pol",
            "cwd": str(tmp_path),
        },
    )


def test_graph_is_valid():
    graph = WorkflowGraph.from_yaml(WORKFLOWS / "phraseforge.yaml")
    assert graph.name == "phraseforge" and graph.start == "read"


def test_latin_script_skips_transcription(tmp_path, stub_export):
    result = _run(tmp_path, "latn", stub_export)
    assert len(result.branches) == 1
    nodes = [r.node_id for r in result.branches[0].context.history]
    assert "transcribe" not in nodes
    assert nodes[:3] == ["read", "clean", "detect"]
    assert nodes[-1] == "save"


def test_nonlatin_script_runs_transcription(tmp_path, stub_export):
    result = _run(tmp_path, "arab", stub_export)
    nodes = [r.node_id for r in result.branches[0].context.history]
    assert "transcribe" in nodes


def test_save_assembles_valid_lesson(tmp_path, stub_export):
    _run(tmp_path, "latn", stub_export)
    lesson = stub_export["lesson"]
    assert lesson["version"] == 1
    assert lesson["lang"] == "deu" and lesson["script"] == "latn"
    assert lesson["translation_lang"] == "pol"
    assert lesson["level"] == "b1"
    assert lesson["source"] == {"kind": "text", "content": "Cleaned German text."}
    assert len(lesson["vocabulary"]) == 12
    assert len(lesson["exercises"]) == 4
    assert lesson["translation"] == "Polskie tłumaczenie."
    # latn => no transcription field assembled
    assert "transcription" not in lesson
    # output path follows docs/<lang>/<level>/<date>-<seq>.mdx
    out = Path(stub_export["argv"][stub_export["argv"].index("--out") + 1])
    assert out.parts[-3:-1] == ("deu", "b1") and out.name.endswith("-a.mdx")
