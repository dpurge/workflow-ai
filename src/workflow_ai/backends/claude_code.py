"""Claude Code headless backend.

Builds and runs a single `claude -p ... --output-format json` subprocess per
node, with no session reuse (clean context every call). For json nodes the
node's Pydantic schema is passed via `--json-schema` so Claude enforces
structure natively; the validated payload is returned as `structured`. For text
nodes no schema is passed and only `text` is returned.

Grounded against the Claude Code headless/CLI reference:
  -p/--print, --output-format json, --json-schema, --append-system-prompt,
  --allowedTools, --mcp-config, --model, --max-turns.
"""

from __future__ import annotations

import json
import shutil
import subprocess

from .base import AgentInvocation, AgentOutputError, AgentResult


class ClaudeCodeBackend:
    def __init__(self, executable: str = "claude", timeout: int | None = 600) -> None:
        self.executable = executable
        self.timeout = timeout

    def _resolve_executable(self) -> str:
        resolved = shutil.which(self.executable)
        if resolved is None:
            raise AgentOutputError(
                f"agent executable '{self.executable}' not found on PATH"
            )
        return resolved

    def _argv(self, inv: AgentInvocation) -> list[str]:
        argv = [
            self._resolve_executable(),
            "-p",
            inv.prompt,
            "--output-format",
            "json",
            "--append-system-prompt",
            inv.system_prompt,
        ]
        if inv.output_kind == "json" and inv.schema is not None:
            argv += ["--json-schema", json.dumps(inv.schema.model_json_schema())]
        if inv.allowed_tools:
            argv += ["--allowedTools", ",".join(inv.allowed_tools)]
        if inv.mcp_config:
            argv += ["--mcp-config", inv.mcp_config]
        if inv.model:
            argv += ["--model", inv.model]
        if inv.max_turns is not None:
            argv += ["--max-turns", str(inv.max_turns)]
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
            raise AgentOutputError(f"agent executable '{self.executable}' not found") from exc
        except subprocess.TimeoutExpired as exc:
            raise AgentOutputError("agent invocation timed out") from exc

        if proc.returncode != 0:
            raise AgentOutputError(
                f"agent exited {proc.returncode}: {proc.stderr.strip() or proc.stdout.strip()}"
            )

        try:
            envelope = json.loads(proc.stdout)
        except json.JSONDecodeError as exc:
            raise AgentOutputError(f"agent stdout was not valid JSON: {exc}") from exc

        structured = envelope.get("structured_output")
        text = envelope.get("result", "")
        return AgentResult(text=text or "", structured=structured, cost_usd=envelope.get("total_cost_usd"))
