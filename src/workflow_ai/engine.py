"""The workflow executor.

Drives the DAG with a worklist of (node_id, context) pairs. A node is either a
model node (runs the agent) or an action node (runs registered Python). Output
is JSON (validated against a schema) or raw text. Edges are resolved by, in
order: a declared `router` (code), a single static successor, or a model-chosen
`next_state`/`next_states`. Failures retry up to `retries` (default 3).
"""

from __future__ import annotations

import copy
import json
import re
from collections import deque
from pathlib import Path
from typing import Any, Sequence

from pydantic import BaseModel, ValidationError

from . import registry
from .backends.base import AgentBackend, AgentInvocation, AgentOutputError
from .graph import NodeSpec, WorkflowGraph
from .models import BranchResult, NodeRun, RunResult, WorkflowContext


class WorkflowError(RuntimeError):
    """Raised when a node exhausts retries or returns an invalid transition."""


class Engine:
    def __init__(self, backend: AgentBackend | None = None, *, default_retries: int = 3) -> None:
        self.backend = backend
        self.default_retries = default_retries

    def run(
        self,
        graph: WorkflowGraph,
        initial_prompt: str,
        *,
        initial_data: dict[str, Any] | None = None,
        retries_override: int | None = None,
        model_override: str | None = None,
        on_event: Any = None,
    ) -> RunResult:
        result = RunResult(workflow=graph.name)
        root = WorkflowContext(initial_prompt=initial_prompt, data=dict(initial_data or {}))
        worklist: deque[tuple[str, WorkflowContext]] = deque([(graph.start, root)])

        def emit(kind: str, node_id: str, data: dict | None = None) -> None:
            if on_event:
                on_event(kind, node_id, data or {})

        while worklist:
            node_id, context = worklist.popleft()
            node = graph.node(node_id)
            emit("enter", node_id)

            if node.terminal:
                emit("terminal", node_id, {"context_data": dict(context.data)})
                result.branches.append(BranchResult(terminal_node=node_id, context=context))
                continue

            output, run = self._execute_node(node, context, retries_override, model_override, emit, graph.yaml_path)
            context.record(run)
            emit("output", node_id, {"raw_output": run.raw_output})
            context = self._apply_update(node, output, context)
            emit("context", node_id, {"context_data": dict(context.data)})

            successors = self._resolve_transition(node, output, context)
            invalid = [s for s in successors if s not in node.successors]
            if invalid:
                raise WorkflowError(
                    f"node '{node.id}' routed to invalid state(s) {invalid}; "
                    f"allowed: {node.successors}"
                )
            emit("transition", node_id, {"successors": successors})
            for succ in successors:
                worklist.append((succ, copy.deepcopy(context)))

        return result

    # --- node execution ----------------------------------------------------

    def _execute_node(
        self,
        node: NodeSpec,
        context: WorkflowContext,
        retries_override: int | None,
        model_override: str | None,
        emit: Any = None,
        yaml_path: Path | None = None,
    ) -> tuple[Any, NodeRun]:
        verify_fn = registry.get_verifier(node.verifier)
        retries = retries_override if retries_override is not None else node.retries
        _emit = emit or (lambda *a, **kw: None)

        last_error = "unknown"
        for attempt in range(1, retries + 1):
            _emit("attempt", node.id, {"attempt": attempt, "retries": retries})
            try:
                output = self._produce_output(node, context, model_override, last_error, attempt, yaml_path)
            except AgentOutputError as exc:
                last_error = str(exc)
                _emit("retry", node.id, {"attempt": attempt, "error": last_error})
                continue

            verify = verify_fn(output, context)
            if not verify.ok:
                last_error = "; ".join(verify.errors) or "verification failed"
                _emit("retry", node.id, {"attempt": attempt, "error": last_error})
                continue

            run = NodeRun(
                node_id=node.id,
                attempts=attempt,
                raw_output=_as_raw(output, node.produces),
                verify=verify,
                cost_usd=None,
            )
            return output, run

        raise WorkflowError(f"node '{node.id}' failed after {retries} attempts: {last_error}")

    def _produce_output(
        self,
        node: NodeSpec,
        context: WorkflowContext,
        model_override: str | None,
        last_error: str,
        attempt: int,
        yaml_path: Path | None = None,
    ) -> Any:
        if node.kind == "action":
            payload = registry.get_action(node.action)(context)
            if node.output_kind == "json" and node.schema_name:
                return self._validate_json(node, payload)
            return payload

        # model node
        if self.backend is None:
            raise AgentOutputError("no backend configured for a model node")

        system_prompt = _build_system_prompt(node)
        prompt = _build_user_prompt(node, context)
        if attempt > 1:
            prompt += (
                f"\n\nYour previous attempt was rejected: {last_error}\n"
                "Correct it and respond again."
            )

        schema = registry.get_schema(node.schema_name) if node.output_kind == "json" else None
        skills = _resolve_skill_paths(node.skills, context, yaml_path)
        if skills:
            system_prompt = _inject_skills(system_prompt, skills)
        inv = AgentInvocation(
            system_prompt=system_prompt,
            prompt=prompt,
            output_kind=node.output_kind,
            schema=schema,
            allowed_tools=node.allowed_tools,
            skills=skills,
            mcp_config=node.mcp_config,
            model=model_override or node.model,
            max_turns=node.max_turns,
        )
        result = self.backend.run(inv)

        if node.output_kind == "text":
            if not result.text.strip():
                raise AgentOutputError("empty text output")
            return result.text.strip()

        payload = result.structured if result.structured is not None else _extract_json(result.text)
        return self._validate_json(node, payload)

    def _validate_json(self, node: NodeSpec, payload: Any) -> BaseModel:
        schema = registry.get_schema(node.schema_name)
        try:
            return schema.model_validate(payload)
        except ValidationError as exc:
            raise AgentOutputError(f"output failed schema validation: {exc}") from exc

    # --- context update + transitions --------------------------------------

    def _apply_update(self, node: NodeSpec, output: Any, context: WorkflowContext) -> WorkflowContext:
        updater = registry.get_updater(node.updater)
        if updater is not None:
            return updater(output, context)
        return registry.default_store(output, context, node.produces)

    def _resolve_transition(
        self, node: NodeSpec, output: Any, context: WorkflowContext
    ) -> list[str]:
        if node.router:
            states = registry.get_router(node.router)(output, context) or []
            return [str(s) for s in states]
        if len(node.successors) == 1 and not node.fan_out:
            return [node.successors[0]]
        return self._model_chosen(node, output)

    @staticmethod
    def _model_chosen(node: NodeSpec, output: Any) -> list[str]:
        data = output.model_dump() if isinstance(output, BaseModel) else (output or {})
        if not isinstance(data, dict):
            raise WorkflowError(f"node '{node.id}' cannot determine next state from output")
        if node.fan_out:
            states = data.get("next_states")
            if not isinstance(states, list) or not states:
                raise WorkflowError(
                    f"fan-out node '{node.id}' must return non-empty 'next_states' list"
                )
            return [str(s) for s in states]
        state = data.get("next_state")
        if not state:
            raise WorkflowError(f"node '{node.id}' must return 'next_state'")
        return [str(state)]


