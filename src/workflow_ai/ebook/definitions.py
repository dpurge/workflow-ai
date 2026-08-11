"""Registered logic for the ebook workflow.

  - actions:   orient_project (read ebook.yml → project facts), fetch_source
               (URL/file → cleaned text), gather_evidence (run the planned
               target-language searches), render_chapter (assemble + render the
               chapter and wire it into ebook.yml)
  - routers:   branch_on_kind (generic / lang-from-source / lang-from-topic),
               branch_on_script (skip transcription for latn/cyrl/grek only)
  - updaters:  store_clean, store_detect (keeps detect output off the book's
               authoritative language/script keys), store_compose (composed text)
  - verifiers: ebook_ready, detect_ok, queries_min, evidence_min, ground_check
               (grounding gate against memory-generation), and the count gates
  - skill:     '@lang' → phraseforge-lang-<iso> SKILL.md, from the bundled
               skills/lang/ set (EBOOK_LANG_SKILLS_DIR is an optional override)
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

import yaml

from ..models import VerifyResult, WorkflowContext
from ..registry import action, router, schema, skill_resolver, updater, verifier
from . import render as render_mod
from .schemas import ComposeOut, DetectOut, ModelList, QueriesOut, QuestionList, VocabularyList

# Register step schemas under workflow-facing names.
schema("detect_out")(DetectOut)
schema("vocabulary_out")(VocabularyList)
schema("models_out")(ModelList)
schema("questions_out")(QuestionList)
schema("queries_out")(QueriesOut)
schema("compose_out")(ComposeOut)

# Scripts routed straight to translation (no transcribe node). Korean (kore) is
# NOT here: the kor skill requires Revised-Romanization transcription, so Hangul
# must go through transcribe (lesson-transcription-rules FR-T5).
NO_TRANSCRIPTION_SCRIPTS = {"latn", "cyrl", "grek"}
MAX_SOURCE_CHARS = 2500

# Compose-from-topic path (kind=lang, --prompt topic, no --source).
_RESEARCH_TOOLS_DIR = Path(__file__).parent.parent / "research" / "tools"
_SEARCH_DELAY_SEC = 5.0   # politeness delay between web searches (avoid throttling/IP bans)
_EVIDENCE_MIN = 15        # gather must collect at least this many evidence items
_GROUND_STEM = 5          # pseudo-stem prefix length (inflection-tolerant overlap)
_GROUND_MIN_TOKEN = 4     # ignore tokens shorter than this when stemming
# Grounding overlap floors (unigram = vocabulary, bigram = phraseology) by form.
# A dialog restructures prose evidence into conversational turns, so both overlaps
# run lower even when genuinely grounded — relax them (vocabulary still gates it).
_GROUND_OVERLAP_BY_FORM = {
    "text": (0.50, 0.25),
    "dialog": (0.40, 0.08),
    # parallel's source paragraphs are straight prose (not restructured into
    # turns like dialog), so it gates identically to "text" — explicit here
    # for clarity even though it matches _GROUND_OVERLAP_DEFAULT.
    "parallel": (0.50, 0.25),
}
_GROUND_OVERLAP_DEFAULT = (0.50, 0.25)
# Minimum composed learner-text length (chars), scaled by CEFR level — an A1
# self-intro is legitimately short, so a flat floor would reject valid lessons.
_GROUND_MIN_LEN_BY_LEVEL = {"a1": 90, "a2": 130, "b1": 190, "b2": 250, "c1": 300, "c2": 300}
_GROUND_MIN_LEN_DEFAULT = 150  # when --level is unset

# ISO 639-3 -> 639-1 for target-language Wikipedia (fallback: skip Wikipedia).
# Keys are limited to the bundled book languages (a language with no @lang skill
# can't complete a run anyway).
_ISO3_TO_1 = {
    "deu": "de", "spa": "es", "fra": "fr", "ita": "it", "por": "pt", "nld": "nl",
    "rus": "ru", "ukr": "uk", "ces": "cs", "bul": "bg", "ell": "el",
    "tur": "tr", "fin": "fi", "hun": "hu", "ron": "ro", "dan": "da",
    "heb": "he", "fas": "fa", "arb": "ar", "cmn": "zh",
    "jpn": "ja", "kor": "ko", "hin": "hi", "lat": "la", "vie": "vi", "ind": "id",
    "hrv": "hr", "srp": "sr", "lit": "lt", "eng": "en",
}

# Directory holding phraseforge-lang-<iso>/SKILL.md files, for the '@lang' skill.
# The full set (42 languages) is BUNDLED in this package, so nothing needs to be
# configured. EBOOK_LANG_SKILLS_DIR is an optional override for a custom set.
_BUNDLED_LANG_SKILLS = Path(__file__).parent / "skills" / "lang"
_LANG_SKILLS_DIR = Path(
    os.environ.get("EBOOK_LANG_SKILLS_DIR", str(_BUNDLED_LANG_SKILLS))
).expanduser()


# --- actions ---------------------------------------------------------------


@action("orient_project")
def orient_project(context: WorkflowContext) -> dict[str, Any]:
    """Read the project's ebook.yml and derive the facts the rest of the run
    needs: book language/script, kind, translation language/script, the target
    chapter path, and the section it belongs to."""

    d = context.data
    ebook_yml = d.get("ebook_yml")
    if not ebook_yml:
        base = Path(d.get("cwd") or ".").expanduser().resolve()
        found = _find_ebook_yml(base)
        ebook_yml = str(found) if found else None
    if not ebook_yml:
        raise ValueError("no ebook.yml given (--ebook-yml) and none found above --cwd")

    ep = Path(ebook_yml).expanduser().resolve()
    if not ep.exists():
        raise ValueError(f"ebook.yml not found: {ep}")
    project = yaml.safe_load(ep.read_text(encoding="utf-8")) or {}

    text_sections = project.get("text") or []
    section = None
    if text_sections and isinstance(text_sections[0], list) and text_sections[0]:
        section = text_sections[0][0]

    # The book's own language/script (from ebook.yml) — the reader/prose side.
    book_language = (project.get("language") or "").lower() or None
    book_script = (project.get("script") or "latn").lower()

    # kind is CLI-enforced to generic|lang; ebook.yml `kind` is not consulted.
    kind = (d.get("kind") or "").lower()
    if kind == "generic":
        # No learning target — the prose chapter is written in the book's language.
        language, script = book_language, book_script
        translation_lang, translation_script = book_language or "pol", book_script
    else:  # lang — the learning TARGET comes from --lang/--script (CLI-required)
        language = (d.get("language") or "").lower() or None
        script = (d.get("script") or "latn").lower()
        # The reader/translation side is the BOOK's language/script.
        translation_lang = (
            d.get("translation_lang")
            or project.get("translation-language")
            or project.get("translation_language")
            or book_language
            or "pol"
        )
        translation_script = (
            project.get("translation-script")
            or project.get("translation_script")
            or book_script
            or "latn"
        )

    return {
        "ebook_yml": str(ep),
        "project_dir": str(ep.parent),
        "language": language,
        "script": script,
        "book_language": book_language,
        "book_script": book_script,
        "kind": kind,
        "translation_lang": translation_lang,
        "translation_script": translation_script,
        "book_title": project.get("title"),
        "section": section,
        "chapter": d.get("chapter") or _derive_chapter(text_sections, kind=kind, level=d.get("level")),
    }


@action("fetch_source")
def fetch_source(context: WorkflowContext) -> dict[str, Any]:
    """Fetch a URL or read a file, strip HTML, cap length."""

    ref = context.data.get("source_ref") or {}
    kind, value = ref.get("kind"), ref.get("value")
    if not value:
        raise ValueError("no source_ref provided (expected {'kind','value'})")

    if kind == "url":
        import urllib.request

        req = urllib.request.Request(value, headers={"User-Agent": "workflow-ai/0.1"})
        with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310 (user-provided URL)
            ctype = resp.headers.get("content-type", "")
            raw = resp.read().decode("utf-8", errors="replace")
        is_html = "text/html" in ctype.lower() or bool(re.search(r"<\/?[a-z][\s\S]*>", raw, re.I))
    else:
        raw = Path(value).read_text(encoding="utf-8")
        is_html = str(value).lower().endswith(".html")

    text = (html_to_text(raw) if is_html else raw)[:MAX_SOURCE_CHARS]
    return {"source": text}


@action("render_chapter")
def render_chapter(context: WorkflowContext) -> dict[str, Any]:
    """Assemble and render the chapter, write it into the project, and (unless
    disabled) wire it into ebook.yml `text`."""

    d = context.data
    project_dir = Path(d["project_dir"])
    chapter_rel = d.get("chapter") or "chapter.md"
    out_path = project_dir / chapter_rel
    out_path.parent.mkdir(parents=True, exist_ok=True)

    kind = (d.get("kind") or "").lower()
    if kind == "generic":
        fallback = d.get("book_title") or "Chapter"
        md = render_mod.render_generic_chapter(d.get("chapter_md"), fallback)
    else:  # lang
        md = render_mod.render_language_chapter(_assemble_lesson(d))
    out_path.write_text(md, encoding="utf-8")

    wired = False
    if d.get("wire", True):
        wired = render_mod.wire_ebook_yml(d["ebook_yml"], chapter_rel)

    return {"out_path": str(out_path), "ebook_yml": d["ebook_yml"], "wired": wired}


# --- compose-from-topic path -----------------------------------------------


def _run_search_tool(name: str, query: str, *extra: str) -> list[dict[str, Any]]:
    """Run a research/tools search script via `uv run --script`; parse its JSON
    list. Degrades to [] on any failure (missing uv, timeout, bad JSON)."""
    tool = _RESEARCH_TOOLS_DIR / name
    uv = shutil.which("uv")
    if uv is None or not tool.exists():
        return []
    try:
        proc = subprocess.run(
            [uv, "run", "--script", str(tool), query, *extra],
            capture_output=True, text=True, encoding="utf-8", timeout=60,
        )
    except (subprocess.SubprocessError, OSError):
        return []
    out = (proc.stdout or "").strip()
    if not out:
        return []
    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else []


# NOTE: name is workflow-unique — the registry is global and `research` already
# registers a `gather_evidence` action.
@action("gather_lang_evidence")
def gather_lang_evidence(context: WorkflowContext) -> dict[str, Any]:
    """Run the planned target-language searches and collect real example
    sentences (target-language Wikipedia + web search). Politeness delay between
    searches; early-stop on repeated empties (likely rate-limited) — keep what
    was gathered so the run can still compose."""

    d = context.data
    queries = [q for q in (d.get("queries") or []) if str(q).strip()]
    max_searches = int(d.get("max_searches") or 24)
    lang3 = (d.get("language") or "").lower()
    topic = context.initial_prompt

    evidence: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add(text: str | None, source: str, query: str) -> None:
        text = " ".join((text or "").split())
        if len(text) < 20:
            return
        key = text[:120].lower()
        if key in seen:
            return
        seen.add(key)
        evidence.append({"text": text[:400], "source": source, "query": query})

    # 1. target-language Wikipedia on the topic (coherent target-language prose)
    iso1 = _ISO3_TO_1.get(lang3)
    if iso1:
        for w in _run_search_tool("wikipedia-search.py", topic, "--lang", iso1, "--limit", "3"):
            add(w.get("summary") or w.get("title", ""), w.get("url", ""), topic)

    # 2. web search per planned query (budgeted, polite, early-stop on throttling)
    empties = 0
    for q in queries[:max_searches]:
        time.sleep(_SEARCH_DELAY_SEC)  # before EVERY search (incl. after Wikipedia)
        results = _run_search_tool("web-search.py", q, "--limit", "5")
        if results:
            empties = 0
            for r in results:
                add(r.get("snippet") or r.get("title", ""), r.get("url", ""), q)
        else:
            empties += 1
            if empties >= 3:
                break  # likely rate-limited — stop and use what we have

    evidence_text = "\n".join(f"- {e['text']}" for e in evidence)[:6000]
    return {"evidence": evidence, "evidence_text": evidence_text}


# --- updaters --------------------------------------------------------------


@updater("store_compose")
def store_compose(output: ComposeOut, context: WorkflowContext) -> WorkflowContext:
    """Store the composed learner text (+ dialog turns / parallel paragraphs) so
    the normal pipeline (detect → vocabulary → … → render) can consume it."""
    context.data["text"] = output.text
    context.data["text_snippet"] = (output.text or "")[:400]
    context.data["form"] = output.form
    if output.form == "dialog" and output.turns:
        context.data["turns"] = [t.model_dump() for t in output.turns]
    if output.form == "parallel" and output.paragraphs:
        context.data["paragraphs"] = [p.model_dump() for p in output.paragraphs]
    return context


@updater("store_clean")
def store_clean(output: str, context: WorkflowContext) -> WorkflowContext:
    """Store cleaned text and a short snippet for language detection."""
    context.data["text"] = output
    context.data["text_snippet"] = output[:400]
    return context


@updater("store_detect")
def store_detect(output: DetectOut, context: WorkflowContext) -> WorkflowContext:
    """Store detect output under non-colliding keys so it never overwrites the
    book's authoritative language/script from orient. Only `title` is consumed
    downstream (by render); src_* are informational."""
    context.data["src_language"] = output.language
    context.data["src_script"] = output.script
    context.data["title"] = output.title
    return context


@updater("store_entries")
def store_entries(output: VocabularyList | ModelList | QuestionList, context: WorkflowContext) -> WorkflowContext:
    """Store object-wrapped list outputs as plain lists for the renderer."""
    node_id = context.history[-1].node_id if context.history else None
    key_by_node = {
        "vocabulary": "vocabulary",
        "models": "models",
        "questions": "questions",
    }
    key = key_by_node.get(node_id)
    if key is None:
        raise ValueError(f"store_entries used outside vocabulary/models/questions (node={node_id!r})")
    context.data[key] = [e.model_dump() if hasattr(e, "model_dump") else e for e in (output.entries or [])]
    return context


# --- helpers ---------------------------------------------------------------


def _lesson_title(d: dict[str, Any]) -> str:
    """Deterministic language-lesson chapter title.

    Use `<level>-<chapter-stem>` when available (e.g. `b1-001`) so generated
    lesson files align with the book structure rather than source/article titles.
    Generic prose chapters keep their authored/book title elsewhere.
    """
    level = (d.get("level") or "").strip().lower()
    chapter = str(d.get("chapter") or "").strip()
    stem = Path(chapter).stem if chapter else ""
    if level and stem:
        return f"{level}-{stem}"
    return d.get("title") or d.get("book_title") or "Lekcja"


def _assemble_lesson(d: dict[str, Any]) -> dict[str, Any]:
    """Build the lesson dict the renderer expects from accumulated context."""
    return {
        "title": _lesson_title(d),
        "lang": d.get("language"),
        "script": d.get("script"),
        "translation_lang": d.get("translation_lang", "pol"),
        "translation_script": d.get("translation_script", "latn"),
        "vocabulary": d.get("vocabulary") or [],
        "models": d.get("models") or [],
        "source_text": d.get("text"),
        "transcription": d.get("transcription"),
        "translation": d.get("translation"),
        "grammar": d.get("grammar"),
        "questions": d.get("questions") or [],
        "form": d.get("form"),
        "turns": d.get("turns"),
        "paragraphs": d.get("paragraphs"),
    }


def _find_ebook_yml(base: Path) -> Path | None:
    """Nearest ebook.yml at or above `base`."""
    for directory in (base, *base.parents):
        candidate = directory / "ebook.yml"
        if candidate.exists():
            return candidate
    return None


def _derive_chapter(text_sections: list[Any], *, kind: str, level: str | None = None) -> str:
    """Derive the next chapter filename.

    Generic chapters keep the historical flat `NN.md` layout. Language lessons
    with a CEFR level default to `<level>/NNN.md` (e.g. `b1/001.md`). Only
    chapters already inside that target subdirectory contribute to the next
    number; older flat files are ignored so projects can migrate layouts.
    """
    entries: list[str] = []
    section_dir = ""
    if text_sections and isinstance(text_sections[0], list) and text_sections[0]:
        section = Path(str(text_sections[0][0]))
        section_dir = "" if str(section.parent) == "." else str(section.parent)
        entries = [str(e) for e in text_sections[0][1:]]  # skip the section file

    kind = (kind or "").lower()
    level = (level or "").lower() or None
    if kind == "lang":
        subdir = level or section_dir
        width = 3
    else:
        subdir = section_dir
        width = 2

    nums: list[int] = []
    for entry in entries:
        p = Path(entry)
        entry_dir = "" if str(p.parent) == "." else str(p.parent)
        if subdir:
            if entry_dir != subdir:
                continue
        elif entry_dir:
            subdir = entry_dir
        m = re.fullmatch(r"(\d+)", p.stem)
        if m:
            nums.append(int(m.group(1)))
    nxt = (max(nums) + 1) if nums else 1
    name = f"{nxt:0{width}d}.md"
    return str(Path(subdir) / name) if subdir else name


def html_to_text(s: str) -> str:
    """Dependency-free HTML→text."""

    s = re.sub(r"<script[\s\S]*?</script>", " ", s, flags=re.I)
    s = re.sub(r"<style[\s\S]*?</style>", " ", s, flags=re.I)
    s = re.sub(r"<[^>]+>", " ", s)
    replacements = {
        "&nbsp;": " ", "&amp;": "&", "&lt;": "<", "&gt;": ">",
        "&quot;": '"', "&#39;": "'",
    }
    for k, v in replacements.items():
        s = s.replace(k, v)
    s = re.sub(r"[ \t]{2,}", " ", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()


# --- routers ---------------------------------------------------------------


@router("branch_on_kind")
def branch_on_kind(output: Any, context: WorkflowContext) -> list[str]:
    """3-way route: generic prose; language-from-source (--source); or
    language-composed-from-a-topic (--prompt, no --source)."""
    kind = (context.data.get("kind") or "").lower()
    if kind not in ("generic", "lang"):
        raise ValueError(f"kind must be 'generic' or 'lang', got {kind!r}")
    if kind == "generic":
        return ["gen_write"]
    return ["read"] if context.data.get("source_ref") else ["plan_queries"]


@router("branch_on_script")
def branch_on_script(output: Any, context: WorkflowContext) -> list[str]:
    """Skip transcription for scripts that don't need it. Routes on the TARGET
    script (from --script), which store_detect deliberately does not overwrite."""
    script = (context.data.get("script") or "").lower()
    return ["translate"] if script in NO_TRANSCRIPTION_SCRIPTS else ["transcribe"]


# --- verifiers -------------------------------------------------------------


@verifier("ebook_ready")
def ebook_ready(output: Any, context: WorkflowContext) -> VerifyResult:
    data = output if isinstance(output, dict) else {}
    errors: list[str] = []
    if not data.get("language"):
        errors.append(
            "could not determine the language: pass --lang for --kind lang, "
            "or set ebook.yml `language` for --kind generic"
        )
    if (data.get("kind") or "").lower() not in ("generic", "lang"):
        errors.append("kind must be 'generic' or 'lang'")
    ey = data.get("ebook_yml")
    if not ey or not Path(ey).exists():
        errors.append("ebook.yml not found")
    return VerifyResult(ok=not errors, errors=errors)


@verifier("detect_ok")
def detect_ok(output: DetectOut, context: WorkflowContext) -> VerifyResult:
    title = (getattr(output, "title", "") or "").strip()
    return VerifyResult(ok=bool(title), errors=[] if title else ["detect must return a non-empty title"])


@verifier("source_nonempty")
def source_nonempty(output: Any, context: WorkflowContext) -> VerifyResult:
    text = (output or {}).get("source", "") if isinstance(output, dict) else ""
    return VerifyResult(ok=bool(text.strip()), errors=[] if text.strip() else ["source text is empty"])


@verifier("queries_min")
def queries_min(output: QueriesOut, context: WorkflowContext) -> VerifyResult:
    n = len(getattr(output, "queries", []) or [])
    return VerifyResult(
        ok=n >= 20,
        errors=[] if n >= 20 else [f"need >=20 target-language search queries, got {n}"],
    )


@verifier("evidence_min")
def evidence_min(output: Any, context: WorkflowContext) -> VerifyResult:
    ev = (output or {}).get("evidence", []) if isinstance(output, dict) else []
    n = len(ev)
    return VerifyResult(
        ok=n >= _EVIDENCE_MIN,
        errors=[] if n >= _EVIDENCE_MIN else [
            f"gathered only {n} evidence items (need >= {_EVIDENCE_MIN}); "
            "web search may be rate-limited"
        ],
    )


def _stems(text: str | None) -> list[str]:
    """Content words reduced to a short pseudo-stem (inflection-tolerant:
    Polish `rynek`/`rynku` → `rynek`[:5])."""
    return [
        t[:_GROUND_STEM]
        for t in re.findall(r"\w+", (text or "").lower(), flags=re.UNICODE)
        if len(t) >= _GROUND_MIN_TOKEN
    ]


def _char_trigrams(text: str | None) -> set[str]:
    chars = re.sub(r"[\W_]+", "", (text or "").lower(), flags=re.UNICODE)
    return {chars[i:i + 3] for i in range(len(chars) - 2)}


def _units(text: str | None, use_chars: bool) -> tuple[set[str], set[str]]:
    """(unigrams, bigrams). For short-morpheme / ideographic scripts (CJK) use
    character trigrams; otherwise word pseudo-stems and consecutive word-pairs.
    The representation is chosen ONCE from the evidence and applied to both texts
    so they are always comparable."""
    if use_chars:
        tri = _char_trigrams(text)
        return tri, tri
    stems = _stems(text)
    return set(stems), {f"{a}|{b}" for a, b in zip(stems, stems[1:])}


@verifier("ground_check")
def ground_check(output: ComposeOut, context: WorkflowContext) -> VerifyResult:
    """Reject composed text that does not draw on the gathered evidence — the gate
    against writing from model memory. Requires BOTH vocabulary overlap AND
    word-pair (phraseology) overlap with the evidence: on-topic memory text tends
    to share domain vocabulary but reproduces far fewer of the exact multi-word
    patterns actually found in the sources. Heuristic, not a semantic guarantee.
    Skipped entirely when grounding is disabled (--no-grounding), for creative prompts."""
    if not context.data.get("grounding", True):
        return VerifyResult(ok=True)
    text = (getattr(output, "text", "") or "").strip()
    level = (context.data.get("level") or "").lower()
    min_len = _GROUND_MIN_LEN_BY_LEVEL.get(level, _GROUND_MIN_LEN_DEFAULT)
    if len(text) < min_len:
        return VerifyResult(ok=False, errors=[
            f"composed text too short ({len(text)} chars, need >= {min_len} for level "
            f"'{level or 'unset'}'); expand it using the evidence"
        ])
    evidence_text = context.data.get("evidence_text") or ""
    use_chars = len(set(_stems(evidence_text))) < 8  # ideographic / short-morpheme corpus
    c_uni, c_bi = _units(text, use_chars)
    e_uni, e_bi = _units(evidence_text, use_chars)
    if not c_uni:
        return VerifyResult(ok=False, errors=["composed text has no content words"])
    if not e_uni:
        return VerifyResult(ok=True)  # no evidence to compare (evidence_min guards this)
    form = (context.data.get("form") or "text").lower()
    uni_min, bi_min = _GROUND_OVERLAP_BY_FORM.get(form, _GROUND_OVERLAP_DEFAULT)
    uni = len(c_uni & e_uni) / len(c_uni)
    bi = (len(c_bi & e_bi) / len(c_bi)) if c_bi else 1.0
    if uni < uni_min or bi < bi_min:
        return VerifyResult(ok=False, errors=[
            f"insufficient grounding: {uni:.0%} of the composed vocabulary and "
            f"{bi:.0%} of its word-pairs appear in the evidence (need "
            f">= {uni_min:.0%} / {bi_min:.0%} for form '{form}'); reuse the actual "
            "words, phrases, and sentence patterns from the sources, not memory"
        ])
    return VerifyResult(ok=True)


@verifier("vocab_min10")
def vocab_min10(output: VocabularyList, context: WorkflowContext) -> VerifyResult:
    n = len(output.entries)
    return VerifyResult(ok=n >= 10, errors=[] if n >= 10 else [f"need >=10 vocabulary entries, got {n}"])


@verifier("models_3_8")
def models_3_8(output: ModelList, context: WorkflowContext) -> VerifyResult:
    n = len(output.entries)
    ok = 3 <= n <= 8
    return VerifyResult(ok=ok, errors=[] if ok else [f"need 3-8 models, got {n}"])


@verifier("questions_3_8")
def questions_3_8(output: QuestionList, context: WorkflowContext) -> VerifyResult:
    n = len(output.entries)
    ok = 3 <= n <= 8
    return VerifyResult(ok=ok, errors=[] if ok else [f"need 3-8 questions, got {n}"])


# --- skill resolver --------------------------------------------------------


@skill_resolver("lang")
def resolve_lang_skill(ref: str, context: WorkflowContext) -> str:
    """Map '@lang' to the language skill's SKILL.md path for the TARGET (lesson)
    language (Mandarin distinguishes script)."""

    lang = (context.data.get("language") or "").lower()
    if not lang:
        raise ValueError("cannot resolve @lang: no target language in context (pass --lang)")
    if lang == "cmn":
        script = (context.data.get("script") or "hans").lower()
        name = f"phraseforge-lang-cmn-{'hant' if script == 'hant' else 'hans'}"
    else:
        name = f"phraseforge-lang-{lang}"
    path = _LANG_SKILLS_DIR / name / "SKILL.md"
    if not path.exists():
        raise ValueError(
            f"no language skill for '{lang}' ({name}) in {_LANG_SKILLS_DIR}; add a "
            f"phraseforge-lang-{lang}/SKILL.md there, or point EBOOK_LANG_SKILLS_DIR "
            "at a skills set that includes it"
        )
    return str(path)
