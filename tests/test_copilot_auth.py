"""Tests for copilot_auth — T12, T13, T14.

All tests are offline: httpx.post and httpx.get are monkeypatched.
No live network, no real tokens.

Fake token values use ghu_FAKE / tid=FAKE placeholders only.
"""

from __future__ import annotations

import json
import stat
import time
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, call, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fake_response(data: Any, status_code: int = 200) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = data
    resp.text = json.dumps(data)
    return resp


# ---------------------------------------------------------------------------
# T12: copilot login happy path
# ---------------------------------------------------------------------------


def test_t12_login_happy_path(tmp_path, capsys):
    """T12: mocked device-code + immediate success + session exchange →
    write_creds writes correct block (0600); confirmation printed; no secret leaked.
    Also asserts (N3) that custom --client-id is forwarded to the device-code POST body.
    """
    import workflow_ai.copilot_auth as auth

    cfg_path = tmp_path / "copilot.json"
    custom_client_id = "CUSTOM_CLIENT_ID"

    now_sec = int(time.time())
    session_expires_at = now_sec + 1500

    device_code_data = {
        "device_code": "DC_SECRET",
        "user_code": "WXYZ-1234",
        "verification_uri": "https://github.com/login/device",
        "expires_in": 900,
        "interval": 5,
    }
    oauth_success_data = {
        "access_token": "ghu_NEW",
        "token_type": "bearer",
        "scope": "copilot",
    }
    session_data = {
        "token": "tid=SESSION",
        "expires_at": session_expires_at,
        "refresh_in": 1500,
    }

    device_code_resp = _fake_response(device_code_data)
    oauth_resp = _fake_response(oauth_success_data)
    session_resp = _fake_response(session_data)

    post_calls: list[tuple] = []
    get_calls: list[tuple] = []

    def fake_post(url, *, json=None, headers=None, timeout=None, **_):
        post_calls.append((url, json))
        if "device/code" in url:
            return device_code_resp
        if "access_token" in url:
            return oauth_resp
        raise AssertionError(f"Unexpected POST to {url}")

    def fake_get(url, *, headers=None, timeout=None, **_):
        get_calls.append((url, headers))
        return session_resp

    sleep_calls: list[float] = []

    with patch("httpx.post", side_effect=fake_post):
        with patch("httpx.get", side_effect=fake_get):
            with patch("time.sleep", side_effect=lambda s: sleep_calls.append(s)):
                auth.login(client_id=custom_client_id, config_path=str(cfg_path))

    out = capsys.readouterr().out

    # Confirm only user_code was printed (not device_code, not tokens)
    assert "WXYZ-1234" in out
    assert "DC_SECRET" not in out
    assert "ghu_NEW" not in out
    assert "tid=SESSION" not in out

    # Confirm file was written with correct content
    assert cfg_path.exists()
    content = json.loads(cfg_path.read_text())
    block = content["github-copilot"]
    assert block["refresh"] == "ghu_NEW"
    assert block["access"] == "tid=SESSION"
    assert block["expires"] == session_expires_at * 1000

    # Check file permissions (0600)
    mode = stat.S_IMODE(cfg_path.stat().st_mode)
    assert mode == 0o600, f"Expected 0600 got {oct(mode)}"

    # N3: custom client_id forwarded to device-code POST body
    device_post = next((c for c in post_calls if "device/code" in c[0]), None)
    assert device_post is not None, "device/code POST must be called"
    assert device_post[1]["client_id"] == custom_client_id

    # N3: custom client_id forwarded to OAuth poll POST body
    oauth_post = next((c for c in post_calls if "access_token" in c[0]), None)
    assert oauth_post is not None, "OAuth poll POST must be called"
    assert oauth_post[1]["client_id"] == custom_client_id


# ---------------------------------------------------------------------------
# T13: copilot status
# ---------------------------------------------------------------------------