def _as_raw(output: Any, produces: str | None) -> dict[str, Any]:
    if isinstance(output, BaseModel):
        return output.model_dump()
    if isinstance(output, dict):
        return output
    return {produces or "value": output}


def _build_system_prompt(node: NodeSpec) -> str:
    parts = [node.role.strip()]
    model_chosen = not node.router and (node.fan_out or len(node.successors) > 1)
    if model_chosen:
        transitions = "\n".join(f"  - {name}: {desc}" for name, desc in node.next.items())
        field = "next_states (a JSON array)" if node.fan_out else "next_state (a single string)"
        parts.append(
            "You are one node in a strict workflow graph with no prior history. "
            "After doing this node's work, choose where the workflow goes next from "
            f"EXACTLY these options:\n{transitions}\n"
            f"Return your choice in the `{field}` field. Pick only from the listed options."
        )
    if node.output_kind == "json":
        parts.append("Respond with JSON only, matching the required schema.")
    return "\n\n".join(p for p in parts if p)


def _build_user_prompt(node: NodeSpec, context: WorkflowContext) -> str:
    template = node.prompt or "Initial request:\n{initial_prompt}"
    fields: dict[str, Any] = {"initial_prompt": context.initial_prompt}
    if node.reads:
        # Inject only the requested context slice (keeps prompts small).
        slice_ = {k: context.data.get(k) for k in node.reads}
        fields["data"] = json.dumps(slice_, ensure_ascii=False, indent=2)
        for k in node.reads:
            fields[k] = context.data.get(k, "")
    else:
        fields["data"] = json.dumps(context.data, ensure_ascii=False, indent=2)
    try:
        return template.format(**fields)
    except KeyError as exc:
        raise WorkflowError(f"node '{node.id}' prompt references unknown field {exc}")


