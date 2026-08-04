"""Output schemas for the research workflow.

classify (model) → gather (action) → synthesize (model) → report (action).
The model reasons (classify/synthesize); actions do IO (gather/report).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from ..registry import schema


@schema("classify_out")
class ClassifyOut(BaseModel):
    topic: str = Field(description="Normalised research topic.")
    rationale: str
    depth: Literal["quick", "background", "deep"] = Field(
        default="quick",
        description="Report depth: quick ~500-1000w (default), background ~1500-3000w, deep 3000+.",
    )


class Finding(BaseModel):
    claim: str = Field(description="One discrete, sourced factual finding.")
    key: str = Field(description="Citation key, e.g. local:<file>, wiki:<url>, arxiv:<id>, web:<url>.")
    source: str = Field(default="", description="URL / DOI / ISBN / file path backing the claim.")
    retrieved: str = Field(default="", description="Retrieval date (ISO) for web sources.")


@schema("gather_out")
class GatherOut(BaseModel):
    findings: list[Finding] = Field(default_factory=list)


class Section(BaseModel):
    heading: str
    body: str = Field(description="Markdown prose with inline [key] citations matching findings.")


@schema("synthesize_out")
class SynthesizeOut(BaseModel):
    title: str
    summary: str = Field(description="~60-100 word plain-language summary of the main finding(s).")
    sections: list[Section] = Field(default_factory=list, description="H2 body, one per sub-topic.")
    open_questions: list[str] = Field(
        default_factory=list, description="Things sources disagreed on or did not cover."
    )
    confidence: Literal["High", "Medium", "Low"]


@schema("report_out")
class ReportOut(BaseModel):
    report_path: str = Field(description="Path to the written report file (.md).")
