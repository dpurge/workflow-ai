from __future__ import annotations

import json as _json
import sys
from typing import Any

from .base import AgentBackend, AgentInvocation, AgentOutputError, AgentResult
from .tools import ToolDef, dispatch, openai_tool_specs, resolve_tools
from ._agentic import MAX_TURNS_CAP, effective_max_turns


def _non_none(**kwargs: Any) -> dict[str, Any]:
    return {k: v for k, v in kwargs.items() if v is not None}


class OpenAIBackend:
    def __init__(
        self,
        *,
        model: str | None = None,
        api_key: str | None = None,
        api_base_url: str | None = None,
        default_headers: dict[str, str] | None = None,
        azure_endpoint: str | None = None,
        api_version: str | None = None,
        max_tokens: int = 4096,
        timeout: int | None = 600,
    ) -> None:
        try:
            import openai
        except ImportError:
            raise AgentOutputError(
                "openai package not installed; run: pip install 'workflow-ai[openai]'"
            )

        if azure_endpoint is not None:
            self._client = openai.AzureOpenAI(
                azure_endpoint=azure_endpoint,
                api_version=api_version,
                **_non_none(api_key=api_key, default_headers=default_headers, timeout=timeout),
            )
        else:
            self._client = openai.OpenAI(
                **_non_none(
                    api_key=api_key,
                    base_url=api_base_url,
                    default_headers=default_headers,
                    timeout=timeout,
                )
            )

        self._model = model
        self._max_tokens = max_tokens

    def run(self, inv: AgentInvocation) -> AgentResult:
        model = inv.model or self._model
        if not model:
            raise AgentOutputError(
                "OpenAIBackend: no model specified; set model in __init__ or AgentInvocation.model"
            )

        if inv.mcp_config:
            print(
                "OpenAIBackend: mcp_config is not supported by the SDK backend and will be ignored",
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
            common: dict[str, Any] = {
                "model": model,
                "max_tokens": self._max_tokens,
                "messages": messages,
            }
            if tool_specs:
                common["tools"] = tool_specs

            try:
                if inv.output_kind == "json" and inv.schema is not None and not tool_specs:
                    # parse() enforces strict tools; only use it when no tools are active
                    response = self._client.chat.completions.parse(
                        **common, response_format=inv.schema
                    )
                elif inv.output_kind == "json":
                    # json_object mode: valid JSON guaranteed, field names from prompt template
                    response = self._client.chat.completions.create(
                        **common, response_format={"type": "json_object"}
                    )
                else:
                    response = self._client.chat.completions.create(**common)
            except AgentOutputError:
                raise
            except Exception as exc:
                raise AgentOutputError(f"OpenAI API error: {exc}") from exc

            msg = response.choices[0].message

            if msg.tool_calls:
                tool_results: list[dict[str, Any]] = []
                for tc in msg.tool_calls:
                    args = tc.function.arguments
                    if isinstance(args, str):
                        try:
                            args = _json.loads(args)
                        except Exception:
                            args = {}
                    result = dispatch(user_tools, tc.function.name, args)
                    tool_results.append(
                        {
                            "role": "tool",
                            "tool_call_id": tc.id,
                            "content": result,
                        }
                    )
                assistant_dict: dict[str, Any] = {
                    "role": "assistant",
                    "content": msg.content,
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.function.name,
                                "arguments": tc.function.arguments,
                            },
                        }
                        for tc in msg.tool_calls
                    ],
                }
                messages = messages + [assistant_dict] + tool_results
                continue

            if inv.output_kind == "json" and inv.schema is not None:
                parsed = getattr(msg, "parsed", None)
                if parsed is not None:
                    structured = (
                        parsed.model_dump()
                        if hasattr(parsed, "model_dump")
                        else dict(parsed)
                    )
                    return AgentResult(
                        text=msg.content or "",
                        structured=structured,
                    )
                content = msg.content or ""
                if not content.strip():
                    # Model finished tool calls but returned no content.
                    # Nudge it in-turn rather than discarding the tool results.
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

            text = msg.content or ""
            if not text.strip():
                raise AgentOutputError("OpenAIBackend: empty text output")
            return AgentResult(text=text, structured=None)

        raise AgentOutputError(
            f"OpenAIBackend: agentic loop exceeded {max_turns} turns without a final answer"
        )
