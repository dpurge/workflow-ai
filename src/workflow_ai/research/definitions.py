"""Verifiers and updaters for the research workflow."""

from __future__ import annotations

from pydantic import BaseModel

from ..models import VerifyResult, WorkflowContext
from ..registry import updater, verifier


@verifier("nonempty_findings")
def nonempty_findings(output: BaseModel, context: WorkflowContext) -> VerifyResult:
    findings = getattr(output, "findings", [])
    if not findings:
        return VerifyResult(ok=False, errors=["no findings were returned"])
    return VerifyResult(ok=True)


@verifier("topic_present")
def topic_present(output: BaseModel, context: WorkflowContext) -> VerifyResult:
    if not getattr(output, "topic", "").strip():
        return VerifyResult(ok=False, errors=["topic must not be empty"])
    return VerifyResult(ok=True)


@updater("store_topic")
def store_topic(output: BaseModel, context: WorkflowContext) -> WorkflowContext:
    context.data["topic"] = output.topic
    context.data["rationale"] = output.rationale
    return context


@updater("append_findings")
def append_findings(output: BaseModel, context: WorkflowContext) -> WorkflowContext:
    context.data.setdefault("findings", [])
    context.data["findings"].extend(output.findings)
    return context


@updater("store_summary")
def store_summary(output: BaseModel, context: WorkflowContext) -> WorkflowContext:
    context.data["summary"] = output.summary
    context.data["confidence"] = output.confidence
    return context
