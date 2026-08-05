# AGENTS.md — workflow-ai

Context for AI agents working in this repo. (Complements `CLAUDE.md`; survives independent of any
external tooling.)

## What this is

**workflow-ai** is a strict-DAG harness that drives an LLM through a declaratively-defined workflow
graph — *the framework owns control flow, not the model*. Its main product here is the **`ebook`**
workflow, which generates language-learning lessons (and generic prose chapters) and renders them to
`{start-*}`-fenced Markdown — the same format the `cli-tools` ebook builder and the `epub-public`
content repo consume. (There is also a `research` workflow.)

## Commands

```bash
uv sync                                             # install deps
uv run workflow-ai list                             # list workflows
uv run workflow-ai validate ebook                   # DAG-validate (do this after editing workflow.yaml/definitions.py)
uv run workflow-ai run ebook "prompt" --out runs/o  # run
uv run pytest -q                                    # all tests
# generate a language lesson (see README for full flags):
uv run workflow-ai ebook --kind lang --lang cmn --script hans --source <url|file> ...
```

## Architecture (see CLAUDE.md for the engine internals)

- Each workflow = `src/workflow_ai/<name>/` with `workflow.yaml` (graph: nodes, roles, prompts, edges,
  `skills:`, `schema`/`verifier`/`updater`/`router` names), `schemas.py` (Pydantic output contracts),
  `definitions.py` (verifiers/updaters/actions/routers/skill-resolvers, decorator-registered), `__init__.py`.
- `engine.py` runs each node: produce output (model or action) → Pydantic-validate → `@verifier` →
  retry on fail → `@updater` folds into `WorkflowContext.data` → resolve next edge (`@router` / static /
  model-chosen). Backends in `backends/` (anthropic_sdk, openai_sdk, copilot) implement `run(AgentInvocation)`.

## ⚠️ The load-bearing facts for lesson QUALITY (hard-won — read before editing skills)

1. **Only two things reach the model in a generation node:** the injected **skill files** (`skills:` in
   workflow.yaml, resolved by `engine.py:_inject_skills`) and the **JSON tool-schema field descriptions**
   (`schemas.py` `Field(description=…)`, sent via tool-forcing / `response_format`). Everything else
   (node `role`/`prompt`, `reads`) is context, but the skill + schema descriptions are the authority.
2. **`@lang` injects ONLY `skills/lang/phraseforge-lang-<iso>/SKILL.md`** (resolver in
   `definitions.py`). The lang skill must be SELF-CONTAINED. The `skills/core/**` and `skills/web/**`
   reference files are **NOT injected into this DAG** — any "follow `references/…`" pointer inside a lang
   skill is a **dead link** for generation. The shared line-format rules live in the injected
   **`skills/format/SKILL.md`** (`phraseforge-entry-format`), added to the `vocabulary` + `models` nodes
   alongside `@lang`.
3. **Weak models (Qwen/GLM) copy examples literally** (the lang skills say so). Therefore **every EXAMPLE
   in a skill must obey every rule** — a wrong example beats correct prose. Most past bugs were
   example/table contradictions.

## Lesson entry contract (vocabulary & models JSON → `render.py` → `{start-*}` line)

Model returns JSON objects (`VocabularyEntry`/`ModelEntry` in `schemas.py`); `render.py` assembles the
line `headword {grammar} [transcription] = translation (notes)`. Field rules (in schema descriptions +
`skills/format/SKILL.md`):
- **grammar**: tag CONTENT only, NO braces (renderer adds them). `render.py` also strips outer `{}`/`[]`
  defensively so a model that includes them can't produce `{{…}}`.
- **transcription**: REQUIRED inline on every entry for non-Latin scripts; null for Latin/Cyrillic/Greek.
- **notes**: null for almost every entry — add only when genuinely ambiguous (no HSK levels / literal glosses).
- **translation**: reader-language gloss; FULL native diacritics (Polish `ą ę ć ł ń ó ś ź ż`), never ASCII-stripped; multi-sense joined with `; `.
- Chinese measure words: Chinese character in the tag (`{N 个}` / `{N 隻}`) or omit — never romanized `cl ge`.

## Transcription system (lessons)

- **12 non-Latin languages require transcription**; the rest (Latin/Cyrillic/Greek) skip it. The router
  `branch_on_script` (`definitions.py`) sends a script to `transcribe` unless it's in
  `NO_TRANSCRIPTION_SCRIPTS = {latn, cyrl, grek}` — **Korean (kore) IS transcribed** (regression-tested in
  `tests/test_ebook_route_transcription.py`).
- Standards (kept, do not swap without asking): DIN 31635 — arb/apc/arz/fas (Persian-adapted: خ→`x`, ح→`ḥ`
  kept as grapheme, emphatics→plain); ULY (uig, post-2008 uses `ë` not `é`); SBL (heb, Modern-Hebrew
  simplifications); YIVO (yid); IAST (hin, with an explicit schwa-deletion rule); Pinyin (cmn-hans/hant);
  Hepburn macrons-only (jpn); Revised Romanization (kor).
- **Philosophy: HYBRID** — the standard's letters are the scientific base, plus natural-reading rules
  (assimilation, supply unwritten vowels, drop silent endings) so it reads the way a native reads.
  **Full scientific diacritics kept.** Each lang skill's `## Transcription` follows a **5-part template**:
  system line → **Letter correspondences** table (hand-editable source of truth) → **Reading rules
  (natural)** → **Typography** (Latin punctuation + sentence/proper-noun capitalization) → worked **Example**.
- Transcription is Latin script → Latin punctuation + capitalization (never the source script's `，。？！`).

## Where things live

`src/workflow_ai/ebook/`: `workflow.yaml`, `schemas.py`, `definitions.py`, `render.py`,
`skills/{core,web,compact}/` (reference material for a full-agent path — NOT injected into the DAG),
`skills/format/SKILL.md` (injected entry-format contract), `skills/lang/phraseforge-lang-<iso>/SKILL.md`
(per-language, injected via `@lang`). Tests in `tests/`.

## Conventions

- `lang` = ISO 639-3 (3-letter); `script` = ISO 15924 lowercase (4-letter). Reader/translation language
  default Polish. Keep all Polish diacritics everywhere.
- After editing `workflow.yaml`/`definitions.py`, run `uv run workflow-ai validate ebook`. After editing
  skills, run `uv run pytest tests/test_ebook_skills_bundle.py -q`. Keep the full suite green.
- Do not commit unless asked. Do not edit generated lesson output in `epub-public` to fix a generation
  problem — fix the generator (skills / schema descriptions / render / node roles) instead.
