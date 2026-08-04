"""CLI: --kind is required (generic|lang) for the ebook workflow."""

from __future__ import annotations

from typer.testing import CliRunner

from workflow_ai.cli import app

runner = CliRunner()


def test_ebook_requires_kind():
    result = runner.invoke(app, ["run", "ebook", "--backend", "copilot", "--prompt", "x"])
    assert result.exit_code != 0
    assert "kind" in result.output.lower()


def test_ebook_rejects_invalid_kind():
    result = runner.invoke(app, ["run", "ebook", "--backend", "copilot", "--kind", "bogus"])
    assert result.exit_code != 0
    assert "kind" in result.output.lower()


def test_kind_lang_requires_lang_and_script():
    result = runner.invoke(app, ["run", "ebook", "--backend", "copilot", "--kind", "lang", "--prompt", "x"])
    assert result.exit_code != 0
    assert "--lang" in result.output


def test_kind_lang_requires_script_when_only_lang_given():
    # the gate fires if EITHER --lang or --script is missing
    result = runner.invoke(app, ["run", "ebook", "--backend", "copilot", "--kind", "lang", "--lang", "ron"])
    assert result.exit_code != 0
    assert "--lang" in result.output or "--script" in result.output


def test_no_registry_collision_with_research():
    """Guard the fixed global-registry collision: importing both packages must
    keep each workflow's actions bound to its own module (names are global)."""
    import workflow_ai.ebook  # noqa: F401
    import workflow_ai.research  # noqa: F401
    from workflow_ai import registry

    assert "research" in registry.get_action("gather_evidence").__module__
    assert "ebook" in registry.get_action("gather_lang_evidence").__module__
