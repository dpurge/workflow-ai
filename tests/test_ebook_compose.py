"""ebook compose-from-topic path: plan_queries → gather → compose → grounded lesson.

Backend is scripted; the search runner and the politeness sleep are stubbed, so
the test is hermetic (no network, no delays). Evidence and composed text share
vocabulary so the grounding gate passes.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from workflow_ai import ebook  # noqa: F401 (registers ebook logic)
from workflow_ai.backends.base import AgentInvocation
from workflow_ai.ebook import definitions as defs
from workflow_ai.ebook.schemas import ComposeOut
from workflow_ai.engine import Engine
from workflow_ai.graph import WorkflowGraph
from workflow_ai.models import WorkflowContext

from conftest import ScriptedBackend

WORKFLOWS = Path(__file__).parent.parent / "src" / "workflow_ai"

# 6 core evidence words repeated → guarantees length ≥200 and full overlap.
GROUNDED_TEXT = ("Kaffee Getränk Koffein Aroma Bohne Tasse. " * 8).strip()


def _project(tmp_path: Path, *, language="pol", script="latn") -> Path:  # book/reader language
    p = tmp_path / "ebook.yml"
    p.write_text(
        yaml.safe_dump(
            {"identifier": "id", "filename": "t.epub", "title": "T",
             "language": language, "script": script, "text": [["section.md", "01.md"]]},
            allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    return p


def _stub_lang(tmp_path, monkeypatch, langs=("deu",)):
    root = tmp_path / "lang-skills"
    for name in langs:
        d = root / f"phraseforge-lang-{name}"
        d.mkdir(parents=True, exist_ok=True)
        (d / "SKILL.md").write_text("# stub\n", encoding="utf-8")
    monkeypatch.setattr(defs, "_LANG_SKILLS_DIR", root)


def _stub_search(monkeypatch):
    """Return varied evidence sharing the 6 core words; record call count."""
    calls = {"n": 0}

    def fake(name, query, *extra):
        calls["n"] += 1
        return [
            {"snippet": f"Kaffee Getränk Koffein Aroma Bohne Tasse Beispiel {query} {i}.",
             "summary": f"Kaffee Getränk Koffein Aroma Bohne Tasse — {query} {i}.",
             "title": "T", "url": f"http://e/{query}/{i}"}
            for i in range(5)
        ]

    monkeypatch.setattr(defs, "_run_search_tool", fake)
    monkeypatch.setattr(defs.time, "sleep", lambda *a, **k: None)  # no delays in tests
    return calls


def _responder(form: str, turns=None):
    def responder(inv: AgentInvocation):
        if inv.schema is not None:
            name = inv.schema.__name__
            if name == "QueriesOut":
                return {"queries": [f"kaffee thema {i}" for i in range(22)], "rationale": "r"}
            if name == "ComposeOut":
                out = {"form": form, "title": "Kawa", "text": GROUNDED_TEXT}
                if form == "dialog":
                    out["turns"] = turns or [
                        {"speaker": "A", "text": "Kaffee Getränk Koffein.",
                         "translation": "Kawa napój kofeina."},
                        {"speaker": None, "text": "Aroma Bohne Tasse.",
                         "translation": "Aromat ziarno filiżanka."},
                    ]
                return out
            if name == "DetectOut":
                return {"language": "deu", "script": "latn", "title": "Kawa"}
            if name == "VocabularyList":
                return [{"headword": f"w{i}", "translation": f"t{i}"} for i in range(12)]
            if name == "ModelList":
                return [{"pattern": f"p{i}", "translation": f"t{i}"} for i in range(4)]
            if name == "QuestionList":
                return [f"q{i}?" for i in range(4)]
            raise AssertionError(name)
        p = inv.prompt
        if p.startswith("Translate to"):
            return "Tłumaczenie."
        if p.startswith("Explain the key grammar"):
            return "## Gramatyka"
        raise AssertionError(p[:40])

    return responder


def _run(tmp_path, monkeypatch, *, form="text"):
    _stub_lang(tmp_path, monkeypatch)
    calls = _stub_search(monkeypatch)
    ebook_yml = _project(tmp_path)
    graph = WorkflowGraph.from_yaml(WORKFLOWS / "ebook" / "workflow.yaml")
    engine = Engine(ScriptedBackend(_responder(form)))
    result = engine.run(
        graph,
        "Coffee culture",  # topic via --prompt; NO source_ref → compose path
        initial_data={"ebook_yml": str(ebook_yml), "kind": "lang",
                      "language": "deu", "script": "latn",  # learning target (--lang/--script)
                      "form": form, "level": "a2", "max_searches": 24, "wire": False},
    )
    return result, calls


def test_compose_path_runs_and_renders(tmp_path, monkeypatch):
    result, calls = _run(tmp_path, monkeypatch, form="text")
    nodes = [r.node_id for r in result.branches[0].context.history]
    # compose sub-path taken (no --source), converging into the normal pipeline
    assert nodes[:4] == ["orient", "plan_queries", "gather_evidence", "compose"]
    assert "detect" in nodes and nodes[-1] == "render"
    # 22 planned web searches + 1 target-language Wikipedia call were issued
    assert calls["n"] >= 23
    chapter = Path(result.branches[0].context.data["out_path"]).read_text(encoding="utf-8")
    assert "{start-text as=source lang=deu script=latn}" in chapter  # target
    assert "{start-text as=translation lang=pol script=latn}" in chapter  # book/reader
    assert "Kaffee Getränk Koffein" in chapter


def test_compose_dialog_form_renders_dialog_fence(tmp_path, monkeypatch):
    result, _ = _run(tmp_path, monkeypatch, form="dialog")
    ctx = result.branches[0].context
    assert ctx.data["form"] == "dialog"
    chapter = Path(ctx.data["out_path"]).read_text(encoding="utf-8")
    assert "{start-dialog lang=deu script=latn}" in chapter          # source dialog (target)
    assert "@A:" in chapter
    assert "--:" in chapter  # the no-speaker turn renders as `--:`
    assert "  Kaffee Getränk Koffein." in chapter  # body indented exactly 2 spaces
    assert "{start-text as=source" not in chapter
    # the translation is a PARALLEL dialog (book/reader language), not a prose block
    assert "{start-dialog as=translation lang=pol script=latn}" in chapter
    assert "  Kawa napój kofeina." in chapter
    assert "{start-text as=translation" not in chapter


def test_evidence_gathered_from_stub(tmp_path, monkeypatch):
    result, _ = _run(tmp_path, monkeypatch, form="text")
    evidence = result.branches[0].context.data["evidence"]
    assert len(evidence) >= defs._EVIDENCE_MIN
    assert result.branches[0].context.data["evidence_text"]


def test_ground_check_rejects_ungrounded_and_accepts_grounded():
    ctx = WorkflowContext(initial_prompt="x")
    ctx.data["evidence_text"] = "Kaffee Getränk Koffein Aroma Bohne Tasse Beispiel"

    grounded = ComposeOut(form="text", text=GROUNDED_TEXT)
    assert defs.ground_check(grounded, ctx).ok

    # text made of words absent from the evidence → rejected (memory-generation)
    ungrounded = ComposeOut(
        form="text",
        text=("Zebra elephant giraffe mountain window bicycle carpet thunder. " * 5).strip(),
    )
    assert not defs.ground_check(ungrounded, ctx).ok

    # too short → rejected regardless of overlap
    short = ComposeOut(form="text", text="Kaffee Getränk.")
    assert not defs.ground_check(short, ctx).ok


def test_ground_check_requires_phraseology_not_just_vocabulary():
    """Reusing the evidence VOCABULARY but not its WORD-PAIRS (phraseology) — the
    signature of on-topic memory generation — must be rejected by the bigram gate."""
    ctx = WorkflowContext(initial_prompt="x")
    ctx.data["evidence_text"] = (
        "Kaffee Getränk Koffein Aroma Bohne Tasse Rösten Wasser Milch Zucker Löffel Bohnen"
    )
    # same words, reversed order → high vocab overlap, ~zero word-pair overlap
    shuffled = (
        "Bohnen Löffel Zucker Milch Wasser Rösten Tasse Bohne Aroma Koffein Getränk Kaffee. " * 3
    ).strip()
    assert not defs.ground_check(ComposeOut(form="text", text=shuffled), ctx).ok


def test_ground_check_length_floor_is_level_aware():
    """A short-but-grounded A1 lesson passes; the same text is 'too short' when the
    level is unset (higher default floor). Overlap is held constant (grounded)."""
    ctx = WorkflowContext(initial_prompt="x")
    ctx.data["evidence_text"] = (
        "Kaffee Getränk Koffein Aroma Bohne Tasse Rösten Wasser Milch Zucker"
    )
    text = (
        "Kaffee Getränk Koffein Aroma Bohne. Tasse Rösten Wasser Milch Zucker. "
        "Kaffee Getränk Koffein Aroma Bohne Tasse."
    )
    assert 90 <= len(text) < 150, len(text)  # self-check: in the a1..default gap
    out = ComposeOut(form="text", text=text)

    ctx.data["level"] = "a1"
    assert defs.ground_check(out, ctx).ok            # A1 floor (90) → passes

    ctx.data.pop("level")
    result = defs.ground_check(out, ctx)             # default floor (150) → too short
    assert not result.ok and "too short" in result.errors[0]


def test_ground_check_dialog_relaxes_phraseology():
    """A dialog reuses the evidence VOCABULARY but restructures word-pairs, so a
    lesson that fails the text-form phraseology floor (25%) passes as a dialog (8%).
    Vocabulary grounding still applies (dialog unigram floor 40%)."""
    ctx = WorkflowContext(initial_prompt="x")
    ctx.data["level"] = "a1"
    ctx.data["evidence_text"] = "mere pere lapte paine branza cafea zahar faina carne peste bani plata"
    text = (
        "cafea zahar mere carne pere faina paine peste lapte bani plata branza "
        "cafea mere zahar faina lapte peste"
    )
    # self-check: high vocabulary overlap, phraseology in the (dialog-pass / text-fail) band
    c_uni, c_bi = defs._units(text, use_chars=False)
    e_uni, e_bi = defs._units(ctx.data["evidence_text"], use_chars=False)
    uni = len(c_uni & e_uni) / len(c_uni)
    bi = len(c_bi & e_bi) / len(c_bi)
    assert uni >= 0.40 and 0.08 <= bi < 0.25, (uni, bi, len(text))

    out = ComposeOut(form="text", text=text)
    ctx.data["form"] = "text"
    assert not defs.ground_check(out, ctx).ok   # text: needs 25% word-pair overlap
    ctx.data["form"] = "dialog"
    assert defs.ground_check(out, ctx).ok        # dialog: 8% floor → passes


def test_no_grounding_skips_the_gate():
    ctx = WorkflowContext(initial_prompt="x")
    ctx.data["evidence_text"] = "Kaffee Getränk Koffein Aroma Bohne Tasse"
    ungrounded = ComposeOut(
        form="text",
        text=("Zebra elephant giraffe mountain window bicycle carpet thunder. " * 5).strip(),
    )
    assert not defs.ground_check(ungrounded, ctx).ok      # normally rejected
    ctx.data["grounding"] = False
    assert defs.ground_check(ungrounded, ctx).ok          # --no-grounding → gate skipped


def test_queries_min_verifier():
    from workflow_ai.ebook.schemas import QueriesOut

    ctx = WorkflowContext(initial_prompt="x")
    assert defs.queries_min(QueriesOut(queries=[f"q{i}" for i in range(20)]), ctx).ok
    assert not defs.queries_min(QueriesOut(queries=["q1", "q2"]), ctx).ok
