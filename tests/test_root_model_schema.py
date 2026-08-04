"""Engine + backend handling of RootModel[list[...]] schemas.

OpenAI and Anthropic structured-output APIs require a top-level ``type:
"object"`` JSON Schema.  A Pydantic ``RootModel[list[...]]`` produces ``type:
"array"``, which both APIs reject.  The fix wraps the array schema in an object
for the API call and unwraps the result before returning it to the engine.

These tests verify:
  1. ``_schema_template`` generates an array-shaped prompt for RootModel[list].
  2. ``_validate_json`` unwraps ``{"items": [...]}`` payloads (text fallback path).
  3. The helpers in ``backends.base`` correctly detect and wrap RootModel[list].
"""

from __future__ import annotations

import json

import pytest
from pydantic import BaseModel, RootModel

from workflow_ai.backends.base import (
    is_root_list_schema,
    unwrap_root_list_payload,
    wrap_root_list_schema,
)


class _Entry(BaseModel):
    name: str
    value: int | None = None


class _EntryList(RootModel[list[_Entry]]):
    pass


class _StrList(RootModel[list[str]]):
    pass


class _NormalOut(BaseModel):
    title: str
    count: int = 0


# ---------------------------------------------------------------------------
# Helper tests
# ---------------------------------------------------------------------------


def test_is_root_list_schema_detects_root_model_list():
    assert is_root_list_schema(_EntryList) is True
    assert is_root_list_schema(_StrList) is True


def test_is_root_list_schema_rejects_normal_model():
    assert is_root_list_schema(_NormalOut) is False


def test_is_root_list_schema_rejects_none():
    assert is_root_list_schema(None) is False


def test_wrap_creates_object_schema_with_items_field():
    wrapper, was_wrapped = wrap_root_list_schema(_EntryList)
    assert was_wrapped is True
    assert wrapper is not _EntryList
    assert issubclass(wrapper, BaseModel)
    assert not issubclass(wrapper, RootModel)
    assert "items" in wrapper.model_fields

    json_schema = wrapper.model_json_schema()
    assert json_schema["type"] == "object"
    assert "items" in json_schema["properties"]


def test_wrap_passthrough_for_normal_model():
    wrapper, was_wrapped = wrap_root_list_schema(_NormalOut)
    assert was_wrapped is False
    assert wrapper is _NormalOut


def test_unwrap_extracts_items_list():
    payload = {"items": [{"name": "a"}, {"name": "b"}]}
    result = unwrap_root_list_payload(payload, was_wrapped=True)
    assert result == [{"name": "a"}, {"name": "b"}]


def test_unwrap_passthrough_when_not_wrapped():
    payload = {"title": "x", "count": 1}
    result = unwrap_root_list_payload(payload, was_wrapped=False)
    assert result is payload


def test_unwrap_passthrough_when_not_dict():
    result = unwrap_root_list_payload([1, 2, 3], was_wrapped=True)
    assert result == [1, 2, 3]


# ---------------------------------------------------------------------------
# Engine _schema_template tests
# ---------------------------------------------------------------------------


def test_schema_template_root_list_returns_array_template():
    from workflow_ai.engine import _schema_template

    template = _schema_template(_EntryList)
    assert isinstance(template, list)
    assert len(template) == 1
    entry = template[0]
    assert "name" in entry
    assert "value" in entry


def test_schema_template_root_list_of_strings():
    from workflow_ai.engine import _schema_template

    template = _schema_template(_StrList)
    assert isinstance(template, list)
    assert template == ["<item>"]


def test_schema_template_normal_model_returns_dict():
    from workflow_ai.engine import _schema_template

    template = _schema_template(_NormalOut)
    assert isinstance(template, dict)
    assert "title" in template
    assert "count" in template


# ---------------------------------------------------------------------------
# Engine _validate_json fallback unwrap
# ---------------------------------------------------------------------------


def test_validate_json_unwraps_items_wrapper():
    """When the text/json_object path produces {"items": [...]}, the engine
    should unwrap it before validating against the RootModel[list] schema."""
    from workflow_ai import registry
    from workflow_ai.engine import Engine
    from workflow_ai.graph import NodeSpec

    # Register the schema under a test name.
    registry._SCHEMAS["test_entry_list"] = _EntryList

    node = NodeSpec(
        id="test_node",
        kind="model",
        role="test",
        prompt="test",
        successors=["done"],
        output_kind="json",
        schema_name="test_entry_list",
    )
    engine = Engine(backend=None)

    # Payload wrapped as {"items": [...]} — simulates text fallback path.
    wrapped_payload = {"items": [{"name": "a", "value": 1}, {"name": "b"}]}
    result = engine._validate_json(node, wrapped_payload)
    assert isinstance(result, _EntryList)
    assert len(result.root) == 2
    assert result.root[0].name == "a"

    # Bare list payload — the normal structured-output path.
    bare_payload = [{"name": "c"}]
    result = engine._validate_json(node, bare_payload)
    assert isinstance(result, _EntryList)
    assert len(result.root) == 1

    # Cleanup
    del registry._SCHEMAS["test_entry_list"]


# ---------------------------------------------------------------------------
# System prompt tests
# ---------------------------------------------------------------------------


def test_system_prompt_says_array_for_root_list():
    from workflow_ai.engine import _build_system_prompt
    from workflow_ai.graph import NodeSpec

    node = NodeSpec(
        id="test_node",
        kind="model",
        role="test role",
        prompt="test",
        successors=["done"],
        output_kind="json",
        schema_name="test_entry_list",
    )
    prompt = _build_system_prompt(node, _EntryList)
    assert "JSON array" in prompt
    assert "JSON object" not in prompt


def test_system_prompt_says_object_for_normal_model():
    from workflow_ai.engine import _build_system_prompt
    from workflow_ai.graph import NodeSpec

    node = NodeSpec(
        id="test_node",
        kind="model",
        role="test role",
        prompt="test",
        successors=["done"],
        output_kind="json",
        schema_name="test_normal_out",
    )
    prompt = _build_system_prompt(node, _NormalOut)
    assert "JSON object" in prompt
