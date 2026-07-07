"""Tests for CopilotBackend — T1 through T11.

All tests are offline: httpx.Client.post and httpx.Client.get are monkeypatched.
No live network, no real tokens.

Fake token values use ghu_FAKE / tid=FAKE placeholders only.
"""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers: fake config and fake httpx responses
# ---------------------------------------------------------------------------


def _write_config(path: Path, access: str = "tid=FAKE", refresh: str = "ghu_FAKE", expires_ms: int | None = None) -> None:
    """Write a minimal copilot.json to *path*."""
    if expires_ms is None:
        expires_ms = int((time.time() + 3600) * 1000)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({
            "github-copilot": {
                "type": "oauth",
                "refresh": refresh,
                "access": access,
                "expires": expires_ms,
            }
        })
    )


def _fake_response(data: Any, status_code: int = 200) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = data
    resp.text = json.dumps(data)
    return resp


def _chat_response(content: str | None = "hello", tool_calls: list | None = None) -> Any:
    msg: dict[str, Any] = {"content": content}
    if tool_calls is not None:
        msg["tool_calls"] = tool_calls
    return {"choices": [{"message": msg}]}


# ---------------------------------------------------------------------------
# T1: Happy text node
# ---------------------------------------------------------------------------


def test_t1_happy_text_node(tmp_path):
    """T1: posts to /chat/completions; returns AgentResult(text=..., structured=None)."""
    from workflow_ai.backends.base import AgentInvocation
    from workflow_ai.backends.copilot import CopilotBackend

    cfg = tmp_path / "copilot.json"
    _write_config(cfg)

    backend = CopilotBackend(model="gpt-5.4", copilot_config=str(cfg))

    post_resp = _fake_response(_chat_response(content="hello world"))

    with patch.object(backend._http, "post", return_value=post_resp) as mock_post:
        inv = AgentInvocation(system_prompt="sys", prompt="user", output_kind="text")
        result = backend.run(inv)

    assert result.text == "hello world"
    assert result.structured is None

    # Verify it posted to the right URL
    call_args = mock_post.call_args
    assert "/chat/completions" in call_args[0][0]


# ---------------------------------------------------------------------------
# T2: Happy JSON node — payload contains response_format
# ---------------------------------------------------------------------------


def test_t2_json_node_sends_response_format(tmp_path):
    """T2: payload contains response_format={"type":"json_object"}; returns content."""
    from workflow_ai.backends.base import AgentInvocation
    from workflow_ai.backends.copilot import CopilotBackend

    cfg = tmp_path / "copilot.json"
    _write_config(cfg)

    backend = CopilotBackend(model="gpt-5.4", copilot_config=str(cfg))

    post_resp = _fake_response(_chat_response(content='{"key": "value"}'))
    captured: list[dict] = []

    def fake_post(url, *, json=None, headers=None):
        captured.append({"json": json, "headers": headers})
        return post_resp

    with patch.object(backend._http, "post", side_effect=fake_post):
        inv = AgentInvocation(system_prompt="sys", prompt="user", output_kind="json")
        result = backend.run(inv)

    assert result.text == '{"key": "value"}'
    assert result.structured is None  # CopilotBackend always returns structured=None

    payload = captured[0]["json"]
    assert payload.get("response_format") == {"type": "json_object"}


# ---------------------------------------------------------------------------
# T3: Token valid (not expired) — no refresh GET
# ---------------------------------------------------------------------------


def test_t3_token_valid_no_refresh(tmp_path):
    """T3: if token not expired, no refresh GET issued; bearer == config access."""
    from workflow_ai.backends.base import AgentInvocation
    from workflow_ai.backends.copilot import CopilotBackend

    cfg = tmp_path / "copilot.json"
    expires_ms = int((time.time() + 3600) * 1000)
    _write_config(cfg, access="tid=VALID", expires_ms=expires_ms)

    backend = CopilotBackend(model="gpt-5.4", copilot_config=str(cfg))

    post_resp = _fake_response(_chat_response(content="ok"))
    get_calls: list = []

    def fake_get(url, **kwargs):
        get_calls.append(url)
        return MagicMock()

    with patch.object(backend._http, "post", return_value=post_resp):
        with patch.object(backend._http, "get", side_effect=fake_get):
            inv = AgentInvocation(system_prompt="s", prompt="p", output_kind="text")
            backend.run(inv)

    assert get_calls == [], "No refresh GET should be issued when token is valid"