def _extract_json(text: str) -> Any:
    """Extract the first JSON value (object or array) from possibly-fenced text."""

    stripped = text.strip()
    fence = re.search(r"```(?:json)?\s*(.+?)```", stripped, re.DOTALL)
    if fence:
        stripped = fence.group(1).strip()
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        pass
    start = min((i for i in (stripped.find("{"), stripped.find("[")) if i != -1), default=-1)
    if start == -1:
        raise AgentOutputError("no JSON value found in agent output")
    decoder = json.JSONDecoder()
    try:
        value, _ = decoder.raw_decode(stripped[start:])
        return value
    except json.JSONDecodeError as exc:
        raise AgentOutputError(f"could not parse JSON from agent output: {exc}") from exc


def _resolve_skill_paths(
    refs: Sequence[str],
    context: Any,
    yaml_path: Path | None,
) -> list[Path]:
    """Resolve skill references to absolute Paths.

    '@name' references go through the registry resolver.
    './relative' paths are resolved against the workflow YAML's directory.
    Absolute paths are used as-is.
    """
    yaml_dir = yaml_path.parent if yaml_path else None
    resolved: list[Path] = []
    for ref in refs:
        path_str = registry.resolve_skill(ref, context)
        p = Path(path_str)
        if not p.is_absolute():
            if yaml_dir is None:
                raise WorkflowError(
                    f"cannot resolve relative skill '{path_str}': workflow yaml_path is unknown"
                )
            p = (yaml_dir / p).resolve()
        resolved.append(p)
    return resolved


def _inject_skills(system_prompt: str, skill_paths: list[Path]) -> str:
    """Read skill files and append their content to the system prompt.

    Each skill block is preceded by a preamble that tells the model the
    absolute directory the skill file lives in, so any relative paths
    referenced inside the skill (scripts, templates, data files) can be
    constructed correctly by the model.
    """
    parts = [system_prompt]
    for p in skill_paths:
        if not p.exists():
            raise WorkflowError(f"skill file not found: {p}")
        content = p.read_text(encoding="utf-8").strip()
        skill_dir = p.parent.resolve()
        parts.append(
            f"---\n"
            f"# Skill: {p.name}\n"
            f"# Skill directory: {skill_dir}\n\n"
            f"{content}"
        )
    return "\n\n".join(parts)


def dump_run(result: RunResult, out_dir: str | Path) -> Path:
    """Persist the run result + per-branch context to a directory."""

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "result.json").write_text(result.model_dump_json(indent=2), encoding="utf-8")
    for i, branch in enumerate(result.branches):
        (out / f"branch-{i}-{branch.terminal_node}.json").write_text(
            branch.context.model_dump_json(indent=2), encoding="utf-8"
        )
    return out
