"""Per-step Pydantic output contracts for the ebook workflow.

These are the small per-step schemas each model node returns. The chapter
itself is assembled and rendered in `render.py`; the ebook format has no
exercise block, so there is no exercise schema here.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field, RootModel, field_validator


class DetectOut(BaseModel):
    language: str = Field(description="ISO 639-3, e.g. deu, arb, cmn")
    script: str = Field(description="ISO 15924 lowercase, e.g. latn, arab, hans")
    title: str

    @field_validator("language")
    @classmethod
    def _iso639(cls, v: str) -> str:
        if not re.fullmatch(r"[a-z]{3}", v):
            raise ValueError("language must be ISO 639-3 (3 lowercase letters)")
        return v

    @field_validator("script")
    @classmethod
    def _iso15924(cls, v: str) -> str:
        if not re.fullmatch(r"[a-z]{4}", v):
            raise ValueError("script must be ISO 15924 (4 lowercase letters)")
        return v


class VocabularyEntry(BaseModel):
    headword: str
    grammar: str | None = None
    transcription: str | None = None
    translation: str | None = None
    notes: str | None = None


class ModelEntry(BaseModel):
    pattern: str
    translation: str
    transcription: str | None = None
    notes: str | None = None


class VocabularyList(RootModel[list[VocabularyEntry]]):
    pass


class ModelList(RootModel[list[ModelEntry]]):
    pass


class QuestionList(RootModel[list[str]]):
    pass


# --- compose-from-topic path -----------------------------------------------


class QueriesOut(BaseModel):
    queries: list[str] = Field(description="Target-language web-search queries about the topic.")
    rationale: str | None = None


class DialogTurnOut(BaseModel):
    speaker: str | None = None
    text: str = Field(description="One turn's utterance in the TARGET language.")
    translation: str | None = Field(
        default=None,
        description="This turn's translation in the reader's (book) language — fill it "
        "for dialog lessons so the reader gets a parallel translated dialog.",
    )


class ComposeOut(BaseModel):
    form: Literal["text", "dialog"]
    title: str | None = None
    text: str = Field(
        description="Flattened learner text (prose, or dialog turns joined) — used by the "
        "downstream steps and the grounding check; always populated."
    )
    turns: list[DialogTurnOut] | None = Field(
        default=None, description="Dialog turns, only when form == dialog."
    )
