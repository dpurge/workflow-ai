from __future__ import annotations

import sys
from typing import Any

from workflow_ai.backends._agentic import effective_max_turns
from workflow_ai.backends.base import AgentInvocation, AgentOutputError, AgentResult
from workflow_ai.backends.tools import anthropic_tool_specs, dispatch, resolve_tools


class AnthropicBackend:
    def __init__(
        self,
        *,
        model: str | None = None,
        api_key: str | None = None,
        api_base_url: str | None = None,
        default_headers: dict[str, str] | None = None,
        max_tokens: int = 4096,
        timeout: int | None = 600,
    ) -> None:
        try:
            import anthropic
        except ImportError:
            raise AgentOutputError(
                "anthropic package not installed; run: pip install 'workflow-ai[anthropic]'"
            )

        client_kwargs: dict[str, Any] = {
            k: v
            for k, v in {
                "api_key": api_key,
                "base_url": api_base_url,
                "default_headers": default_headers,
                "timeout": timeout,
            }.items()
            if v is not None
        }
        self._client = anthropic.Anthropic(**client_kwargs)
        self._model = model
        self._max_tokens = max_tokens

    def run(self, inv: AgentInvocation) -> AgentResult:
        model = inv.model or self._model
        if not model:
            raise AgentOutputError(
                "AnthropicBackend requires a model — set via --model or config"
            )

        user_tools = resolve_tools(inv.allowed_tools)
        tool_specs = anthropic_tool_specs(user_tools)

        emit_schema = None
        if inv.output_kind == "json" and inv.schema is not None:
            emit_schema = inv.schema.model_json_schema()
            tool_specs = tool_specs + [
                {
                    "name": "emit_result",
                    "description": "Return the final structured answer.",
                    "input_schema": emit_schema,
                }
            ]

        messages: list[dict[str, Any]] = [{"role": "user", "content": inv.prompt}]
        max_turns = effective_max_turns(inv.max_turns)
        accumulated_text: list[str] = []

        if inv.mcp_config:
            print(
                "AnthropicBackend: mcp_config is not supported and will be ignored",
                file=sys.stderr,
            )

        for _turn in range(max_turns):
            kwargs: dict[str, Any] = {
                "model": model,
                "max_tokens": self._max_tokens,
                "system": inv.system_prompt,
                "messages": messages,
            }
            if tool_specs:
                kwargs["tools"] = tool_specs
                if inv.output_kind == "json" and not user_tools:
                    kwargs["tool_choice"] = {"type": "tool", "name": "emit_result"}
                else:
                    kwargs["tool_choice"] = {"type": "auto"}

            try:
                response = self._client.messages.create(**kwargs)
            except Exception as exc:
                raise AgentOutputError(f"Anthropic API error: {exc}") from exc

            text_parts: list[str] = []
            tool_use_blocks: list[Any] = []
            for block in response.content:
                if block.type == "text":
                    text_parts.append(block.text)
                elif block.type == "tool_use":
                    tool_use_blocks.append(block)

            turn_text = "".join(text_parts)
            if turn_text:
                accumulated_text.append(turn_text)

            for block in tool_use_blocks:
                if block.name == "emit_result":
                    try:
                        validated = inv.schema.model_validate(block.input)
                        return AgentResult(
                            text=turn_text or " ".join(accumulated_text),
                            structured=validated.model_dump(),
                        )
                    except Exception as exc:
                        messages = messages + [
                            {"role": "assistant", "content": response.content},
                            {
                                "role": "user",
                                "content": [
                                    {
                                        "type": "tool_result",
                                        "tool_use_id": block.id,
                                        "content": (
                                            f"Validation error: {exc}. Fix the output and call emit_result again."
                                        ),
                                    }
                                ],
                            },
                        ]
                        break
            else:
                if not tool_use_blocks:
                    final_text = " ".join(accumulated_text).strip()
                    if inv.output_kind == "text":
                        if not final_text:
                            raise AgentOutputError("AnthropicBackend: empty text output")
                        return AgentResult(text=final_text, structured=None)
                    else:
                        return AgentResult(text=final_text, structured=None)

                tool_results: list[dict[str, Any]] = []
                for block in tool_use_blocks:
                    result = dispatch(user_tools, block.name, block.input)
                    tool_results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": result,
                        }
                    )

                messages = messages + [
                    {"role": "assistant", "content": response.content},
                    {"role": "user", "content": tool_results},
                ]

        raise AgentOutputError(
            f"AnthropicBackend: agentic loop exceeded {max_turns} turns without a final answer"
        )
