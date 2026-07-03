"""Registry registration and lookup tests."""

from __future__ import annotations

import pytest

from workflow_ai import definitions  # noqa: F401
from workflow_ai import registry
from workflow_ai.models import VerifyResult, WorkflowContext


def test_schema_lookup():
    cls = registry.get_schema("classify_out")
    assert cls.__name__ == "ClassifyOut"


def test_unknown_schema_raises():
    with pytest.raises(KeyError):
        registry.get_schema("nope")


def test_default_verifier_passes():
    ctx = WorkflowContext(initial_prompt="x")
    out = registry.get_schema("report_out").model_validate(
        {"report_path": "/r", "next_state": "done"}
    )
    result = registry.get_verifier(None)(out, ctx)
    assert isinstance(result, VerifyResult)
    assert result.ok


def test_registered_verifier_flags_empty_findings():
    ctx = WorkflowContext(initial_prompt="x")
    out = registry.get_schema("gather_out").model_validate(
        {"findings": [], "next_state": "synthesize"}
    )
    result = registry.get_verifier("nonempty_findings")(out, ctx)
    assert not result.ok


def test_updater_appends_findings():
    ctx = WorkflowContext(initial_prompt="x")
    out = registry.get_schema("gather_out").model_validate(
        {"findings": ["f1"], "next_state": "synthesize"}
    )
    ctx = registry.get_updater("append_findings")(out, ctx)
    assert ctx.data["findings"] == ["f1"]
