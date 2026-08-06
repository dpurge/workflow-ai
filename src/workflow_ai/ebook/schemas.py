"""Per-step Pydantic output contracts for the ebook workflow.

These are the small per-step schemas each model node returns. The chapter
itself is assembled and rendered in `render.py`; the ebook format has no
exercise block, so there is no exercise schema here.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


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
    phrase: str = Field(
        description="The foreign word or phrase in dictionary/citation form."
    )
    grammar: str | None = Field(
        default=None,
        description=(
            "Grammar tag CONTENT ONLY — space-separated tokens, with NO curly braces. "
            "Write e.g. `N`, `V`, `Adj`, `N m sg`. Do NOT wrap it: write `N`, never `{N}` or "
            "`{{N}}` — the renderer adds the braces. Follow the language skill for the exact "
            "token set. Omit (null) if unsure."
        ),
    )
    transcription: str | None = Field(
        default=None,
        description=(
            "Romanization of the phrase. REQUIRED for non-Latin scripts (arab, hans, jpan, "
            "kore, hebr, …): fill it for EVERY entry, never omit it, never move it to a separate "
            "list — it belongs inline on each entry. Use the romanization system the language "
            "skill specifies. Leave null ONLY for Latin/Cyrillic/Greek scripts."
        ),
    )
    translation: str | None = Field(
        default=None,
        description=(
            "Gloss in the reader's language. Join multiple senses with '; '. Write it in that "
            "language's FULL native orthography — keep EVERY diacritic (Polish ą ę ć ł ń ó ś ź ż, "
            "etc.); never ASCII-strip or substitute plain letters (książka, not ksiazka). Keep "
            "this field as the main gloss text only: if you need a parenthetical clarification "
            "that should appear at the very END of the rendered lesson line, put that content in "
            "`notes`, not inside `translation`."
        ),
    )
    notes: str | None = Field(
        default=None,
        description=(
            "Leave null for almost every entry. Add a SHORT note ONLY when the entry is genuinely "
            "ambiguous or hard to understand without it (false friend, non-obvious sense or usage, "
            "easily-confused homograph). Do NOT add HSK/level tags, literal glosses, or general "
            "'helpful' commentary — unnecessary notes make the lesson harder to read. Any "
            "parenthetical clarification that should render as the FINAL `(...)` at the end of "
            "the lesson line belongs here, not inside `translation`."
        ),
    )


class ModelEntry(BaseModel):
    phrase: str = Field(
        description="The foreign phrase (built progressively toward a full sentence)."
    )
    translation: str = Field(
        description=(
            "Gloss in the reader's language. Join multiple senses with '; '. Write it in that "
            "language's FULL native orthography — keep EVERY diacritic (Polish ą ę ć ł ń ó ś ź ż, "
            "etc.); never ASCII-strip or substitute plain letters (książka, not ksiazka). Keep "
            "this field as the main gloss text only: if you need a parenthetical clarification "
            "that should appear at the very END of the rendered lesson line, put that content in "
            "`notes`, not inside `translation`."
        )
    )
    transcription: str | None = Field(
        default=None,
        description=(
            "Romanization of the phrase. REQUIRED for non-Latin scripts (arab, hans, jpan, kore, "
            "hebr, …): fill it inline for EVERY entry, never omit. Use the language skill's system. "
            "Leave null ONLY for Latin/Cyrillic/Greek scripts."
        ),
    )
    notes: str | None = Field(
        default=None,
        description=(
            "Leave null for almost every entry. Add a short note ONLY when genuinely needed for "
            "comprehension — no literal glosses or filler. Any parenthetical clarification that "
            "should render as the FINAL `(...)` at the end of the lesson line belongs here, not "
            "inside `translation`."
        ),
    )


class VocabularyList(BaseModel):
    entries: list[VocabularyEntry] = Field(
        description="Vocabulary entries extracted from the lesson text."
    )

    @model_validator(mode="before")
    @classmethod
    def _coerce_root_list(cls, data):
        if isinstance(data, list):
            return {"entries": data}
        if isinstance(data, dict) and isinstance(data.get("entries"), list):
            coerced = []
            for entry in data["entries"]:
                if isinstance(entry, dict) and "phrase" not in entry and "headword" in entry:
                    entry = {**entry, "phrase": entry["headword"]}
                coerced.append(entry)
            return {**data, "entries": coerced}
        return data


class ModelList(BaseModel):
    entries: list[ModelEntry] = Field(
        description="Model phrase entries built from the lesson text."
    )

    @model_validator(mode="before")
    @classmethod
    def _coerce_root_list(cls, data):
        if isinstance(data, list):
            return {"entries": data}
        if isinstance(data, dict) and isinstance(data.get("entries"), list):
            coerced = []
            for entry in data["entries"]:
                if isinstance(entry, dict) and "phrase" not in entry and "pattern" in entry:
                    entry = {**entry, "phrase": entry["pattern"]}
                coerced.append(entry)
            return {**data, "entries": coerced}
        return data


class QuestionList(BaseModel):
    entries: list[str] = Field(
        description="Short open-ended comprehension questions in the lesson language."
    )

    @model_validator(mode="before")
    @classmethod
    def _coerce_root_list(cls, data):
        return {"entries": data} if isinstance(data, list) else data


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
