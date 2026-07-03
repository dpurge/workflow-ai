"""Pydantic data structures carried through a workflow run.

The WorkflowContext is the single mutable state object passed between nodes.
Node outputs are validated against per-node schemas (see registry/definitions),
verified, then folded into the context by an updater function.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class VerifyResult(BaseModel):
    """Outcome of a node's semantic output verification (beyond schema)."""

    ok: bool
    errors: list[str] = Field(default_factory=list)


class NodeRun(BaseModel):
    """One executed node attempt that succeeded, recorded in history."""

    node_id: str
    attempts: int
    raw_output: Any
    verify: VerifyResult
    cost_usd: float | None = None


class WorkflowContext(BaseModel):
    """The state threaded through the DAG and updated after each node.

    `data` accumulates whatever updater functions choose to store; `history`
    is the ordered list of completed node runs along this branch.
    """

    initial_prompt: str
    data: dict[str, Any] = Field(default_factory=dict)
    history: list[NodeRun] = Field(default_factory=list)

    def record(self, run: NodeRun) -> None:
        self.history.append(run)


class BranchResult(BaseModel):
    """A single terminal branch outcome."""

    terminal_node: str
    context: WorkflowContext


class RunResult(BaseModel):
    """Aggregate result of a full workflow run (one entry per terminal branch)."""

    workflow: str
    branches: list[BranchResult] = Field(default_factory=list)
