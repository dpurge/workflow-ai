"""Registered logic for the phraseforge workflow.

Mirrors Pi's `phraseforge-mdx.ts`:
  - actions:  fetch_source (URL/file -> cleaned text), save_lesson (assemble the
              Lesson JSON and render via Pi's mdx-export.py)
  - router:   branch_on_script (skip transcription for latn/cyrl/grek)
  - verifiers: count/shape gates matching the TS thresholds
  - skill:    '@lang' -> phraseforge-lang-<iso> SKILL.md path
"""

from __future__ import annotations

import datetime
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from ..models import VerifyResult, WorkflowContext
from ..registry import action, router, schema, skill_resolver, verifier
from .schemas import (
    ALLOWED_EXERCISE_TYPES,
    DetectOut,
    ExerciseList,
    ModelList,
    QuestionList,
    VocabularyList,
)

# Register step schemas under workflow-facing names.
schema("detect_out")(DetectOut)
schema("vocabulary_out")(VocabularyList)
schema("models_out")(ModelList)
schema("questions_out")(QuestionList)
schema("exercises_out")(ExerciseList)

TRANSLITERATED = {"latn", "cyrl", "grek"}  # scripts that need NO transcription
MAX_SOURCE_CHARS = 8000

_PI_SKILLS_DIR = Path(os.environ.get("PI_SKILLS_DIR", "~/.pi/agent/skills")).expanduser()
_MDX_EXPORT = Path(
    os.environ.get(
        "PHRASEFORGE_EXPORT",
        "~/.pi/agent/skills/phraseforge-web/tools/mdx-export.py",
    )
).expanduser()


# --- actions ---------------------------------------------------------------


@action("fetch_source")
def fetch_source(context: WorkflowContext) -> dict[str, Any]:
    """Fetch a URL or read a file, strip HTML, cap length. Mirrors TS `read`."""

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


@action("save_lesson")
def save_lesson(context: WorkflowContext) -> dict[str, Any]:
    """Assemble the Lesson JSON and render MDX via Pi's mdx-export.py."""

    import json

    d = context.data
    date = datetime.date.today().isoformat()
    lesson: dict[str, Any] = {
        "version": 1,
        "title": d.get("title"),
        "lang": d.get("language"),
        "script": d.get("script"),
        "translation_lang": d.get("translation_lang", "pol"),
        "translation_script": "latn",
        "level": d.get("level"),
        "date": date,
        "vocabulary": d.get("vocabulary"),
        "models": d.get("models"),
        "source": {"kind": "text", "content": d.get("text")},
        "translation": d.get("translation"),
        "grammar": d.get("grammar"),
        "questions": d.get("questions"),
        "exercises": d.get("exercises"),
    }
    if d.get("transcription"):
        lesson["transcription"] = d["transcription"]

    cwd = Path(d.get("cwd", "."))
    out_dir = cwd / "docs" / str(d.get("language")) / str(d.get("level"))
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{date}-{_next_seq(out_dir, date)}.mdx"

    uv = shutil.which("uv")
    if uv is None:
        raise RuntimeError("'uv' not found on PATH; required to run mdx-export.py")
    if not _MDX_EXPORT.exists():
        raise RuntimeError(f"mdx-export.py not found at {_MDX_EXPORT}")

    proc = subprocess.run(
        [uv, "run", "--script", str(_MDX_EXPORT), "--out", str(out_path)],
        input=json.dumps(lesson),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"mdx-export failed: {proc.stderr.strip() or proc.stdout.strip()}")
    return {"out_path": str(out_path)}


def _next_seq(out_dir: Path, date: str) -> str:
    taken = {
        f.name[len(date) + 1 : -4]
        for f in out_dir.glob(f"{date}-*.mdx")
    }
    seq = "a"
    while seq in taken:
        seq = chr(ord(seq) + 1)
    return seq


def html_to_text(s: str) -> str:
    """Dependency-free HTML->text, ported from the TS `htmlToText`."""

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


# --- router ----------------------------------------------------------------


@router("branch_on_script")
def branch_on_script(output: Any, context: WorkflowContext) -> list[str]:
    """Skip transcription for scripts that don't need it (mirrors TS detect.next)."""

    script = (context.data.get("script") or "").lower()
    return ["translate"] if script in TRANSLITERATED else ["transcribe"]


# --- verifiers -------------------------------------------------------------


@verifier("source_nonempty")
def source_nonempty(output: Any, context: WorkflowContext) -> VerifyResult:
    text = (output or {}).get("source", "") if isinstance(output, dict) else ""
    return VerifyResult(ok=bool(text.strip()), errors=[] if text.strip() else ["source text is empty"])


@verifier("vocab_min10")
def vocab_min10(output: VocabularyList, context: WorkflowContext) -> VerifyResult:
    n = len(output.root)
    return VerifyResult(ok=n >= 10, errors=[] if n >= 10 else [f"need >=10 vocabulary entries, got {n}"])


@verifier("models_3_8")
def models_3_8(output: ModelList, context: WorkflowContext) -> VerifyResult:
    n = len(output.root)
    ok = 3 <= n <= 8
    return VerifyResult(ok=ok, errors=[] if ok else [f"need 3-8 models, got {n}"])


@verifier("questions_3_8")
def questions_3_8(output: QuestionList, context: WorkflowContext) -> VerifyResult:
    n = len(output.root)
    ok = 3 <= n <= 8
    return VerifyResult(ok=ok, errors=[] if ok else [f"need 3-8 questions, got {n}"])


@verifier("exercises_ok")
def exercises_ok(output: ExerciseList, context: WorkflowContext) -> VerifyResult:
    items = output.root
    if not (3 <= len(items) <= 6):
        return VerifyResult(ok=False, errors=[f"need 3-6 exercises, got {len(items)}"])
    bad = [e.type for e in items if e.type not in ALLOWED_EXERCISE_TYPES]
    if bad:
        return VerifyResult(ok=False, errors=[f"unsupported exercise type(s): {bad}"])
    return VerifyResult(ok=True)


# --- skill resolver --------------------------------------------------------


@skill_resolver("lang")
def resolve_lang_skill(ref: str, context: WorkflowContext) -> str:
    """Map '@lang' to the phraseforge-lang skill's SKILL.md path for the
    detected language (Mandarin distinguishes script)."""

    lang = (context.data.get("language") or "").lower()
    if not lang:
        raise ValueError("cannot resolve @lang: no detected language in context")
    if lang == "cmn":
        script = (context.data.get("script") or "hans").lower()
        name = f"phraseforge-lang-cmn-{'hant' if script == 'hant' else 'hans'}"
    else:
        name = f"phraseforge-lang-{lang}"
    return str(_PI_SKILLS_DIR / name / "SKILL.md")
