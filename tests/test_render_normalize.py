"""Renderer normalization (lesson-prompt-hardening FR-1/FR-4 defensive net).

A weak model may emit the grammar tag already wrapped in braces, or double-wrapped,
or the transcription already bracketed. The renderer must strip the OUTER brace/
bracket run so its own wrapping never produces `{{...}}` / `[[...]]`.
"""

from workflow_ai.ebook.render import _model_line, _vocab_line


def test_vocab_grammar_brace_variants_all_render_single():
    for grammar in ("N 只", "{N 只}", "{{N 只}}", " {N 只} "):
        line = _vocab_line(
            {"phrase": "狗", "grammar": grammar, "transcription": "gǒu", "translation": "pies"}
        )
        assert line == "狗 {N 只} [gǒu] = pies", (grammar, line)


def test_vocab_transcription_bracket_variants_all_render_single():
    for transcription in ("gǒu", "[gǒu]", "[[gǒu]]"):
        line = _vocab_line(
            {"phrase": "狗", "grammar": "N", "transcription": transcription, "translation": "pies"}
        )
        assert line == "狗 {N} [gǒu] = pies", (transcription, line)


def test_model_transcription_bracket_variants_all_render_single():
    for transcription in ("fābù", "[fābù]", "[[fābù]]"):
        line = _model_line(
            {"phrase": "发布", "transcription": transcription, "translation": "publikować"}
        )
        assert line == "发布 [fābù] = publikować", (transcription, line)


def test_vocab_latin_no_grammar_no_transcription_unchanged():
    line = _vocab_line({"phrase": "das Haus", "translation": "dom"})
    assert line == "das Haus = dom"
