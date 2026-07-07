"""Native GitHub Copilot backend.

Talks directly to https://api.githubcopilot.com/chat/completions (OpenAI wire
format) via httpx.Client. No OpenAI SDK, no Pi/claude/codex CLI dependency.

Auth:
  - If api_key provided: used verbatim as bearer; no config read or refresh.
  - Otherwise: loads own config via copilot_auth helpers; refreshes session
    token via GET copilot_internal/v2/token when within 60s of expiry.

Security rules:
  - Never log or include token values in error messages.
  - Body snippets in errors truncated to <=300 chars.
"""

from __future__ import annotations

import json as _json
import sys
import time
import uuid
from typing import Any

import httpx

from .base import AgentBackend, AgentInvocation, AgentOutputError, AgentResult
from .tools import ToolDef, dispatch, openai_tool_specs, resolve_tools
from ._agentic import effective_max_turns
from ..copilot_auth import resolve_config_path, read_creds, write_creds, _ssl_verify


_COPILOT_API_BASE = "https://api.githubcopilot.com"
_GITHUB_TOKEN_URL = "https://api.github.com/copilot_internal/v2/token"
_TOKEN_REFRESH_BUFFER_MS = 60_000  # refresh 60s before expiry


def _non_none(**kwargs: Any) -> dict[str, Any]:
    return {k: v for k, v in kwargs.items() if v is not None}


def _body_snippet(body: str) -> str:
    """Truncate body to <=300 chars for safe error messages."""
    return body[:300]


