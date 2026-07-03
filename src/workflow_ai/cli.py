"""Typer CLI: run, validate, and list workflow graphs."""

from __future__ import annotations

from pathlib import Path

import typer

from . import definitions  # noqa: F401  (registers research schemas/verifiers/updaters)
from . import lessons  # noqa: F401  (registers phraseforge schemas/actions/routers/verifiers)
from .backends.claude_code import ClaudeCodeBackend
from .backends.pi import PiBackend
from .engine import Engine, dump_run
from .graph import GraphError, WorkflowGraph

app = typer.Typer(
    help="Force a headless coding agent through a strict workflow DAG.",
    no_args_is_help=True,
)

WORKFLOW_DIR = Path(__file__).parent / "workflows"


def _resolve(workflow: str) -> Path:
    """Resolve a workflow name or path to a YAML file."""

    candidate = Path(workflow)
    if candidate.exists():
        return candidate
    builtin = WORKFLOW_DIR / f"{workflow}.yaml"
    if builtin.exists():
        return builtin
    raise typer.BadParameter(f"workflow '{workflow}' not found")


@app.command("list")
def list_workflows() -> None:
    """List built-in workflows."""

    for path in sorted(WORKFLOW_DIR.glob("*.yaml")):
        typer.echo(path.stem)


@app.command()
def validate(workflow: str = typer.Argument(..., help="Workflow name or path")) -> None:
    """Load and DAG-validate a workflow without running it."""

    try:
        graph = WorkflowGraph.from_yaml(_resolve(workflow))
    except GraphError as exc:
        typer.secho(f"INVALID: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)
    typer.secho(
        f"OK: '{graph.name}' valid — {len(graph.nodes)} nodes, start='{graph.start}'",
        fg=typer.colors.GREEN,
    )


def _make_backend(backend: str, provider: str | None, model: str | None):
    if backend == "claude":
        return ClaudeCodeBackend()
    if backend == "pi":
        return PiBackend(provider=provider, model=model)
    raise typer.BadParameter(f"unknown backend '{backend}' (use 'claude' or 'pi')")


@app.command()
def run(
    workflow: str = typer.Argument(..., help="Workflow name or path"),
    prompt: str = typer.Option("", "--prompt", "-p", help="Initial prompt for the start state"),
    backend: str = typer.Option("claude", "--backend", help="Agent backend: claude | pi"),
    provider: str = typer.Option(None, "--provider", help="Backend provider (e.g. ollama for Pi)"),
    model: str = typer.Option(None, "--model", help="Model id (e.g. gemma2:9b)"),
    source: str = typer.Option(None, "--source", help="phraseforge: source URL or file path"),
    level: str = typer.Option(None, "--level", help="phraseforge: CEFR level a1..c2"),
    translation_lang: str = typer.Option("pol", "--translation-lang", help="phraseforge: gloss language"),
    cwd: str = typer.Option(".", "--cwd", help="phraseforge: base dir for docs/<lang>/<level>/"),
    retries: int = typer.Option(None, "--retries", help="Override per-node retry count"),
    out: str = typer.Option("runs/latest", "--out", help="Output directory for results"),
) -> None:
    """Run a workflow end-to-end with the selected backend."""

    try:
        graph = WorkflowGraph.from_yaml(_resolve(workflow))
    except GraphError as exc:
        typer.secho(f"INVALID workflow: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)

    initial_data: dict = {}
    if source:
        kind = "url" if source.startswith(("http://", "https://")) else "file"
        initial_data["source_ref"] = {"kind": kind, "value": source}
    if level:
        initial_data["level"] = level.lower()
    initial_data["translation_lang"] = translation_lang
    initial_data["cwd"] = cwd

    engine = Engine(_make_backend(backend, provider, model))

    def on_event(kind: str, node_id: str) -> None:
        if kind == "enter":
            typer.secho(f"→ {node_id}", fg=typer.colors.CYAN)

    result = engine.run(
        graph,
        prompt,
        initial_data=initial_data,
        retries_override=retries,
        model_override=model,
        on_event=on_event,
    )
    path = dump_run(result, out)
    typer.secho(
        f"Done: {len(result.branches)} terminal branch(es) → {path}",
        fg=typer.colors.GREEN,
    )


if __name__ == "__main__":
    app()