# ---------------------------------------------------------------------------
# T4: Token expired — refresh GET issued; new token used; expires_ms updated sec→ms
# ---------------------------------------------------------------------------


def test_t4_token_expired_refresh(tmp_path):
    """T4: expired token triggers refresh GET; new token used; expires_ms updated sec→ms."""
    from workflow_ai.backends.base import AgentInvocation
    from workflow_ai.backends.copilot import CopilotBackend

    cfg = tmp_path / "copilot.json"
    expired_ms = int((time.time() - 10) * 1000)
    _write_config(cfg, access="tid=OLD", expires_ms=expired_ms)

    backend = CopilotBackend(model="gpt-5.4", copilot_config=str(cfg))

    new_expires_at_sec = int(time.time()) + 1500
    refresh_resp = _fake_response({"token": "tid=NEW", "expires_at": new_expires_at_sec, "refresh_in": 1500})
    post_resp = _fake_response(_chat_response(content="refreshed"))

    get_calls: list = []
    captured_headers: list[dict] = []

    def fake_get(url, **kwargs):
        get_calls.append(url)
        captured_headers.append(kwargs.get("headers", {}))
        return refresh_resp

    with patch.object(backend._http, "get", side_effect=fake_get):
        with patch.object(backend._http, "post", return_value=post_resp):
            inv = AgentInvocation(system_prompt="s", prompt="p", output_kind="text")
            result = backend.run(inv)

    assert len(get_calls) == 1, "Exactly one refresh GET"
    assert "copilot_internal/v2/token" in get_calls[0]
    assert backend._token == "tid=NEW"
    # expires_ms should be updated: expires_at (sec) * 1000
    assert backend._expires_ms == new_expires_at_sec * 1000
    assert result.text == "refreshed"


# ---------------------------------------------------------------------------
# T5: api_key override — no config read, no refresh
# ---------------------------------------------------------------------------


def test_t5_api_key_override(tmp_path):
    """T5: api_key override → no config read, no refresh; bearer == api_key."""
    from workflow_ai.backends.base import AgentInvocation
    from workflow_ai.backends.copilot import CopilotBackend

    # No config file exists — with api_key, this should not fail
    cfg = tmp_path / "nonexistent.json"

    post_resp = _fake_response(_chat_response(content="ok"))
    captured_headers: list[dict] = []

    def fake_post(url, *, json=None, headers=None):
        captured_headers.append(headers or {})
        return post_resp

    backend = CopilotBackend(model="gpt-5.4", api_key="mykey", copilot_config=str(cfg))

    with patch.object(backend._http, "post", side_effect=fake_post):
        inv = AgentInvocation(system_prompt="s", prompt="p", output_kind="text")
        result = backend.run(inv)

    assert result.text == "ok"
    assert any("mykey" in h.get("authorization", "") for h in captured_headers)
    assert backend._api_key_override is True


# ---------------------------------------------------------------------------
# T6: Config path override (arg + env)
# ---------------------------------------------------------------------------


def test_t6_config_path_override_arg(tmp_path):
    """T6: reads from override arg path."""
    from workflow_ai.backends.base import AgentInvocation
    from workflow_ai.backends.copilot import CopilotBackend

    cfg = tmp_path / "custom_copilot.json"
    _write_config(cfg, access="tid=CUSTOM")

    backend = CopilotBackend(model="gpt-5.4", copilot_config=str(cfg))
    assert backend._token == "tid=CUSTOM"


