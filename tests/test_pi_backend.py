"""Pi backend: reconstruct assistant text from the --mode json event stream."""

from __future__ import annotations

import json

from workflow_ai.backends.base import AgentInvocation
from workflow_ai.backends.pi import PiBackend, parse_pi_stream


def _stream(*events: dict) -> str:
    return "\n".join(json.dumps(e) for e in events) + "\n"


def test_parse_prefers_text_deltas():
    stream = _stream(
        {"type": "session", "version": 3, "id": "x"},
        {"type": "agent_start"},
        {"type": "message_update", "assistantMessageEvent": {"type": "text_delta", "delta": "Hello "}},
        {"type": "message_update", "assistantMessageEvent": {"type": "text_delta", "delta": "world"}},
        {"type": "message_end", "message": {"role": "assistant", "content": "Hello world"}},
        {"type": "agent_end", "messages": []},
    )
    assert parse_pi_stream(stream) == "Hello world"


def test_parse_falls_back_to_message_end_block_content():
    stream = _stream(
        {"type": "message_end", "message": {"role": "assistant",
            "content": [{"type": "text", "text": '{"language":"deu"}'}]}},
    )
    assert parse_pi_stream(stream) == '{"language":"deu"}'


def test_parse_ignores_non_json_and_non_assistant_lines():
    stream = "not json\n" + _stream(
        {"type": "message_end", "message": {"role": "user", "content": "ignored"}},
        {"type": "message_update", "assistantMessageEvent": {"type": "text_delta", "delta": "kept"}},
    )
    assert parse_pi_stream(stream) == "kept"


def test_argv_shape_no_tools_and_skill_paths(monkeypatch):
    backend = PiBackend(provider="ollama", model="gemma2:9b")
    monkeypatch.setattr("workflow_ai.backends.pi.shutil.which", lambda _e: "/opt/homebrew/bin/pi")
    inv = AgentInvocation(
        system_prompt="SYS",
        prompt="DO IT",
        output_kind="text",
        skills=["/abs/phraseforge-lang-deu/SKILL.md"],
    )
    argv = backend._argv(inv)
    assert argv[0] == "/opt/homebrew/bin/pi"
    assert "--mode" in argv and argv[argv.index("--mode") + 1] == "json"
    assert "--no-session" in argv and "--no-context-files" in argv
    assert argv[argv.index("--provider") + 1] == "ollama"
    assert argv[argv.index("--model") + 1] == "gemma2:9b"
    assert "--no-tools" in argv  # empty allowed_tools -> keep the small model focused
    assert argv[argv.index("--skill") + 1] == "/abs/phraseforge-lang-deu/SKILL.md"
    assert argv[-1] == "DO IT"  # prompt is the trailing positional
