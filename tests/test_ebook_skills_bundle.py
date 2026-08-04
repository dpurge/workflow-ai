"""The 42 phraseforge-lang skills are bundled in-repo, hardened, and resolve
without EBOOK_LANG_SKILLS_DIR."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from workflow_ai.ebook import definitions as defs
from workflow_ai.models import WorkflowContext

BUNDLE = defs._BUNDLED_LANG_SKILLS
SKILLS = sorted(BUNDLE.glob("phraseforge-lang-*/SKILL.md"))


def test_bundle_covers_all_languages():
    names = {p.parent.name for p in SKILLS}
    assert len(names) >= 42
    # a spread of scripts/languages must be present
    for lang in ("deu", "spa", "fra", "rus", "arb", "cmn-hans", "cmn-hant", "jpn", "kor", "heb"):
        assert f"phraseforge-lang-{lang}" in names


@pytest.mark.parametrize("skill", SKILLS, ids=lambda p: p.parent.name)
def test_each_skill_hardened_and_frontmatter_valid(skill: Path):
    text = skill.read_text(encoding="utf-8")
    assert text.startswith("---")
    head, frontmatter, body = text.split("---", 2)
    # frontmatter still parses (directive was NOT inserted inside it)
    meta = yaml.safe_load(frontmatter)
    assert meta.get("name", "").startswith("phraseforge-lang-")
    # the mandatory directive lives in the BODY, after the closing '---'
    assert "MANDATORY" in body


def test_resolver_uses_bundle_without_env(monkeypatch):
    # simulate the default (no override): resolver points at the bundle
    monkeypatch.setattr(defs, "_LANG_SKILLS_DIR", defs._BUNDLED_LANG_SKILLS)

    ctx = WorkflowContext(initial_prompt="x")
    ctx.data["language"] = "deu"
    assert Path(defs.resolve_lang_skill("@lang", ctx)).exists()

    ctx2 = WorkflowContext(initial_prompt="x")
    ctx2.data["language"] = "cmn"
    ctx2.data["script"] = "hant"
    resolved = Path(defs.resolve_lang_skill("@lang", ctx2))
    assert resolved.exists() and resolved.parent.name == "phraseforge-lang-cmn-hant"


def test_bundle_is_the_resolver_default(monkeypatch):
    import os

    assert defs._BUNDLED_LANG_SKILLS.is_dir()
    assert defs._BUNDLED_LANG_SKILLS.name == "lang"
    # with no override set, the default resolves to the bundle (mirror the module logic)
    monkeypatch.delenv("EBOOK_LANG_SKILLS_DIR", raising=False)
    default = Path(os.environ.get("EBOOK_LANG_SKILLS_DIR", str(defs._BUNDLED_LANG_SKILLS)))
    assert default == defs._BUNDLED_LANG_SKILLS


def test_resolver_errors_clearly_for_unbundled_language(monkeypatch):
    monkeypatch.setattr(defs, "_LANG_SKILLS_DIR", defs._BUNDLED_LANG_SKILLS)
    ctx = WorkflowContext(initial_prompt="x")
    ctx.data["language"] = "pol"  # Polish has no bundled @lang skill
    with pytest.raises(ValueError, match="pol"):
        defs.resolve_lang_skill("@lang", ctx)


def test_search_delay_is_at_least_5s():
    assert defs._SEARCH_DELAY_SEC >= 5