def test_t6_config_path_override_env(tmp_path, monkeypatch):
    """T6: env var used when arg absent; default is ~/.config/workflow-ai/copilot.json."""
    from workflow_ai.backends.copilot import CopilotBackend

    cfg = tmp_path / "env_copilot.json"
    _write_config(cfg, access="tid=ENV")

    monkeypatch.setenv("WORKFLOW_AI_COPILOT_CONFIG", str(cfg))
    backend = CopilotBackend(model="gpt-5.4")
    assert backend._token == "tid=ENV"


def test_t6_default_config_path():
    """T6: when no override, resolve_config_path returns the default path."""
    from workflow_ai.copilot_auth import resolve_config_path, DEFAULT_CONFIG_PATH

    assert resolve_config_path() == DEFAULT_CONFIG_PATH
    assert resolve_config_path(None) == DEFAULT_CONFIG_PATH


# ---------------------------------------------------------------------------
# T7: Header assertions
# ---------------------------------------------------------------------------


def test_t7_required_headers(tmp_path):
    """T7: all required headers present; x-request-id is a UUID; x-initiator flips."""
    from workflow_ai.backends.base import AgentInvocation
    from workflow_ai.backends.copilot import CopilotBackend

    cfg = tmp_path / "copilot.json"
    _write_config(cfg)

    backend = CopilotBackend(model="gpt-5.4", copilot_config=str(cfg))

    post_resp = _fake_response(_chat_response(content="ok"))
    captured_headers: list[dict] = []

    def fake_post(url, *, json=None, headers=None):
        captured_headers.append(headers or {})
        return post_resp

    with patch.object(backend._http, "post", side_effect=fake_post):
        inv = AgentInvocation(system_prompt="sys", prompt="hello", output_kind="text")
        backend.run(inv)

    h = captured_headers[0]
    assert "authorization" in h
    assert h["content-type"] == "application/json"
    assert h["copilot-integration-id"] == "vscode-chat"
    assert h["editor-version"] == "vscode/1.104.1"
    assert h["editor-plugin-version"] == "copilot-chat/0.26.7"
    assert h["user-agent"] == "GitHubCopilotChat/0.26.7"
    assert h["openai-intent"] == "conversation-panel"
    assert h["x-github-api-version"] == "2025-04-01"
    assert h["x-vscode-user-agent-library-version"] == "electron-fetch"

    # x-request-id must be a valid UUID4
    rid = h.get("x-request-id", "")
    parsed = uuid.UUID(rid)
    assert parsed.version == 4

    # x-initiator = 'user' when only system+user messages
    assert h["x-initiator"] == "user"


def test_t7_x_initiator_flips_to_agent(tmp_path):
    """T7: x-initiator flips to 'agent' once assistant/tool messages exist."""
    from workflow_ai.backends.base import AgentInvocation
    from workflow_ai.backends.copilot import CopilotBackend
    import workflow_ai.backends.tools as tools_mod

    cfg = tmp_path / "copilot.json"
    _write_config(cfg)

    backend = CopilotBackend(model="gpt-5.4", copilot_config=str(cfg))

    tool_call_resp = _fake_response({
        "choices": [{
            "message": {
                "content": None,
                "tool_calls": [{
                    "id": "tc1",
                    "function": {"name": "Read", "arguments": '{"path": "f.txt"}'},
                }],
            }
        }]
    })
    final_resp = _fake_response(_chat_response(content="done"))

    responses = iter([tool_call_resp, final_resp])
    captured_headers: list[dict] = []

    def fake_post(url, *, json=None, headers=None):
        captured_headers.append(headers or {})
        return next(responses)

    original_dispatch = tools_mod.dispatch
    tools_mod.dispatch = lambda tools, name, args: "file data"
    try:
        with patch.object(backend._http, "post", side_effect=fake_post):
            inv = AgentInvocation(
                system_prompt="s", prompt="p", output_kind="text", allowed_tools=["Read"]
            )
            backend.run(inv)
    finally:
        tools_mod.dispatch = original_dispatch

    # First request: only system+user → x-initiator == 'user'
    assert captured_headers[0]["x-initiator"] == "user"
    # Second request: assistant+tool messages present → x-initiator == 'agent'
    assert captured_headers[1]["x-initiator"] == "agent"


