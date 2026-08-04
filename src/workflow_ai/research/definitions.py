"""Actions, verifiers, and updaters for the research workflow.

Evidence gathering is deterministic (`gather_evidence`): the local knowledge
base (RAG) takes precedence, and only on an empty result does it fall through to
Wikipedia → arXiv → web, in that order — mirroring the research command. The
report is assembled deterministically (`write_report`) into the standard report
shape, so no backend Write/WebSearch tool is required (robust on copilot /
openrouter).
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from ..models import VerifyResult, WorkflowContext
from ..registry import action, updater, verifier
from . import schemas  # noqa: F401 (registers classify_out/gather_out/synthesize_out/report_out)

_TOOLS_DIR = Path(__file__).parent / "tools"
_DEPTH_LIMIT = {"quick": 5, "background": 8, "deep": 12}


# --- gather (deterministic multi-source) -----------------------------------


def _run_tool(name: str, query: str, *extra: str) -> list[dict[str, Any]]:
    """Run a research/tools/*.py script via `uv run --script` and parse its JSON
    list. Degrades to [] on any failure (missing uv, timeout, bad JSON). The
    tools print [] and exit 0 on failure; rag-query exits 1 on no-hits, so we
    always parse stdout regardless of the return code."""
    tool = _TOOLS_DIR / name
    uv = shutil.which("uv")
    if uv is None or not tool.exists():
        return []
    try:
        proc = subprocess.run(
            [uv, "run", "--script", str(tool), query, *extra],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=180,
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


@action("gather_evidence")
def gather_evidence(context: WorkflowContext) -> dict[str, Any]:
    """Local RAG first; on empty, fall through to Wikipedia → arXiv → web."""

    d = context.data
    topic = d.get("topic") or context.initial_prompt
    as_of = d.get("as_of") or ""
    limit = _DEPTH_LIMIT.get((d.get("depth") or "quick").lower(), 5)
    findings: list[dict[str, Any]] = []

    def add(claim: str, key: str, source: str) -> None:
        claim = " ".join((claim or "").split())[:500]
        if claim:
            findings.append({"claim": claim, "key": key, "source": source, "retrieved": as_of})

    # 1. local knowledge base (takes precedence)
    for chunk in _run_tool("rag-query.py", topic, "--top-k", str(limit)):
        file = chunk.get("file", "local")
        add(chunk.get("text", ""), f"local:{file}", file)

    # 2. external sources, in order, only when local had nothing
    if not findings:
        for wiki in _run_tool("wikipedia-search.py", topic, "--limit", str(limit)):
            add(wiki.get("summary") or wiki.get("title", ""), f"wiki:{wiki.get('url', '')}", wiki.get("url", ""))
        for arx in _run_tool("arxiv-search.py", topic, "--limit", str(limit)):
            add(arx.get("abstract") or arx.get("title", ""), f"arxiv:{arx.get('id', '')}", arx.get("pdf_url") or arx.get("id", ""))
        for web in _run_tool("web-search.py", topic, "--limit", str(limit)):
            add(web.get("snippet") or web.get("title", ""), f"web:{web.get('url', '')}", web.get("url", ""))

    return {"findings": findings}


# --- report assembly (deterministic, report-shape) -------------------------


@action("write_report")
def write_report(context: WorkflowContext) -> dict[str, Any]:
    """Assemble the standard-shape Markdown report and write it to disk."""

    d = context.data
    report = d.get("report") or {}
    title = report.get("title") or d.get("topic") or "Research report"
    lines: list[str] = [f"# {title}", ""]

    summary = (report.get("summary") or "").strip()
    if summary:
        lines += ["## Summary", "", summary, ""]

    for section in report.get("sections") or []:
        heading = (section.get("heading") or "").strip()
        body = (section.get("body") or "").strip()
        if heading or body:
            lines += [f"## {heading}".rstrip(), "", body, ""]

    open_qs = [q.strip() for q in (report.get("open_questions") or []) if q and q.strip()]
    if open_qs:
        lines += ["## Open questions", "", *[f"- {q}" for q in open_qs], ""]

    references = _references(d.get("findings") or [])
    if references:
        lines += ["## References", "", *references, ""]

    confidence = report.get("confidence")
    if confidence:
        lines += [f"_Confidence: {confidence}._", ""]

    report_dir = Path(d.get("report_dir") or ".")
    report_dir.mkdir(parents=True, exist_ok=True)
    path = report_dir / "report.md"
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return {"report_path": str(path)}


def _references(findings: list[dict[str, Any]]) -> list[str]:
    """One dedup'd reference line per finding key: `[key] source (retrieved date)`."""
    seen: set[str] = set()
    out: list[str] = []
    for f in findings:
        key = f.get("key", "")
        if not key or key in seen:
            continue
        seen.add(key)
        source = f.get("source", "")
        retrieved = f.get("retrieved", "")
        tail = f" (retrieved {retrieved})" if retrieved else ""
        out.append(f"- [{key}] {source}{tail}".rstrip())
    return out


# --- updaters --------------------------------------------------------------


@updater("store_topic")
def store_topic(output: BaseModel, context: WorkflowContext) -> WorkflowContext:
    context.data["topic"] = output.topic
    context.data["rationale"] = output.rationale
    # A --depth CLI override (already in data) wins over the model's choice.
    context.data["depth"] = context.data.get("depth") or output.depth
    return context


@updater("append_findings")
def append_findings(output: BaseModel, context: WorkflowContext) -> WorkflowContext:
    context.data.setdefault("findings", [])
    context.data["findings"].extend(f.model_dump() for f in output.findings)
    return context


@updater("store_report")
def store_report(output: BaseModel, context: WorkflowContext) -> WorkflowContext:
    context.data["report"] = output.model_dump()
    return context


# --- verifiers -------------------------------------------------------------


@verifier("topic_present")
def topic_present(output: BaseModel, context: WorkflowContext) -> VerifyResult:
    if not getattr(output, "topic", "").strip():
        return VerifyResult(ok=False, errors=["topic must not be empty"])
    return VerifyResult(ok=True)


@verifier("nonempty_findings")
def nonempty_findings(output: BaseModel, context: WorkflowContext) -> VerifyResult:
    if not getattr(output, "findings", []):
        return VerifyResult(ok=False, errors=["no findings were gathered from any source"])
    return VerifyResult(ok=True)


@verifier("markdown_path")
def markdown_path(output: BaseModel, context: WorkflowContext) -> VerifyResult:
    path = getattr(output, "report_path", "") or ""
    if not path.endswith(".md"):
        return VerifyResult(ok=False, errors=[f"report_path must end with .md, got: {path!r}"])
    return VerifyResult(ok=True)
