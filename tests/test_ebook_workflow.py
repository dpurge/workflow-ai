"""ebook workflow: orient → language/generic branch → in-repo fenced render.

The backend is scripted; the renderer is in-repo (no external tool), so the test
is hermetic. `@lang` skills are stubbed under a temp dir.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from workflow_ai import ebook  # noqa: F401 (registers ebook logic)
from workflow_ai.backends.base import AgentInvocation
from workflow_ai.ebook import definitions as defs
from workflow_ai.engine import Engine
from workflow_ai.graph import WorkflowGraph

from conftest import ScriptedBackend

WORKFLOWS = Path(__file__).parent.parent / "src" / "workflow_ai"


def _responder_for(lang: str, script: str):
    """Scripted responder driving one language-lesson run."""

    def responder(inv: AgentInvocation):
        if inv.schema is not None:  # json node
            name = inv.schema.__name__
            if name == "DetectOut":
                return {"language": lang, "script": script, "title": "Lekcja"}
            if name == "VocabularyList":
                return {"entries": [{"phrase": f"w{i}", "translation": f"t{i}"} for i in range(12)]}
            if name == "ModelList":
                return {"entries": [{"phrase": f"p{i}", "translation": f"t{i}"} for i in range(4)]}
            if name == "QuestionList":
                return {"entries": [f"Frage {i}?" for i in range(4)]}
            raise AssertionError(name)
        p = inv.prompt
        if p.startswith("Source:"):
            return "# Tytuł\n\n**Cleaned** source text."
        if p.startswith("Transcribe"):
            return "# Romanized\n\n**romanized**"
        if p.startswith("Translate to"):
            return "# Tłumaczenie\n\n**Przekład**."
        if p.startswith("Explain the key grammar"):
            return "# Gramatyka\n\nProsta."
        if p.startswith("Book language"):  # generic gen_write
            return "# Generic Chapter\n\nProse body with a source [web:example]."
        raise AssertionError(f"unexpected text prompt: {p[:40]}")

    return responder


def _project(tmp_path: Path, *, language: str, script: str, chapters: list[str]) -> Path:
    section = chapters[0] if chapters else "section.md"
    ebook_yml = tmp_path / "ebook.yml"
    ebook_yml.write_text(
        yaml.safe_dump(
            {
                "identifier": "test-id",
                "filename": "test.epub",
                "title": "Test Book",
                "language": language,
                "script": script,
                "text": [chapters] if chapters else [[section]],
            },
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return ebook_yml


def _stub_lang_skills(tmp_path: Path, monkeypatch, langs: list[str]) -> None:
    root = tmp_path / "lang-skills"
    for name in langs:
        d = root / f"phraseforge-lang-{name}"
        d.mkdir(parents=True, exist_ok=True)
        (d / "SKILL.md").write_text(f"# stub skill for {name}\n", encoding="utf-8")
    monkeypatch.setattr(defs, "_LANG_SKILLS_DIR", root)


def _run(tmp_path, monkeypatch, *, language="deu", script="latn", kind="lang",
         book_lang="pol", book_script="latn", wire=True):
    # `language`/`script` = learning target (--lang/--script); ebook.yml carries a
    # DISTINCT book/reader language (book_lang).
    _stub_lang_skills(tmp_path, monkeypatch, [language])
    ebook_yml = _project(tmp_path, language=book_lang, script=book_script, chapters=["section.md", "01.md"])
    src = tmp_path / "article.txt"
    src.write_text("Ein Artikel.", encoding="utf-8")

    graph = WorkflowGraph.from_yaml(WORKFLOWS / "ebook" / "workflow.yaml")
    engine = Engine(ScriptedBackend(_responder_for(language, script)))
    data = {
        "ebook_yml": str(ebook_yml),
        "source_ref": {"kind": "file", "value": str(src)},
        "level": "b1",
        "wire": wire,
    }
    if kind:
        data["kind"] = kind
    if kind == "lang":
        data["language"] = language  # learning target (--lang)
        data["script"] = script      # learning target (--script)
    return engine.run(graph, "Write about food", initial_data=data), ebook_yml


def test_graph_is_valid():
    graph = WorkflowGraph.from_yaml(WORKFLOWS / "ebook" / "workflow.yaml")
    assert graph.name == "ebook" and graph.start == "orient"


def test_language_branch_renders_fenced_chapter(tmp_path, monkeypatch):
    result, ebook_yml = _run(tmp_path, monkeypatch)
    nodes = [r.node_id for r in result.branches[0].context.history]
    assert nodes[:3] == ["orient", "read", "clean"]
    assert nodes[-1] == "render"

    out_path = Path(result.branches[0].context.data["out_path"])
    chapter = out_path.read_text(encoding="utf-8")
    assert out_path.parent.name == "b1"
    assert out_path.name == "001.md"
    assert chapter.startswith("# b1-001")
    assert "{start-vocabulary lang=deu script=latn}" in chapter
    assert "{start-models lang=deu script=latn}" in chapter
    assert "{start-text as=source lang=deu script=latn}" in chapter
    assert "{start-text as=translation lang=pol script=latn}" in chapter
    assert "{start-text as=grammar lang=pol script=latn}" in chapter
    assert "{start-questions" in chapter
    assert "\n## Tytuł\n" in chapter
    assert "\n## Tłumaczenie\n" in chapter
    assert "\n## Gramatyka\n" in chapter
    # ebook format has NO exercise block
    assert "exercise" not in chapter


def test_target_and_book_languages_are_separated(tmp_path, monkeypatch):
    # book/reader = Hebrew (non-Latin script); learning target = Arabic
    result, _ = _run(tmp_path, monkeypatch, language="arb", script="arab",
                     book_lang="heb", book_script="hebr")
    chapter = Path(result.branches[0].context.data["out_path"]).read_text(encoding="utf-8")
    # target content carries the TARGET language/script
    assert "{start-vocabulary lang=arb script=arab}" in chapter
    assert "{start-models lang=arb script=arab}" in chapter
    assert "{start-text as=source lang=arb script=arab}" in chapter
    # romanization is always Latin, tagged with the target language
    assert "{start-text as=transcription lang=arb script=latn}" in chapter
    # reader-facing content carries the BOOK language/script (incl. book script)
    assert "{start-text as=translation lang=heb script=hebr}" in chapter
    assert "{start-text as=grammar lang=heb script=hebr}" in chapter


def test_language_branch_wires_into_ebook_yml(tmp_path, monkeypatch):
    result, ebook_yml = _run(tmp_path, monkeypatch, wire=True)
    assert result.branches[0].context.data["wired"] is True
    project = yaml.safe_load(ebook_yml.read_text(encoding="utf-8"))
    assert "b1/001.md" in project["text"][0]


def test_no_wire_leaves_ebook_yml_untouched(tmp_path, monkeypatch):
    result, ebook_yml = _run(tmp_path, monkeypatch, wire=False)
    assert result.branches[0].context.data["wired"] is False
    project = yaml.safe_load(ebook_yml.read_text(encoding="utf-8"))
    assert "b1/001.md" not in project["text"][0]


def test_latin_script_skips_transcription(tmp_path, monkeypatch):
    result, _ = _run(tmp_path, monkeypatch, language="deu", script="latn")
    nodes = [r.node_id for r in result.branches[0].context.history]
    assert "transcribe" not in nodes


def test_nonlatin_script_runs_transcription(tmp_path, monkeypatch):
    result, _ = _run(tmp_path, monkeypatch, language="arb", script="arab")
    nodes = [r.node_id for r in result.branches[0].context.history]
    assert "transcribe" in nodes
    chapter = Path(result.branches[0].context.data["out_path"]).read_text(encoding="utf-8")
    assert "{start-text as=transcription lang=arb script=latn}" in chapter
    assert "\n## Romanized\n" in chapter
    assert "**romanized**" in chapter


def test_store_detect_does_not_clobber_book_script(tmp_path, monkeypatch):
    """Book is arb/arab; the model mis-detects the excerpt as eng/latn. The book's
    authoritative script must survive (drives transcription routing + fences)."""
    _stub_lang_skills(tmp_path, monkeypatch, ["arb"])
    # book/reader = Polish; learning target = Arabic (passed via initial_data below)
    ebook_yml = _project(tmp_path, language="pol", script="latn", chapters=["section.md", "01.md"])
    src = tmp_path / "a.txt"
    src.write_text("نص عربي.", encoding="utf-8")

    def responder(inv: AgentInvocation):
        if inv.schema is not None:
            name = inv.schema.__name__
            if name == "DetectOut":
                return {"language": "eng", "script": "latn", "title": "T"}  # wrong on purpose
            if name == "VocabularyList":
                return {"entries": [{"phrase": f"w{i}", "translation": f"t{i}"} for i in range(12)]}
            if name == "ModelList":
                return {"entries": [{"phrase": f"p{i}", "translation": f"t{i}"} for i in range(4)]}
            if name == "QuestionList":
                return {"entries": [f"q{i}?" for i in range(4)]}
            raise AssertionError(name)
        p = inv.prompt
        if p.startswith("Source:"):
            return "cleaned"
        if p.startswith("Transcribe"):
            return "romanized"
        if p.startswith("Translate to"):
            return "tłum"
        if p.startswith("Explain the key grammar"):
            return "## G"
        raise AssertionError(p[:30])

    graph = WorkflowGraph.from_yaml(WORKFLOWS / "ebook" / "workflow.yaml")
    engine = Engine(ScriptedBackend(responder))
    result = engine.run(
        graph,
        "x",
        initial_data={
            "ebook_yml": str(ebook_yml),
            "kind": "lang",
            "language": "arb",   # learning target (--lang)
            "script": "arab",    # learning target (--script)
            "source_ref": {"kind": "file", "value": str(src)},
            "wire": False,
        },
    )
    ctx = result.branches[0].context
    nodes = [r.node_id for r in ctx.history]
    # routed on the TARGET script (arab) → transcription runs, despite detect saying latn
    assert "transcribe" in nodes
    # the authoritative target values survived store_detect; detect landed on src_*
    assert ctx.data["script"] == "arab"
    assert ctx.data["src_script"] == "latn"
    # fences use the target script, not the detected one
    chapter = Path(ctx.data["out_path"]).read_text(encoding="utf-8")
    assert "{start-vocabulary lang=arb script=arab}" in chapter


def test_generic_branch_writes_prose(tmp_path, monkeypatch):
    result, ebook_yml = _run(tmp_path, monkeypatch, kind="generic")
    nodes = [r.node_id for r in result.branches[0].context.history]
    assert nodes == ["orient", "gen_write", "render"]
    chapter = Path(result.branches[0].context.data["out_path"]).read_text(encoding="utf-8")
    assert chapter.startswith("# Generic Chapter")
    assert "{start-" not in chapter  # prose, no fences
    project = yaml.safe_load(ebook_yml.read_text(encoding="utf-8"))
    assert "02.md" in project["text"][0]