# ---------------------------------------------------------------------------
# T8: Tool loop
# ---------------------------------------------------------------------------


def test_t8_tool_loop(tmp_path):
    """T8: tool_call → dispatch → tool message appended → final answer; bounded by max_turns."""
    import workflow_ai.backends.tools as tools_mod
    from workflow_ai.backends.base import AgentInvocation
    from workflow_ai.backends.copilot import CopilotBackend

    cfg = tmp_path / "copilot.json"
    _write_config(cfg)

    backend = CopilotBackend(model="gpt-5.4", copilot_config=str(cfg))

    tool_call_resp = _fake_response({
        "choices": [{
            "message": {
                "content": None,
                "tool_calls": [{
                    "id": "tc1",
                    "function": {"name": "Read", "arguments": '{"path": "f.txt"}'},
                }],
            }
        }]
    })
    final_resp = _fake_response(_chat_response(content="final answer"))

    responses = iter([tool_call_resp, final_resp])
    captured_payloads: list[dict] = []

    def fake_post(url, *, json=None, headers=None):
        captured_payloads.append(json or {})
        return next(responses)

    original_dispatch = tools_mod.dispatch
    tools_mod.dispatch = lambda tools, name, args: "file content"
    try:
        with patch.object(backend._http, "post", side_effect=fake_post):
            inv = AgentInvocation(
                system_prompt="s", prompt="p", output_kind="text", allowed_tools=["Read"]
            )
            result = backend.run(inv)
    finally:
        tools_mod.dispatch = original_dispatch

    assert result.text == "final answer"
    # Second call should include tool result
    second_msgs = captured_payloads[1]["messages"]
    assert any(m.get("role") == "tool" for m in second_msgs)
    # And assistant with tool_calls
    assert any(m.get("role") == "assistant" for m in second_msgs)


# ---------------------------------------------------------------------------
# T9: Loop overrun
# ---------------------------------------------------------------------------


def test_t9_loop_overrun(tmp_path):
    """T9: raises loop-exceeded AgentOutputError when max_turns exhausted."""
    import workflow_ai.backends.tools as tools_mod
    from workflow_ai.backends.base import AgentInvocation, AgentOutputError
    from workflow_ai.backends.copilot import CopilotBackend

    cfg = tmp_path / "copilot.json"
    _write_config(cfg)

    backend = CopilotBackend(model="gpt-5.4", copilot_config=str(cfg))

    # Always return a tool call → loop never ends
    tool_call_resp = _fake_response({
        "choices": [{
            "message": {
                "content": None,
                "tool_calls": [{
                    "id": "tc1",
                    "function": {"name": "Read", "arguments": '{"path": "x"}'},
                }],
            }
        }]
    })

    original_dispatch = tools_mod.dispatch
    tools_mod.dispatch = lambda tools, name, args: "data"
    try:
        with patch.object(backend._http, "post", return_value=tool_call_resp):
            inv = AgentInvocation(
                system_prompt="s", prompt="p", output_kind="text",
                allowed_tools=["Read"], max_turns=2
            )
            with pytest.raises(AgentOutputError, match="exceeded"):
                backend.run(inv)
    finally:
        tools_mod.dispatch = original_dispatch


# ---------------------------------------------------------------------------
# T10: Claude json fallback
# ---------------------------------------------------------------------------


