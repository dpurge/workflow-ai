"""In-repo renderer for ebook chapters.

Assembles a language lesson into a `{start-*}`-fenced Markdown chapter, renders
a generic prose chapter, and wires a chapter file into an `ebook.yml` project.
Mirrors the cli-tools `ebook` builder's fence contract:

  - vocabulary: `headword {grammar} [transcription] = translation (notes)` —
    never a bare `=` (a raw `=` in the gloss is swapped for a full-width `＝`
    because the vocabulary parser splits on the RIGHTMOST `=`).
  - models:     `pattern [transcription] = translation` (first ` = ` splits).
  - text:       raw markdown, `as=` in {source, transcription, translation, grammar}.
  - questions:  one question per line.
  - every chapter starts with an `# H1`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

TRANSLATION_SCRIPT_DEFAULT = "latn"


def _attrs(lang: str | None, script: str | None, *, as_: str | None = None) -> str:
    parts: list[str] = []
    if as_:
        parts.append(f"as={as_}")
    if lang:
        parts.append(f"lang={lang}")
    if script:
        parts.append(f"script={script}")
    return (" " + " ".join(parts)) if parts else ""


def _with_notes(translation: str | None, notes: str | None) -> str:
    translation = (translation or "").strip()
    if notes and str(notes).strip():
        return f"{translation} ({str(notes).strip()})"
    return translation


def _vocab_line(entry: dict[str, Any]) -> str | None:
    headword = (entry.get("headword") or "").strip()
    if not headword:
        return None
    parts = [headword]
    grammar = (entry.get("grammar") or "").strip()
    if grammar:
        parts.append("{" + grammar + "}")
    transcription = (entry.get("transcription") or "").strip()
    if transcription:
        parts.append("[" + transcription + "]")
    line = " ".join(parts)
    translation = (entry.get("translation") or "").strip()
    if translation:
        # The vocabulary parser splits on the RIGHTMOST `=`; neutralise any raw
        # `=` in the gloss with a full-width `＝` (U+FF1D) so fields don't corrupt.
        trans = _with_notes(entry.get("translation"), entry.get("notes")).replace("=", "＝")
        line += " = " + trans
    return line


def _model_line(entry: dict[str, Any]) -> str | None:
    # Models split on the FIRST ` = ` (spaces), so a raw `=` in the gloss is
    # harmless here — no full-width neutralisation needed (unlike vocabulary).
    pattern = (entry.get("pattern") or "").strip()
    translation = (entry.get("translation") or "").strip()
    if not pattern or not translation:
        return None
    left = pattern
    transcription = (entry.get("transcription") or "").strip()
    if transcription:
        left += " [" + transcription + "]"
    return f"{left} = " + _with_notes(entry.get("translation"), entry.get("notes"))


def _fence(name: str, attr: str, body: list[str]) -> list[str]:
    return [f"{{start-{name}{attr}}}", "", *body, "", f"{{end-{name}}}", ""]


def _indent2(text: str | None) -> list[str]:
    """Indent EVERY line of a turn by exactly 2 spaces (dialog parser is strict)."""
    return ["  " + line for line in (text or "").strip().split("\n")]


def _dialog_body(turns: list[dict[str, Any]], field: str = "text") -> list[str]:
    """Render dialog turns from `field` ("text" = target, "translation" = reader):
    `@Speaker:` / `--:` header, body indented exactly 2 spaces, blank line between
    turns. Turns whose chosen field is empty are skipped."""
    lines: list[str] = []
    for turn in turns or []:
        body = (turn.get(field) or "").strip()
        if not body:
            continue
        speaker = (turn.get("speaker") or "").strip().rstrip(":").strip()
        lines.append(f"@{speaker}:" if speaker else "--:")
        lines += _indent2(body)
        lines.append("")
    while lines and lines[-1] == "":
        lines.pop()
    return lines


def render_language_chapter(lesson: dict[str, Any]) -> str:
    """Render an assembled lesson dict into a fenced chapter. Source is a text
    block, or a `{start-dialog}` when form == dialog; block order matches the
    cli-tools exporter."""

    lang = lesson.get("lang")
    script = lesson.get("script")
    tl = lesson.get("translation_lang") or "pol"
    ts = lesson.get("translation_script") or TRANSLATION_SCRIPT_DEFAULT
    title = (lesson.get("title") or "Lekcja").strip()

    out: list[str] = [f"# {title}", ""]

    vocab_body = [line for e in (lesson.get("vocabulary") or []) if (line := _vocab_line(e))]
    if vocab_body:
        out += _fence("vocabulary", _attrs(lang, script), vocab_body)

    model_body = [line for m in (lesson.get("models") or []) if (line := _model_line(m))]
    if model_body:
        out += _fence("models", _attrs(lang, script), model_body)

    if lesson.get("form") == "dialog" and lesson.get("turns"):
        out += _fence("dialog", _attrs(lang, script), _dialog_body(lesson["turns"]))
    elif (source := (lesson.get("source_text") or "").strip()):
        out += _fence("text", _attrs(lang, script, as_="source"), [source])

    transcription = (lesson.get("transcription") or "").strip()
    if transcription:
        out += _fence("text", _attrs(lang, "latn", as_="transcription"), [transcription])

    # A dialog gets a parallel translated dialog ({start-dialog as=translation});
    # otherwise the translation is a prose text block.
    translation_turns = [
        t for t in (lesson.get("turns") or []) if (t.get("translation") or "").strip()
    ]
    if lesson.get("form") == "dialog" and translation_turns:
        out += _fence(
            "dialog", _attrs(tl, ts, as_="translation"),
            _dialog_body(lesson["turns"], field="translation"),
        )
    elif (translation := (lesson.get("translation") or "").strip()):
        out += _fence("text", _attrs(tl, ts, as_="translation"), [translation])

    questions = [q.strip() for q in (lesson.get("questions") or []) if q and q.strip()]
    if questions:
        out += _fence("questions", _attrs(lang, script), questions)

    grammar = (lesson.get("grammar") or "").strip()
    if grammar:
        out += _fence("text", _attrs(tl, ts, as_="grammar"), [grammar])

    return "\n".join(out).rstrip() + "\n"


def render_generic_chapter(prose_md: str | None, fallback_title: str = "Chapter") -> str:
    """Render a generic prose chapter, guaranteeing a leading `# H1`."""

    text = (prose_md or "").strip()
    if not text:
        return f"# {fallback_title}\n"
    if not text.lstrip().startswith("# "):
        text = f"# {fallback_title}\n\n{text}"
    return text.rstrip() + "\n"


def wire_ebook_yml(path: str | Path, chapter: str, section_index: int = 0) -> bool:
    """Append `chapter` to the ebook.yml `text` section (additive, idempotent).

    Never reorders or removes existing entries. Returns True if the file was
    modified, False if the chapter was already present or the file is absent.
    Note: re-serialises the YAML (keys preserved, formatting may normalise).
    """

    p = Path(path)
    if not p.exists():
        return False
    project = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    text = project.get("text")

    if not isinstance(text, list) or not text:
        text = [[chapter]]
    else:
        idx = section_index if 0 <= section_index < len(text) else 0
        section = text[idx] if isinstance(text[idx], list) else [text[idx]]
        if chapter in section:
            return False  # idempotent
        text = list(text)
        text[idx] = [*section, chapter]

    project["text"] = text
    p.write_text(yaml.safe_dump(project, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return True