def test_t13_status_valid(tmp_path, capsys):
    """T13: valid token → prints models+expiry."""
    import workflow_ai.copilot_auth as auth

    cfg_path = tmp_path / "copilot.json"
    expires_ms = int((time.time() + 3600) * 1000)
    auth.write_creds(cfg_path, refresh="ghu_FAKE", access="tid=FAKE", expires_ms=expires_ms)

    auth.status(config_path=str(cfg_path))

    out = capsys.readouterr().out
    assert "Logged in" in out or "expires" in out.lower()
    # Should show some models
    assert any(m in out for m in auth._KNOWN_MODELS)


def test_t13_status_expired(tmp_path, capsys):
    """T13: expired token → prints 'token expired'."""
    import workflow_ai.copilot_auth as auth

    cfg_path = tmp_path / "copilot.json"
    expired_ms = int((time.time() - 10) * 1000)
    auth.write_creds(cfg_path, refresh="ghu_FAKE", access="tid=FAKE", expires_ms=expired_ms)

    auth.status(config_path=str(cfg_path))

    out = capsys.readouterr().out
    assert "expired" in out.lower()


def test_t13_status_absent(tmp_path, capsys):
    """T13: absent config → prints 'not logged in'."""
    import workflow_ai.copilot_auth as auth

    cfg_path = tmp_path / "nonexistent.json"
    auth.status(config_path=str(cfg_path))

    out = capsys.readouterr().out
    assert "not logged in" in out.lower()


# ---------------------------------------------------------------------------
# T14: copilot login polling
# ---------------------------------------------------------------------------


def test_t14_polling_with_pending(tmp_path, capsys):
    """T14: first N poll responses authorization_pending, then success; sleep patched;
    slow_down increases interval.
    """
    import workflow_ai.copilot_auth as auth

    cfg_path = tmp_path / "copilot.json"

    now_sec = int(time.time())
    session_expires_at = now_sec + 1500

    device_code_data = {
        "device_code": "DC_SECRET",
        "user_code": "ABCD-5678",
        "verification_uri": "https://github.com/login/device",
        "expires_in": 900,
        "interval": 5,
    }

    pending_resp = _fake_response({"error": "authorization_pending"})
    slow_down_resp = _fake_response({"error": "slow_down", "interval": 10})
    success_resp = _fake_response({
        "access_token": "ghu_NEW2",
        "token_type": "bearer",
        "scope": "copilot",
    })
    session_resp = _fake_response({
        "token": "tid=SESSION2",
        "expires_at": session_expires_at,
        "refresh_in": 1500,
    })

    device_code_resp = _fake_response(device_code_data)

    # Sequence: pending × 2, slow_down, pending × 1, success
    oauth_responses = iter([pending_resp, pending_resp, slow_down_resp, pending_resp, success_resp])

    sleep_calls: list[float] = []

    def fake_post(url, *, json=None, headers=None, timeout=None, **_):
        if "device/code" in url:
            return device_code_resp
        if "access_token" in url:
            return next(oauth_responses)
        raise AssertionError(f"Unexpected POST to {url}")

    def fake_get(url, *, headers=None, timeout=None, **_):
        return session_resp

    with patch("httpx.post", side_effect=fake_post):
        with patch("httpx.get", side_effect=fake_get):
            with patch("time.sleep", side_effect=lambda s: sleep_calls.append(s)):
                auth.login(config_path=str(cfg_path))

    # Should have slept multiple times
    assert len(sleep_calls) >= 4

    # After slow_down, interval should have increased; look for a larger sleep value
    # Initial interval is 5; after slow_down server hints 10
    assert any(s >= 10 for s in sleep_calls), "Interval should increase after slow_down"

    # Config should be written
    assert cfg_path.exists()
    content = json.loads(cfg_path.read_text())
    assert content["github-copilot"]["refresh"] == "ghu_NEW2"