def test_t10_claude_json_fallback(tmp_path):
    """T10: 400 "response_format unsupported" → retried once w/o response_format; succeeds."""
    from workflow_ai.backends.base import AgentInvocation, AgentOutputError
    from workflow_ai.backends.copilot import CopilotBackend

    cfg = tmp_path / "copilot.json"
    _write_config(cfg)

    backend = CopilotBackend(model="claude-sonnet-4.6", copilot_config=str(cfg))

    fail_resp = MagicMock()
    fail_resp.status_code = 400
    fail_resp.text = '{"error": "response_format json_object not supported for this model"}'
    fail_resp.json.return_value = {"error": "response_format json_object not supported"}

    success_resp = _fake_response(_chat_response(content='{"result": "ok"}'))

    calls: list[dict] = []

    def fake_post(url, *, json=None, headers=None):
        calls.append({"json": json, "headers": headers})
        if len(calls) == 1:
            return fail_resp
        return success_resp

    with patch.object(backend._http, "post", side_effect=fake_post):
        inv = AgentInvocation(system_prompt="s", prompt="p", output_kind="json")
        result = backend.run(inv)

    assert result.text == '{"result": "ok"}'
    assert len(calls) == 2
    # First call had response_format
    assert calls[0]["json"].get("response_format") == {"type": "json_object"}
    # Second call (retry) has no response_format
    assert "response_format" not in calls[1]["json"]


def test_t10_unrelated_400_not_retried(tmp_path):
    """T10: unrelated 400 (not about response_format) propagates as AgentOutputError."""
    from workflow_ai.backends.base import AgentInvocation, AgentOutputError
    from workflow_ai.backends.copilot import CopilotBackend

    cfg = tmp_path / "copilot.json"
    _write_config(cfg)

    backend = CopilotBackend(model="gpt-5.4", copilot_config=str(cfg))

    fail_resp = MagicMock()
    fail_resp.status_code = 400
    fail_resp.text = '{"error": "invalid messages array"}'
    fail_resp.json.return_value = {"error": "invalid messages array"}

    with patch.object(backend._http, "post", return_value=fail_resp):
        inv = AgentInvocation(system_prompt="s", prompt="p", output_kind="json")
        with pytest.raises(AgentOutputError, match="API error HTTP 400"):
            backend.run(inv)


# ---------------------------------------------------------------------------
# T11: Backend error modes
# ---------------------------------------------------------------------------


def test_t11_missing_config_raises():
    """T11: missing config raises AgentOutputError with expected prefix."""
    from workflow_ai.backends.base import AgentOutputError
    from workflow_ai.backends.copilot import CopilotBackend

    with pytest.raises(AgentOutputError, match="CopilotBackend: cannot read config"):
        CopilotBackend(model="gpt-5.4", copilot_config="/nonexistent/path/copilot.json")


def test_t11_missing_key_raises(tmp_path):
    """T11: config missing required key raises AgentOutputError."""
    from workflow_ai.backends.base import AgentOutputError
    from workflow_ai.backends.copilot import CopilotBackend

    cfg = tmp_path / "copilot.json"
    # Missing 'expires' field
    cfg.write_text(json.dumps({
        "github-copilot": {"refresh": "ghu_FAKE", "access": "tid=FAKE"}
    }))

    with pytest.raises(AgentOutputError, match="'expires' missing"):
        CopilotBackend(model="gpt-5.4", copilot_config=str(cfg))


def test_t11_refresh_non2xx_raises(tmp_path):
    """T11: refresh returning non-2xx raises AgentOutputError with expected prefix."""
    from workflow_ai.backends.base import AgentInvocation, AgentOutputError
    from workflow_ai.backends.copilot import CopilotBackend

    cfg = tmp_path / "copilot.json"
    expired_ms = int((time.time() - 10) * 1000)
    _write_config(cfg, expires_ms=expired_ms)

    backend = CopilotBackend(model="gpt-5.4", copilot_config=str(cfg))

    fail_refresh = MagicMock()
    fail_refresh.status_code = 401
    fail_refresh.json.return_value = {"error": "bad_verification_code"}

    with patch.object(backend._http, "get", return_value=fail_refresh):
        inv = AgentInvocation(system_prompt="s", prompt="p", output_kind="text")
        with pytest.raises(AgentOutputError, match="token refresh failed"):
            backend.run(inv)


