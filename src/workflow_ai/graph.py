"""Load a workflow DAG from YAML and validate it before any run.

A workflow is a directed acyclic graph with exactly one start node, internal
nodes, and one or more terminal nodes. A node is one of:
  - model node (default): runs the agent. `output_kind` is "json" (validated
    against `schema`) or "text" (raw prose, stored under `produces`).
  - action node (`kind: action`): runs a registered Python `action` instead of
    the agent (e.g. fetch a URL, render a file).

Transitions come from the node's `next` map. Edges are model-chosen
(`next_state`/`next_states` in the output) unless the node declares a `router`,
which computes the successor(s) in code from output + context.
"""

from __future__ import annotations

from pathlib import Path

import networkx as nx
import yaml
from pydantic import BaseModel, Field

from . import registry


class GraphError(ValueError):
    """Raised when a workflow definition is structurally invalid."""


class NodeSpec(BaseModel):
    """A single node in the workflow graph."""

    id: str
    role: str = ""
    prompt: str | None = None
    kind: str = "model"  # "model" | "action"
    output_kind: str = "json"  # "json" | "text"
    schema_name: str | None = Field(default=None, alias="schema")
    verifier: str | None = None
    updater: str | None = None
    action: str | None = None
    router: str | None = None
    reads: list[str] = Field(default_factory=list)
    produces: str | None = None
    allowed_tools: list[str] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    mcp_config: str | None = None
    model: str | None = None
    max_turns: int | None = None
    retries: int = 3
    fan_out: bool = False
    terminal: bool = False
    next: dict[str, str] = Field(default_factory=dict)

    model_config = {"populate_by_name": True}

    @property
    def successors(self) -> list[str]:
        return list(self.next.keys())


class WorkflowGraph(BaseModel):
    name: str
    start: str
    nodes: dict[str, NodeSpec]

    @classmethod
    def from_yaml(cls, path: str | Path) -> "WorkflowGraph":
        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        nodes = {
            node_id: NodeSpec(id=node_id, **body)
            for node_id, body in (raw.get("nodes") or {}).items()
        }
        graph = cls(name=raw["name"], start=raw["start"], nodes=nodes)
        graph.validate_dag()
        return graph

    def node(self, node_id: str) -> NodeSpec:
        if node_id not in self.nodes:
            raise GraphError(f"unknown node '{node_id}'")
        return self.nodes[node_id]

    def validate_dag(self) -> None:
        """Enforce graph well-formedness and that all logic references resolve."""

        if self.start not in self.nodes:
            raise GraphError(f"start node '{self.start}' is not defined")

        dg = nx.DiGraph()
        dg.add_nodes_from(self.nodes)

        terminals: list[str] = []
        for node in self.nodes.values():
            if node.terminal:
                terminals.append(node.id)
                if node.next:
                    raise GraphError(f"terminal node '{node.id}' must not declare transitions")
                continue

            if not node.next:
                raise GraphError(f"internal node '{node.id}' has no transitions")

            for target in node.successors:
                if target not in self.nodes:
                    raise GraphError(
                        f"node '{node.id}' transitions to unknown node '{target}'"
                    )
                dg.add_edge(node.id, target)

            self._validate_node_logic(node)

        if not terminals:
            raise GraphError("workflow has no terminal node")

        if not nx.is_directed_acyclic_graph(dg):
            cycle = nx.find_cycle(dg)
            raise GraphError(f"workflow graph is not acyclic; cycle: {cycle}")

        unreachable = set(self.nodes) - {self.start} - set(nx.descendants(dg, self.start))
        if unreachable:
            raise GraphError(f"unreachable nodes from start: {sorted(unreachable)}")

    def _validate_node_logic(self, node: NodeSpec) -> None:
        if node.kind not in ("model", "action"):
            raise GraphError(f"node '{node.id}' has invalid kind '{node.kind}'")
        if node.output_kind not in ("json", "text"):
            raise GraphError(
                f"node '{node.id}' has invalid output_kind '{node.output_kind}'"
            )

        if node.kind == "action":
            if not node.action:
                raise GraphError(f"action node '{node.id}' must declare 'action'")
            if not registry.is_registered("action", node.action):
                raise GraphError(
                    f"node '{node.id}' references unregistered action '{node.action}'"
                )

        # Model json nodes need a registered schema (native/validated structure).
        # Action json nodes may omit it (their returned dict is merged/stored).
        # Text nodes need a produces key.
        if node.output_kind == "json":
            if node.kind == "model" and not node.schema_name:
                raise GraphError(f"model json node '{node.id}' must declare 'schema'")
            if node.schema_name and not registry.is_registered("schema", node.schema_name):
                raise GraphError(
                    f"node '{node.id}' references unregistered schema '{node.schema_name}'"
                )
        elif not node.produces:
            raise GraphError(f"text node '{node.id}' must declare 'produces'")

        if node.verifier and not registry.is_registered("verifier", node.verifier):
            raise GraphError(
                f"node '{node.id}' references unregistered verifier '{node.verifier}'"
            )
        if node.updater and not registry.is_registered("updater", node.updater):
            raise GraphError(
                f"node '{node.id}' references unregistered updater '{node.updater}'"
            )
        if node.router and not registry.is_registered("router", node.router):
            raise GraphError(
                f"node '{node.id}' references unregistered router '{node.router}'"
            )
