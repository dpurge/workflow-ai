"""Routing: which scripts go through the transcribe node (FR-T5).

Korean Hangul MUST be transcribed (Revised Romanization) — regression test for
the router that previously skipped it.
"""

from workflow_ai.ebook.definitions import branch_on_script
from workflow_ai.models import WorkflowContext


def _route(script: str) -> list[str]:
    ctx = WorkflowContext(initial_prompt="x", data={"script": script})
    return branch_on_script(None, ctx)


def test_korean_hangul_is_transcribed():
    assert _route("kore") == ["transcribe"]


def test_non_latin_scripts_are_transcribed():
    for s in ("arab", "hans", "hant", "hebr", "jpan", "deva"):
        assert _route(s) == ["transcribe"], s


def test_latin_cyrillic_greek_skip_transcription():
    for s in ("latn", "cyrl", "grek"):
        assert _route(s) == ["translate"], s
