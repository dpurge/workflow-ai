"""Per-step Pydantic output contracts for the phraseforge workflow.

These mirror the small per-step schemas in Pi's `phraseforge-mdx.ts`. The full
Lesson schema is owned by Pi's `mdx-export.py` (the deterministic render+
validate gate); we only validate each step's output here.
"""

from __future__ import annotations

import re

from pydantic import BaseModel, Field, RootModel, field_validator

ALLOWED_EXERCISE_TYPES = {
    "translation",
    "fill-gaps",
    "word-order",
    "multiple-choice",
    "matching",
    "true-false",
    "open-answer",
}


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


class Exercise(BaseModel):
    type: str
    instruction: str | None = None
    items: list[str]


class VocabularyList(RootModel[list[VocabularyEntry]]):
    pass


class ModelList(RootModel[list[ModelEntry]]):
    pass


class QuestionList(RootModel[list[str]]):
    pass


class ExerciseList(RootModel[list[Exercise]]):
    pass
