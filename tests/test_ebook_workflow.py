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
from workflow_ai.engine import Engine, WorkflowError
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
                return [{"headword": f"w{i}", "translation": f"t{i}"} for i in range(12)]
            if name == "ModelList":
                return [{"pattern": f"p{i}", "translation": f"t{i}"} for i in range(4)]
            if name == "QuestionList":
                return [f"Frage {i}?" for i in range(4)]
            raise AssertionError(name)
        p = inv.prompt
        if p.startswith("Source:"):
            return "Cleaned source text."
        if p.startswith("Transcribe"):
            return "romanized"
        if p.startswith("Translate to"):
            return "Tłumaczenie."
        if p.startswith("Explain the key grammar"):
            return "## Gramatyka\nProsta."
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
    # derived chapter is the next number after 01.md
    assert out_path.name == "02.md"
    assert chapter.startswith("# Lekcja")
    assert "{start-vocabulary lang=deu script=latn}" in chapter
    assert "{start-models lang=deu script=latn}" in chapter
    assert "{start-text as=source lang=deu script=latn}" in chapter
    assert "{start-text as=translation lang=pol script=latn}" in chapter
    assert "{start-text as=grammar lang=pol script=latn}" in chapter
    assert "{start-questions" in chapter
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
    assert "02.md" in project["text"][0]


def test_no_wire_leaves_ebook_yml_untouched(tmp_path, monkeypatch):
    result, ebook_yml = _run(tmp_path, monkeypatch, wire=False)
    assert result.branches[0].context.data["wired"] is False
    project = yaml.safe_load(ebook_yml.read_text(encoding="utf-8"))
    assert "02.md" not in project["text"][0]


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
                return [{"headword": f"w{i}", "translation": f"t{i}"} for i in range(12)]
            if name == "ModelList":
                return [{"pattern": f"p{i}", "translation": f"t{i}"} for i in range(4)]
            if name == "QuestionList":
                return [f"q{i}?" for i in range(4)]
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


# ---------------------------------------------------------------------------
# URL source fetch (httpx-based)
# ---------------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, text: str, ctype: str = "text/html; charset=utf-8", status: int = 200):
        self.text = text
        self.headers = {"content-type": ctype}
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            import httpx
            raise httpx.HTTPError(f"HTTP {self.status_code}")


class _FakeClient:
    """Minimal httpx.Client stub: context manager with .get()."""

    def __init__(self, responses: list, error=None):
        self._responses = list(responses)
        self._error = error
        self.calls: list[dict] = []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get(self, url, **kw):
        self.calls.append({"url": url, **kw})
        if self._error is not None:
            raise self._error
        return self._responses.pop(0) if self._responses else _FakeResponse("")


def _url_project(tmp_path, language="pol", script="latn"):
    return _project(tmp_path, language=language, script=script, chapters=["section.md", "01.md"])


def test_url_source_fetches_html(tmp_path, monkeypatch):
    """URL source: httpx fetches HTML, fetch_source strips tags and caps length."""
    import httpx

    _stub_lang_skills(tmp_path, monkeypatch, ["deu"])
    ebook_yml = _url_project(tmp_path)

    html_body = "<html><body><h1>Artikel</h1><p>Ein Text.</p></body></html>"
    fake_client = _FakeClient([_FakeResponse(text=html_body)])
    monkeypatch.setattr(httpx, "Client", lambda **kw: fake_client)

    graph = WorkflowGraph.from_yaml(WORKFLOWS / "ebook" / "workflow.yaml")
    engine = Engine(ScriptedBackend(_responder_for("deu", "latn")))
    result = engine.run(
        graph,
        "Write about food",
        initial_data={
            "ebook_yml": str(ebook_yml),
            "kind": "lang",
            "language": "deu",
            "script": "latn",
            "source_ref": {"kind": "url", "value": "https://example.com/article"},
            "wire": False,
        },
    )
    # The fetched HTML was cleaned (tags stripped) and stored as source.
    source = result.branches[0].context.data["source"]
    assert "<html>" not in source
    assert "Artikel" in source
    assert "Ein Text." in source

    # The User-Agent was a browser-like one (not "workflow-ai/0.1").
    ua = fake_client.calls[0]["headers"]["User-Agent"]
    assert "Mozilla" in ua


def test_url_source_network_error_retries_then_fails(tmp_path, monkeypatch):
    """A persistent network error must be wrapped as AgentOutputError so the
    engine retries, then raises WorkflowError — not a raw TimeoutError."""
    import httpx

    _stub_lang_skills(tmp_path, monkeypatch, ["deu"])
    ebook_yml = _url_project(tmp_path)

    monkeypatch.setattr(
        httpx, "Client",
        lambda **kw: _FakeClient([], error=httpx.ConnectError("connection refused")),
    )

    graph = WorkflowGraph.from_yaml(WORKFLOWS / "ebook" / "workflow.yaml")
    engine = Engine(ScriptedBackend(_responder_for("deu", "latn")))
    with pytest.raises(WorkflowError, match="could not fetch"):
        engine.run(
            graph,
            "Write about food",
            initial_data={
                "ebook_yml": str(ebook_yml),
                "kind": "lang",
                "language": "deu",
                "script": "latn",
                "source_ref": {"kind": "url", "value": "https://example.com/article"},
                "wire": False,
            },
            retries_override=2,
        )


def test_url_source_403_wraps_as_agent_error(tmp_path, monkeypatch):
    """HTTP 403 (raise_for_status) must be caught and wrapped, not crash raw."""
    import httpx

    _stub_lang_skills(tmp_path, monkeypatch, ["deu"])
    ebook_yml = _url_project(tmp_path)

    monkeypatch.setattr(
        httpx, "Client",
        lambda **kw: _FakeClient([_FakeResponse(text="Forbidden", status=403)]),
    )

    graph = WorkflowGraph.from_yaml(WORKFLOWS / "ebook" / "workflow.yaml")
    engine = Engine(ScriptedBackend(_responder_for("deu", "latn")))
    with pytest.raises(WorkflowError, match="could not fetch"):
        engine.run(
            graph,
            "x",
            initial_data={
                "ebook_yml": str(ebook_yml),
                "kind": "lang",
                "language": "deu",
                "script": "latn",
                "source_ref": {"kind": "url", "value": "https://example.com/"},
                "wire": False,
            },
            retries_override=1,
        )