class CopilotBackend:
    """AgentBackend implementation using the GitHub Copilot chat completions API."""

    def __init__(
        self,
        *,
        model: str | None = None,
        api_key: str | None = None,
        api_base_url: str | None = None,
        copilot_config: str | None = None,
        default_headers: dict[str, str] | None = None,
        max_tokens: int = 4096,
        timeout: int | None = 600,
    ) -> None:
        self._model = model
        self._api_base = (api_base_url or _COPILOT_API_BASE).rstrip("/")
        self._default_headers = default_headers or {}
        self._max_tokens = max_tokens
        self._http = httpx.Client(timeout=timeout, verify=_ssl_verify())

        if api_key is not None:
            # api_key override: use verbatim, disable refresh
            self._token: str = api_key
            self._refresh: str | None = None
            self._expires_ms: int = 0
            self._api_key_override = True
            self._config_path = None
        else:
            # Load creds from own config file
            self._api_key_override = False
            config_path = resolve_config_path(copilot_config)
            self._config_path = config_path
            try:
                block = read_creds(config_path)
            except AgentOutputError:
                raise

            self._token = block["access"]
            self._refresh = block["refresh"]
            self._expires_ms = int(block["expires"])

    # ------------------------------------------------------------------
    # Token management
    # ------------------------------------------------------------------

    def _ensure_token(self) -> str:
        """Return a valid bearer token, refreshing if needed."""
        if self._api_key_override:
            return self._token
        now_ms = time.time() * 1000
        if now_ms + _TOKEN_REFRESH_BUFFER_MS >= self._expires_ms:
            self._refresh_token()
        return self._token

    def _refresh_token(self) -> None:
        """Refresh the session token via copilot_internal/v2/token.

        Uses self._http (the shared httpx.Client) so the configured timeout
        applies uniformly (N2 reviewer concern — no bare httpx.get).
        """
        if not self._refresh:
            raise AgentOutputError(
                "CopilotBackend: token refresh not available (no refresh token)"
            )
        try:
            resp = self._http.get(
                _GITHUB_TOKEN_URL,
                headers={
                    "Authorization": f"token {self._refresh}",
                    "user-agent": "GithubCopilot/1.155.0",
                },
            )
        except httpx.HTTPError as exc:
            raise AgentOutputError(
                f"CopilotBackend: token refresh failed: {exc}"
            ) from exc

        if resp.status_code != 200:
            raise AgentOutputError(
                f"CopilotBackend: token refresh failed: HTTP {resp.status_code}"
            )

        data = resp.json()
        if "token" not in data:
            raise AgentOutputError(
                "CopilotBackend: token refresh failed: 'token' not in response"
            )

        # expires_at is Unix seconds; normalize to ms
        self._token = data["token"]
        self._expires_ms = int(data["expires_at"]) * 1000

        # Optional refresh writeback (own file — safe, failures non-fatal)
        if self._config_path is not None and self._refresh is not None:
            try:
                write_creds(
                    self._config_path,
                    refresh=self._refresh,
                    access=self._token,
                    expires_ms=self._expires_ms,
                )
            except OSError as exc:
                print(
                    f"CopilotBackend: warning: could not persist refreshed token: {exc}",
                    file=sys.stderr,
                )

    # ------------------------------------------------------------------
    # Headers
    # ------------------------------------------------------------------

    def _headers(self, bearer: str, messages: list[Any]) -> dict[str, str]:
        """Build required Copilot headers for a request.

        x-initiator is 'agent' if any assistant/tool role present, else 'user'.
        x-request-id is a fresh UUID4 per request.
        default_headers merge on top, allowing override of any value.
        """
        roles = {m.get("role") for m in messages if isinstance(m, dict)}
        initiator = "agent" if (roles & {"assistant", "tool"}) else "user"

        headers = {
            "authorization": f"Bearer {bearer}",
            "content-type": "application/json",
            "copilot-integration-id": "vscode-chat",
            "editor-version": "vscode/1.104.1",
            "editor-plugin-version": "copilot-chat/0.26.7",
            "user-agent": "GitHubCopilotChat/0.26.7",
            "openai-intent": "conversation-panel",
            "x-github-api-version": "2025-04-01",
            "x-request-id": str(uuid.uuid4()),
            "x-vscode-user-agent-library-version": "electron-fetch",
            "x-initiator": initiator,
        }
        # Merge default_headers last (allows override)
        headers.update(self._default_headers)
        return headers

    # ------------------------------------------------------------------
    # Chat completions
    # ------------------------------------------------------------------

    def _post_chat(self, payload: dict[str, Any], messages: list[Any]) -> dict[str, Any]:
        """POST to chat/completions, returning parsed response dict.

        Raises AgentOutputError on non-2xx (429 special-cased).
        """
        bearer = self._ensure_token()
        headers = self._headers(bearer, messages)
        url = f"{self._api_base}/chat/completions"

        try:
            resp = self._http.post(url, json=payload, headers=headers)
        except httpx.HTTPError as exc:
            raise AgentOutputError(
                f"CopilotBackend: HTTP error posting to {url}: {exc}"
            ) from exc

        if resp.status_code == 429:
            snippet = _body_snippet(resp.text)
            raise AgentOutputError(
                f"CopilotBackend: rate limited (HTTP 429): {snippet}"
            )
        if resp.status_code < 200 or resp.status_code >= 300:
            snippet = _body_snippet(resp.text)
            raise AgentOutputError(
                f"CopilotBackend: API error HTTP {resp.status_code}: {snippet}"
            )

        try:
            return resp.json()
        except Exception as exc:
            raise AgentOutputError(
                f"CopilotBackend: could not parse API response as JSON: {exc}"
            ) from exc

    # ------------------------------------------------------------------
    # Agentic run loop
    # ------------------------------------------------------------------

    def run(self, inv: AgentInvocation) -> AgentResult:
        model = inv.model or self._model
        if not model:
            raise AgentOutputError(
                "CopilotBackend: no model specified; set model in __init__ or AgentInvocation.model"
            )

        if inv.mcp_config:
            print(
                "CopilotBackend: mcp_config is not supported and will be ignored",
                file=sys.stderr,
            )

        user_tools: list[ToolDef] = resolve_tools(inv.allowed_tools)
        tool_specs = openai_tool_specs(user_tools)

        messages: list[Any] = [
            {"role": "system", "content": inv.system_prompt},
            {"role": "user", "content": inv.prompt},
        ]
        max_turns = effective_max_turns(inv.max_turns)

        for _turn in range(max_turns):
            payload: dict[str, Any] = {
                "model": model,
                "messages": messages,
                "max_completion_tokens": self._max_tokens,
                "temperature": 0,
                "stream": False,
            }
            if tool_specs:
                payload["tools"] = tool_specs

            # JSON node: add response_format unless we need to try fallback
            is_json_node = inv.output_kind == "json"
            if is_json_node:
                payload["response_format"] = {"type": "json_object"}

            # --- P6: Claude-via-gateway fallback ---
            # Track whether we already tried without response_format this turn
            _tried_fallback = False

            while True:
                try:
                    data = self._post_chat(payload, messages)
                    break
                except AgentOutputError as exc:
                    msg = str(exc)
                    # Check if this is a 400/422 response_format unsupported error
                    if (
                        is_json_node
                        and not _tried_fallback
                        and _is_response_format_unsupported(msg)
                    ):
                        # Remove response_format and add strengthening instruction
                        payload = {k: v for k, v in payload.items() if k != "response_format"}
                        # Append a user message strengthening the JSON instruction
                        messages = messages + [
                            {
                                "role": "user",
                                "content": (
                                    "return ONLY a JSON object matching the required schema; "
                                    "no markdown"
                                ),
                            }
                        ]
                        _tried_fallback = True
                        continue
                    raise

            msg_obj = data["choices"][0]["message"]
            content = msg_obj.get("content") or ""
            tool_calls = msg_obj.get("tool_calls") or []

            # Tool calls branch
            if tool_calls:
                tool_results: list[dict[str, Any]] = []
                for tc in tool_calls:
                    tc_id = tc["id"]
                    fn = tc["function"]
                    fn_name = fn["name"]
                    args = fn.get("arguments", "{}")
                    if isinstance(args, str):
                        try:
                            args = _json.loads(args)
                        except Exception:
                            args = {}
                    result = dispatch(user_tools, fn_name, args)
                    tool_results.append(
                        {
                            "role": "tool",
                            "tool_call_id": tc_id,
                            "content": result,
                        }
                    )

                assistant_dict: dict[str, Any] = {
                    "role": "assistant",
                    "content": msg_obj.get("content"),
                    "tool_calls": [
                        {
                            "id": tc["id"],
                            "type": "function",
                            "function": {
                                "name": tc["function"]["name"],
                                "arguments": tc["function"].get("arguments", "{}"),
                            },
                        }
                        for tc in tool_calls
                    ],
                }
                messages = messages + [assistant_dict] + tool_results
                continue

            # JSON node: handle empty content after tool calls (nudge)
            if is_json_node:
                if not content.strip():
                    messages = messages + [
                        {"role": "assistant", "content": ""},
                        {
                            "role": "user",
                            "content": (
                                "You have completed your tool calls. "
                                "Now return ONLY a JSON object matching the required schema. "
                                "No explanation, no markdown."
                            ),
                        },
                    ]
                    continue
                return AgentResult(text=content, structured=None)

            # Text node
            if not content.strip():
                raise AgentOutputError("CopilotBackend: empty text output")
            return AgentResult(text=content, structured=None)

        raise AgentOutputError(
            f"CopilotBackend: agentic loop exceeded {max_turns} turns without a final answer"
        )


def _is_response_format_unsupported(error_msg: str) -> bool:
    """Heuristic: check if an error message indicates response_format is unsupported."""
    lower = error_msg.lower()
    # Look for HTTP 400/422 + keywords suggesting response_format rejection
    if "http 400" not in lower and "http 422" not in lower:
        return False
    # Require at least one schema-specific keyword to avoid false positives on unrelated 400s
    schema_keywords = ["response_format", "json_object"]
    return any(kw in lower for kw in schema_keywords)
