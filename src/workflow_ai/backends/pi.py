"""Pi coding-agent headless backend.

Runs one `pi --print --mode json` subprocess per node (fresh context, no session)
and reconstructs the assistant's final text from Pi's JSON-lines event stream.
Pi has no native JSON-schema enforcement, so this backend always returns
`structured=None`; the engine extracts and validates JSON from the text for
json nodes and retries on failure.

Grounded against the Pi CLI (v0.80.2): --print, --mode json, --no-session,
--offline, --provider, --model, --system-prompt, --no-context-files, --tools /
--no-tools, --skill <path>. Point Pi at a local model by configuring
~/.pi/agent/models.json (e.g. an "ollama" provider) and passing --provider/--model.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from typing import Any

from .base import AgentInvocation, AgentOutputError, AgentResult


class PiBackend:
    def __init__(
        self,
        executable: str = "pi",
        *,
        provider: str | None = None,
        model: str | None = None,
        offline: bool = True,
        extra_args: list[str] | None = None,
        timeout: int | None = 600,
    ) -> None:
        self.executable = executable
        self.provider = provider
        self.model = model
        self.offline = offline
        self.extra_args = list(extra_args or [])
        self.timeout = timeout

    def _resolve_executable(self) -> str:
        resolved = shutil.which(self.executable)
        if resolved is None:
            raise AgentOutputError(f"Pi executable '{self.executable}' not found on PATH")
        return resolved

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
        provider = self.provider
        model = inv.model or self.model
        if provider:
            argv += ["--provider", provider]
        if model:
            argv += ["--model", model]
        # Keep a small local model focused: enable only requested tools, else none.
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