def test_t14_expired_token_aborts(tmp_path):
    """T14: expired_token error code aborts with expected message."""
    import workflow_ai.copilot_auth as auth
    from workflow_ai.backends.base import AgentOutputError

    cfg_path = tmp_path / "copilot.json"

    device_code_data = {
        "device_code": "DC",
        "user_code": "AAAA-1111",
        "verification_uri": "https://github.com/login/device",
        "expires_in": 900,
        "interval": 1,
    }
    device_code_resp = _fake_response(device_code_data)
    expired_resp = _fake_response({"error": "expired_token"})

    def fake_post(url, *, json=None, headers=None, timeout=None, **_):
        if "device/code" in url:
            return device_code_resp
        return expired_resp

    with patch("httpx.post", side_effect=fake_post):
        with patch("time.sleep", return_value=None):
            with pytest.raises(AgentOutputError, match="timed out"):
                auth.login(config_path=str(cfg_path))


def test_t14_access_denied_aborts(tmp_path):
    """T14: access_denied error code aborts with expected message."""
    import workflow_ai.copilot_auth as auth
    from workflow_ai.backends.base import AgentOutputError

    cfg_path = tmp_path / "copilot.json"

    device_code_data = {
        "device_code": "DC",
        "user_code": "BBBB-2222",
        "verification_uri": "https://github.com/login/device",
        "expires_in": 900,
        "interval": 1,
    }
    device_code_resp = _fake_response(device_code_data)
    denied_resp = _fake_response({"error": "access_denied"})

    def fake_post(url, *, json=None, headers=None, timeout=None, **_):
        if "device/code" in url:
            return device_code_resp
        return denied_resp

    with patch("httpx.post", side_effect=fake_post):
        with patch("time.sleep", return_value=None):
            with pytest.raises(AgentOutputError, match="denied"):
                auth.login(config_path=str(cfg_path))


def test_t14_overall_timeout_aborts(tmp_path):
    """T14: overall timeout_s exhausted aborts with expected message."""
    import workflow_ai.copilot_auth as auth
    from workflow_ai.backends.base import AgentOutputError

    cfg_path = tmp_path / "copilot.json"

    device_code_data = {
        "device_code": "DC",
        "user_code": "CCCC-3333",
        "verification_uri": "https://github.com/login/device",
        "expires_in": 900,  # Long device code lifetime
        "interval": 1,
    }
    device_code_resp = _fake_response(device_code_data)
    pending_resp = _fake_response({"error": "authorization_pending"})

    # Use timeout_s=2 so it expires quickly
    # We need time.time() to advance past timeout_s
    real_time = time.time
    _call_count = [0]

    def fake_time():
        # First call returns start time, subsequent calls advance past timeout
        _call_count[0] += 1
        if _call_count[0] <= 2:
            return real_time()
        return real_time() + 1000  # Way past any timeout

    def fake_post(url, *, json=None, headers=None, timeout=None, **_):
        if "device/code" in url:
            return device_code_resp
        return pending_resp

    with patch("httpx.post", side_effect=fake_post):
        with patch("time.sleep", return_value=None):
            with patch("time.time", side_effect=fake_time):
                with pytest.raises(AgentOutputError, match="timed out"):
                    auth.login(config_path=str(cfg_path), timeout_s=5)


def test_t12_write_creds_file_permissions(tmp_path):
    """T12: write_creds creates file with 0600 permissions."""
    import workflow_ai.copilot_auth as auth

    cfg_path = tmp_path / "subdir" / "copilot.json"
    auth.write_creds(
        cfg_path,
        refresh="ghu_FAKE",
        access="tid=FAKE",
        expires_ms=int(time.time() * 1000 + 3600000),
    )

    assert cfg_path.exists()
    mode = stat.S_IMODE(cfg_path.stat().st_mode)
    assert mode == 0o600, f"Expected 0600 got {oct(mode)}"

    # Check content
    content = json.loads(cfg_path.read_text())
    assert content["github-copilot"]["refresh"] == "ghu_FAKE"
    assert content["github-copilot"]["access"] == "tid=FAKE"
