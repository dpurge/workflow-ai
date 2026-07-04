"""Output schemas for the research workflow."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from ..registry import schema


@schema("classify_out")
class ClassifyOut(BaseModel):
    topic: str = Field(description="Normalised research topic.")
    rationale: str
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