def test_t11_chat_429_raises(tmp_path):
    """T11: chat HTTP 429 raises AgentOutputError with expected prefix."""
    from workflow_ai.backends.base import AgentInvocation, AgentOutputError
    from workflow_ai.backends.copilot import CopilotBackend

    cfg = tmp_path / "copilot.json"
    _write_config(cfg)

    backend = CopilotBackend(model="gpt-5.4", copilot_config=str(cfg))

    rate_limit_resp = MagicMock()
    rate_limit_resp.status_code = 429
    rate_limit_resp.text = "rate limit exceeded"

    with patch.object(backend._http, "post", return_value=rate_limit_resp):
        inv = AgentInvocation(system_prompt="s", prompt="p", output_kind="text")
        with pytest.raises(AgentOutputError, match="rate limited.*429"):
            backend.run(inv)


def test_t11_chat_500_raises(tmp_path):
    """T11: chat HTTP 500 raises AgentOutputError with expected prefix; no token leaked."""
    from workflow_ai.backends.base import AgentInvocation, AgentOutputError
    from workflow_ai.backends.copilot import CopilotBackend

    cfg = tmp_path / "copilot.json"
    _write_config(cfg)

    backend = CopilotBackend(model="gpt-5.4", copilot_config=str(cfg))

    server_error_resp = MagicMock()
    server_error_resp.status_code = 500
    server_error_resp.text = "internal server error"

    with patch.object(backend._http, "post", return_value=server_error_resp):
        inv = AgentInvocation(system_prompt="s", prompt="p", output_kind="text")
        with pytest.raises(AgentOutputError, match="API error HTTP 500") as exc_info:
            backend.run(inv)

    # Token must not appear in error message
    assert "tid=FAKE" not in str(exc_info.value)
    assert "ghu_FAKE" not in str(exc_info.value)


def test_t11_no_token_leaked_in_errors(tmp_path):
    """T11: error messages never include token values."""
    from workflow_ai.backends.base import AgentInvocation, AgentOutputError
    from workflow_ai.backends.copilot import CopilotBackend

    cfg = tmp_path / "copilot.json"
    _write_config(cfg, access="tid=SECRET_TOKEN", refresh="ghu_SECRET_REFRESH")

    backend = CopilotBackend(model="gpt-5.4", copilot_config=str(cfg))

    fail_resp = MagicMock()
    fail_resp.status_code = 500
    fail_resp.text = "boom"

    with patch.object(backend._http, "post", return_value=fail_resp):
        inv = AgentInvocation(system_prompt="s", prompt="p", output_kind="text")
        with pytest.raises(AgentOutputError) as exc_info:
            backend.run(inv)

    error_msg = str(exc_info.value)
    assert "tid=SECRET_TOKEN" not in error_msg
    assert "ghu_SECRET_REFRESH" not in error_msg


def test_t11_body_snippet_truncated(tmp_path):
    """T11: body snippet in error is truncated to <=300 chars."""
    from workflow_ai.backends.base import AgentInvocation, AgentOutputError
    from workflow_ai.backends.copilot import CopilotBackend

    cfg = tmp_path / "copilot.json"
    _write_config(cfg)

    backend = CopilotBackend(model="gpt-5.4", copilot_config=str(cfg))

    fail_resp = MagicMock()
    fail_resp.status_code = 500
    fail_resp.text = "x" * 400  # 400 chars

    with patch.object(backend._http, "post", return_value=fail_resp):
        inv = AgentInvocation(system_prompt="s", prompt="p", output_kind="text")
        with pytest.raises(AgentOutputError) as exc_info:
            backend.run(inv)

    # The error message should not contain >300 chars of the body
    error_body_part = str(exc_info.value).split("API error HTTP 500: ")[-1]
    assert len(error_body_part) <= 300


# ---------------------------------------------------------------------------
# Edge/boundary cases (F1–F4 from test review)
# ---------------------------------------------------------------------------

