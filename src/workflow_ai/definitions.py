"""Registered schemas, verifiers, and updaters for the sample workflows.

Importing this module populates the registry. The CLI imports it before
loading any graph so that name references resolve.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from .models import VerifyResult, WorkflowContext
from .registry import schema, updater, verifier

# --- research workflow -------------------------------------------------------


@schema("classify_out")
class ClassifyOut(BaseModel):
    topic: str = Field(description="Normalised research topic.")
    rationale: str
    # Fan-out node: choose which gathering branches to run.
    next_states: list[Literal["search_web", "read_files"]] = Field(
        description="One or more gathering steps to run as parallel branches."
    )


@schema("gather_out")
class GatherOut(BaseModel):
    findings: list[str] = Field(description="Discrete factual findings with sources.")
    next_state: Literal["synthesize"]


@schema("synthesize_out")
class SynthesizeOut(BaseModel):
    summary: str
    confidence: Literal["High", "Medium", "Low"]
    next_state: Literal["report"]


@schema("report_out")
class ReportOut(BaseModel):
    report_path: str = Field(description="Path to the written report file.")
    next_state: Literal["done"]


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
