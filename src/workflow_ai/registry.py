"""Name-based registries linking YAML graph nodes to Python logic.

YAML declares the graph topology and references logic by name; this module
holds the actual code:
  - schema:   a Pydantic model class describing a node's JSON output contract
  - verifier: semantic validation beyond the schema -> VerifyResult
  - updater:  fold output into the WorkflowContext
  - action:   pure-Python node body (no agent call) -> dict output
  - router:   compute the next state(s) from output+context (code-driven edges)
  - skill resolver: expand dynamic "@name" skill references at runtime

Model nodes choose their successor(s) by returning `next_state`/`next_states`
unless the node declares a `router`, in which case edges are code-driven.
"""

from __future__ import annotations

from typing import Any, Callable

from pydantic import BaseModel

from .models import VerifyResult, WorkflowContext

Verifier = Callable[[Any, WorkflowContext], VerifyResult]
Updater = Callable[[Any, WorkflowContext], WorkflowContext]
Action = Callable[[WorkflowContext], dict]
Router = Callable[[Any, WorkflowContext], list[str]]
SkillResolver = Callable[[str, WorkflowContext], str]

_SCHEMAS: dict[str, type[BaseModel]] = {}
_VERIFIERS: dict[str, Verifier] = {}
_UPDATERS: dict[str, Updater] = {}
_ACTIONS: dict[str, Action] = {}
_ROUTERS: dict[str, Router] = {}
_SKILL_RESOLVERS: dict[str, SkillResolver] = {}


def schema(name: str) -> Callable[[type[BaseModel]], type[BaseModel]]:
    def deco(cls: type[BaseModel]) -> type[BaseModel]:
        _SCHEMAS[name] = cls
        return cls

    return deco


def verifier(name: str) -> Callable[[Verifier], Verifier]:
    def deco(fn: Verifier) -> Verifier:
        _VERIFIERS[name] = fn
        return fn

    return deco


def updater(name: str) -> Callable[[Updater], Updater]:
    def deco(fn: Updater) -> Updater:
        _UPDATERS[name] = fn
        return fn

    return deco


def action(name: str) -> Callable[[Action], Action]:
    def deco(fn: Action) -> Action:
        _ACTIONS[name] = fn
        return fn

    return deco


def router(name: str) -> Callable[[Router], Router]:
    def deco(fn: Router) -> Router:
        _ROUTERS[name] = fn
        return fn

    return deco


def skill_resolver(name: str) -> Callable[[SkillResolver], SkillResolver]:
    """Register a resolver for a dynamic skill reference, keyed by its bare name
    (e.g. 'lang' handles the '@lang' reference)."""

    def deco(fn: SkillResolver) -> SkillResolver:
        _SKILL_RESOLVERS[name] = fn
        return fn

    return deco


def get_schema(name: str) -> type[BaseModel]:
    if name not in _SCHEMAS:
        raise KeyError(f"schema '{name}' is not registered")
    return _SCHEMAS[name]


def get_verifier(name: str | None) -> Verifier:
    if name is None:
        return _default_verifier
    if name not in _VERIFIERS:
        raise KeyError(f"verifier '{name}' is not registered")
    return _VERIFIERS[name]


def get_updater(name: str | None) -> Updater | None:
    if name is None:
        return None
    if name not in _UPDATERS:
        raise KeyError(f"updater '{name}' is not registered")
    return _UPDATERS[name]


def get_action(name: str) -> Action:
    if name not in _ACTIONS:
        raise KeyError(f"action '{name}' is not registered")
    return _ACTIONS[name]


def get_router(name: str) -> Router:
    if name not in _ROUTERS:
        raise KeyError(f"router '{name}' is not registered")
    return _ROUTERS[name]


def resolve_skill(ref: str, context: WorkflowContext) -> str:
    """Expand a skill reference. '@name' is resolved by the registered resolver;
    anything else is returned unchanged."""

    if not ref.startswith("@"):
        return ref
    key = ref[1:]
    if key not in _SKILL_RESOLVERS:
        raise KeyError(f"no skill resolver registered for '@{key}'")
    return _SKILL_RESOLVERS[key](ref, context)


def is_registered(kind: str, name: str) -> bool:
    table = {
        "schema": _SCHEMAS,
        "verifier": _VERIFIERS,
        "updater": _UPDATERS,
        "action": _ACTIONS,
        "router": _ROUTERS,
    }[kind]
    return name in table


def default_store(output: Any, context: WorkflowContext, produces: str | None) -> WorkflowContext:
    """Default context update when a node declares no updater.

    json output with a `produces` key -> store under that key.
    json output without `produces`   -> shallow-merge the dict into data.
    text output                      -> store the string under `produces`.
    """

    if isinstance(output, BaseModel):
        payload: Any = output.model_dump()
    else:
        payload = output
    if produces:
        context.data[produces] = payload
    elif isinstance(payload, dict):
        context.data.update(payload)
    else:  # pragma: no cover - guarded by graph validation
        context.data["last_output"] = payload
    return context


def _default_verifier(output: Any, context: WorkflowContext) -> VerifyResult:
    return VerifyResult(ok=True)
