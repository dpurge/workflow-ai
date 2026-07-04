"""OpenAI Codex CLI headless backend.

Builds and runs a single `codex --quiet --approval-mode full-auto` subprocess
per node (clean context every call). Codex has no native JSON-schema enforcement
and no structured-output flag, so this backend always returns `structured=None`;
the engine extracts and validates JSON from `text` for json nodes and retries on
failure (same path as PiBackend).

Grounded against openai/codex codex-cli source (index.tsx, approvals.ts):
  -q/--quiet, --approval-mode, --system-prompt, --model, --max-turns.

Unsupported AgentInvocation fields (no Codex CLI equivalent):
  allowed_tools, skills, mcp_config — silently ignored.
"""

from __future__ import annotations

import os
import shutil
import subprocess

from .base import AgentInvocation, AgentOutputError, AgentResult


class CodexBackend:
    def __init__(
        self,
        executable: str = "codex",
        *,
        approval_mode: str = "full-auto",
        model: str | None = None,
        api_base_url: str | None = None,
        api_key: str | None = None,
        extra_args: list[str] | None = None,
        timeout: int | None = 600,
    ) -> None:
        self.executable = executable
        self.approval_mode = approval_mode
        self.model = model
        self.api_base_url = api_base_url
        self.api_key = api_key
        self.extra_args = list(extra_args or [])
        self.timeout = timeout

    def _resolve_executable(self) -> str:
        resolved = shutil.which(self.executable)
        if resolved is None:
            raise AgentOutputError(
                f"Codex executable '{self.executable}' not found on PATH"
            )
        return resolved

    def _env(self) -> dict[str, str]:
        env = os.environ.copy()
        if self.api_base_url:
            env["OPENAI_BASE_URL"] = self.api_base_url
        if self.api_key:
            env["OPENAI_API_KEY"] = self.api_key
        return env

    def _argv(self, inv: AgentInvocation) -> list[str]:
        argv = [
            self._resolve_executable(),
            "--quiet",
            "--approval-mode",
            self.approval_mode,
            "--system-prompt",
            inv.system_prompt,
        ]
        model = inv.model or self.model
        if model:
            argv += ["--model", model]
        if inv.max_turns is not None:
            argv += ["--max-turns", str(inv.max_turns)]
        argv += self.extra_args
        argv.append(inv.prompt)
        return argv

    def run(self, inv: AgentInvocation) -> AgentResult:
        try:
            proc = subprocess.run(
                self._argv(inv),
                env=self._env(),
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=self.timeout,
            )
        except FileNotFoundError as exc:
            raise AgentOutputError(
                f"Codex executable '{self.executable}' not found"
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise AgentOutputError("Codex invocation timed out") from exc

        if proc.returncode != 0:
            raise AgentOutputError(
                f"Codex exited {proc.returncode}: "
                f"{proc.stderr.strip() or proc.stdout.strip()}"
            )

        text = proc.stdout.strip()
        if not text:
            raise AgentOutputError("Codex produced no output")
        return AgentResult(text=text, structured=None, cost_usd=None)
