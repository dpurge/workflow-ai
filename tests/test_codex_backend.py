"""Codex backend: subprocess argv shape, output parsing, and error handling."""

from __future__ import annotations

import subprocess

import pytest

from workflow_ai.backends.base import AgentInvocation, AgentOutputError
from workflow_ai.backends.codex import CodexBackend


def _inv(**kwargs) -> AgentInvocation:
    defaults = dict(system_prompt="SYS", prompt="DO IT", output_kind="text")
    return AgentInvocation(**{**defaults, **kwargs})


def _backend(monkeypatch, **kwargs) -> CodexBackend:
    backend = CodexBackend(**kwargs)
    monkeypatch.setattr(
        "workflow_ai.backends.codex.shutil.which",
        lambda _e: "/usr/local/bin/codex",
    )
    return backend


# ---------------------------------------------------------------------------
# argv shape
# ---------------------------------------------------------------------------


def test_argv_minimal(monkeypatch):
    backend = _backend(monkeypatch)
    argv = backend._argv(_inv())
    assert argv[0] == "/usr/local/bin/codex"
    assert "--quiet" in argv
    assert argv[argv.index("--approval-mode") + 1] == "full-auto"
    assert argv[argv.index("--system-prompt") + 1] == "SYS"
    assert argv[-1] == "DO IT"


def test_argv_model_from_invocation(monkeypatch):
    backend = _backend(monkeypatch)
    argv = backend._argv(_inv(model="o4-mini"))
    assert argv[argv.index("--model") + 1] == "o4-mini"


def test_argv_model_from_backend_default(monkeypatch):
    backend = _backend(monkeypatch, model="codex-mini-latest")
    argv = backend._argv(_inv())
    assert argv[argv.index("--model") + 1] == "codex-mini-latest"


def test_argv_invocation_model_overrides_backend_default(monkeypatch):
    backend = _backend(monkeypatch, model="codex-mini-latest")
    argv = backend._argv(_inv(model="o4-mini"))
    assert argv[argv.index("--model") + 1] == "o4-mini"


def test_argv_max_turns(monkeypatch):
    backend = _backend(monkeypatch)
    argv = backend._argv(_inv(max_turns=5))
    assert argv[argv.index("--max-turns") + 1] == "5"


def test_argv_no_model_when_none(monkeypatch):
    backend = _backend(monkeypatch)
    argv = backend._argv(_inv())
    assert "--model" not in argv


def test_argv_no_max_turns_when_none(monkeypatch):
    backend = _backend(monkeypatch)
    argv = backend._argv(_inv())
    assert "--max-turns" not in argv


def test_argv_custom_approval_mode(monkeypatch):
    backend = _backend(monkeypatch, approval_mode="auto-edit")
    argv = backend._argv(_inv())
    assert argv[argv.index("--approval-mode") + 1] == "auto-edit"


def test_argv_extra_args(monkeypatch):
    backend = _backend(monkeypatch, extra_args=["--no-project-doc"])
    argv = backend._argv(_inv())
    assert "--no-project-doc" in argv
    assert argv[-1] == "DO IT"  # prompt still last


def test_argv_ignored_fields_absent(monkeypatch):
    """allowed_tools, skills, mcp_config have no Codex flags — must not appear."""
    backend = _backend(monkeypatch)
    argv = backend._argv(
        _inv(allowed_tools=["WebSearch"], skills=["/some/SKILL.md"], mcp_config="/cfg.json")
    )
    assert "--allowedTools" not in argv
    assert "--tools" not in argv
    assert "--skill" not in argv
    assert "--mcp-config" not in argv


# ---------------------------------------------------------------------------
# run() — happy path
# ---------------------------------------------------------------------------


def test_run_returns_text(monkeypatch):
    backend = _backend(monkeypatch)
    monkeypatch.setattr(
        "workflow_ai.backends.codex.subprocess.run",
        lambda *a, **kw: subprocess.CompletedProcess([], 0, stdout="Hello world\n", stderr=""),
    )
    result = backend.run(_inv())
    assert result.text == "Hello world"
    assert result.structured is None
    assert result.cost_usd is None


def test_run_strips_whitespace(monkeypatch):
    backend = _backend(monkeypatch)
    monkeypatch.setattr(
        "workflow_ai.backends.codex.subprocess.run",
        lambda *a, **kw: subprocess.CompletedProcess([], 0, stdout="  answer  \n", stderr=""),
    )
    assert backend.run(_inv()).text == "answer"


# ---------------------------------------------------------------------------
# run() — error paths
# ---------------------------------------------------------------------------


def test_run_raises_on_nonzero_exit(monkeypatch):
    backend = _backend(monkeypatch)
    monkeypatch.setattr(
        "workflow_ai.backends.codex.subprocess.run",
        lambda *a, **kw: subprocess.CompletedProcess([], 1, stdout="", stderr="auth error"),
    )
    with pytest.raises(AgentOutputError, match="exited 1"):
        backend.run(_inv())


def test_run_raises_on_empty_output(monkeypatch):
    backend = _backend(monkeypatch)
    monkeypatch.setattr(
        "workflow_ai.backends.codex.subprocess.run",
        lambda *a, **kw: subprocess.CompletedProcess([], 0, stdout="   ", stderr=""),
    )
    with pytest.raises(AgentOutputError, match="no output"):
        backend.run(_inv())


def test_run_raises_on_timeout(monkeypatch):
    backend = _backend(monkeypatch)

    def _raise(*a, **kw):
        raise subprocess.TimeoutExpired(cmd="codex", timeout=600)

    monkeypatch.setattr("workflow_ai.backends.codex.subprocess.run", _raise)
    with pytest.raises(AgentOutputError, match="timed out"):
        backend.run(_inv())


def test_run_raises_when_executable_missing(monkeypatch):
    backend = CodexBackend()
    monkeypatch.setattr("workflow_ai.backends.codex.shutil.which", lambda _e: None)
    with pytest.raises(AgentOutputError, match="not found on PATH"):
        backend.run(_inv())


def test_run_raises_on_file_not_found(monkeypatch):
    backend = _backend(monkeypatch)

    def _raise(*a, **kw):
        raise FileNotFoundError

    monkeypatch.setattr("workflow_ai.backends.codex.subprocess.run", _raise)
    with pytest.raises(AgentOutputError, match="not found"):
        backend.run(_inv())