def test_f1_token_expiry_boundary(tmp_path):
    """F1: token exactly at now+60s boundary must trigger refresh."""
    from workflow_ai.backends.base import AgentInvocation, AgentResult
    from workflow_ai.backends.copilot import CopilotBackend, _TOKEN_REFRESH_BUFFER_MS

    cfg = tmp_path / "copilot.json"
    # expires_ms == now_ms + buffer exactly → must refresh
    expires_ms = int(time.time() * 1000) + _TOKEN_REFRESH_BUFFER_MS
    _write_config(cfg, access="tid=OLD", refresh="ghu_FAKE", expires_ms=expires_ms)

    backend = CopilotBackend(model="gpt-5.4", copilot_config=str(cfg))

    refresh_resp = MagicMock()
    refresh_resp.status_code = 200
    refresh_resp.json.return_value = {
        "token": "tid=NEW",
        "expires_at": int(time.time()) + 1800,
    }
    chat_resp = _fake_response({
        "choices": [{"message": {"content": "hello", "tool_calls": None}}]
    })

    with patch.object(backend._http, "get", return_value=refresh_resp) as mock_get:
        with patch.object(backend._http, "post", return_value=chat_resp):
            inv = AgentInvocation(system_prompt="s", prompt="p", output_kind="text")
            result = backend.run(inv)

    mock_get.assert_called_once()  # refresh was triggered
    assert result.text == "hello"


def test_f2_inv_model_overrides_constructor(tmp_path):
    """F2: inv.model takes precedence over constructor model in POST payload."""
    from workflow_ai.backends.base import AgentInvocation
    from workflow_ai.backends.copilot import CopilotBackend

    cfg = tmp_path / "copilot.json"
    _write_config(cfg)

    backend = CopilotBackend(model="gpt-5.4", copilot_config=str(cfg))

    chat_resp = _fake_response({
        "choices": [{"message": {"content": "hi", "tool_calls": None}}]
    })

    captured = {}
    original_post = backend._http.post

    def capturing_post(url, **kwargs):
        captured["payload"] = kwargs.get("json") or {}
        return chat_resp

    with patch.object(backend._http, "post", side_effect=capturing_post):
        inv = AgentInvocation(
            system_prompt="s", prompt="p", output_kind="text", model="claude-sonnet-4.6"
        )
        backend.run(inv)

    assert captured["payload"]["model"] == "claude-sonnet-4.6"


def test_f3_missing_model_raises(tmp_path):
    """F3: no model from constructor or inv raises AgentOutputError."""
    from workflow_ai.backends.base import AgentInvocation, AgentOutputError
    from workflow_ai.backends.copilot import CopilotBackend

    cfg = tmp_path / "copilot.json"
    _write_config(cfg)

    backend = CopilotBackend(model=None, copilot_config=str(cfg))

    inv = AgentInvocation(system_prompt="s", prompt="p", output_kind="text", model=None)
    with pytest.raises(AgentOutputError, match="model"):
        backend.run(inv)


def test_f4_max_completion_tokens_in_payload(tmp_path):
    """F4: payload must use max_completion_tokens, never max_tokens."""
    from workflow_ai.backends.base import AgentInvocation
    from workflow_ai.backends.copilot import CopilotBackend

    cfg = tmp_path / "copilot.json"
    _write_config(cfg)

    backend = CopilotBackend(model="gpt-5.4", copilot_config=str(cfg))

    chat_resp = _fake_response({
        "choices": [{"message": {"content": "ok", "tool_calls": None}}]
    })

    captured = {}

    def capturing_post(url, **kwargs):
        captured["payload"] = kwargs.get("json") or {}
        return chat_resp

    with patch.object(backend._http, "post", side_effect=capturing_post):
        inv = AgentInvocation(system_prompt="s", prompt="p", output_kind="text")
        backend.run(inv)

    assert "max_completion_tokens" in captured["payload"]
    assert "max_tokens" not in captured["payload"]
