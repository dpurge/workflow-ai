"""Pi coding-agent headless backend.

Runs one `pi --print --mode json` subprocess per node (fresh context, no session)
and reconstructs the assistant's final text from Pi's JSON-lines event stream.
Pi has no native JSON-schema enforcement, so this backend always returns
`structured=None`; the engine extracts and validates JSON from the text for
json nodes and retries on failure.

API endpoint rewiring is done via subprocess environment variables:
  OPENAI_BASE_URL   — redirect to OpenRouter, Ollama (v1 endpoint), Azure, etc.
  OPENAI_API_KEY    — API key for the target endpoint.
Pi reads these standard OpenAI-SDK env vars for its OpenAI-compatible provider.
For Ollama: OPENAI_BASE_URL=http://host:11434/v1, OPENAI_API_KEY=ollama.
For OpenRouter: OPENAI_BASE_URL=https://openrouter.ai/api/v1, OPENAI_API_KEY=sk-or-...

Grounded against the Pi CLI (v0.80.2): --print, --mode json, --no-session,
--offline, --no-context-files, --model, --api-key, --system-prompt,
--no-tools / --tools, --skill <path>.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from typing import Any

from .base import AgentInvocation, AgentOutputError, AgentResult


class PiBackend:
    def __init__(
        self,
        executable: str = "pi",
        *,
        model: str | None = None,
        api_base_url: str | None = None,
        api_key: str | None = None,
        offline: bool = True,
        extra_args: list[str] | None = None,
        timeout: int | None = 600,
    ) -> None:
        self.executable = executable
        self.model = model
        self.api_base_url = api_base_url
        self.api_key = api_key
        self.offline = offline
        self.extra_args = list(extra_args or [])
        self.timeout = timeout

    def _resolve_executable(self) -> str:
        resolved = shutil.which(self.executable)
        if resolved is None:
            raise AgentOutputError(f"Pi executable '{self.executable}' not found on PATH")
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
            "--print",
            "--mode",
            "json",
            "--no-session",
            "--no-context-files",
            "--system-prompt",
            inv.system_prompt,
        ]
        if self.offline:
            argv.append("--offline")
        model = inv.model or self.model
        if model:
            argv += ["--model", model]
        if inv.allowed_tools:
            argv += ["--tools", ",".join(inv.allowed_tools)]
        else:
            argv.append("--no-tools")
        for skill in inv.skills:
            argv += ["--skill", skill]
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
            raise AgentOutputError(f"Pi executable '{self.executable}' not found") from exc
        except subprocess.TimeoutExpired as exc:
            raise AgentOutputError("Pi invocation timed out") from exc

        if proc.returncode != 0:
            raise AgentOutputError(
                f"Pi exited {proc.returncode}: {proc.stderr.strip() or proc.stdout.strip()}"
            )

        text = parse_pi_stream(proc.stdout)
        if not text.strip():
            raise AgentOutputError("Pi produced no assistant text")
        return AgentResult(text=text, structured=None)


def parse_pi_stream(stdout: str) -> str:
    """Reconstruct assistant text from Pi's `--mode json` JSON-lines stream.

    Prefers streamed `text_delta` deltas; falls back to the text of the final
    assistant message (`message_end` / `agent_end`).
    """

    deltas: list[str] = []
    fallback = ""
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        etype = event.get("type")
        if etype == "message_update":
            ame = event.get("assistantMessageEvent") or {}
            if ame.get("type") == "text_delta" and isinstance(ame.get("delta"), str):
                deltas.append(ame["delta"])
        elif etype == "message_end":
            txt = _message_text(event.get("message"))
            if txt:
                fallback = txt
        elif etype == "agent_end":
            for msg in event.get("messages") or []:
                txt = _message_text(msg)
                if txt:
                    fallback = txt
    return "".join(deltas) if deltas else fallback


def _message_text(message: Any) -> str:
    """Extract text from a Pi message whose content may be a string or blocks."""

    if not isinstance(message, dict):
        return ""
    if message.get("role") not in (None, "assistant"):
        return ""
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [
            block.get("text", "")
            for block in content
            if isinstance(block, dict) and block.get("type") in ("text", None)
        ]
        return "".join(parts)
    return ""
