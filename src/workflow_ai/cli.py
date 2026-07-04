"""Typer CLI: run, validate, and list workflow graphs."""

from __future__ import annotations

from pathlib import Path

import typer

from . import phraseforge  # noqa: F401  (registers phraseforge schemas/actions/routers/verifiers)
from . import research  # noqa: F401  (registers research schemas/verifiers/updaters)
from .backends.claude import ClaudeCodeBackend
from .backends.codex import CodexBackend
from .backends.pi import PiBackend
from .config import load_config
from .engine import Engine, dump_run
from .logger import RunLogger
from .graph import GraphError, WorkflowGraph

app = typer.Typer(
    help="Force a headless coding agent through a strict workflow DAG.",
    no_args_is_help=True,
)

_PACKAGE_DIR = Path(__file__).parent


def _resolve(workflow: str) -> Path:
    """Resolve a workflow name or path to a YAML file."""

    candidate = Path(workflow)
    if candidate.exists():
        return candidate
    builtin = _PACKAGE_DIR / workflow / "workflow.yaml"
    if builtin.exists():
        return builtin
    raise typer.BadParameter(f"workflow '{workflow}' not found")


@app.command("list")
def list_workflows() -> None:
    """List built-in workflows."""

    for path in sorted(_PACKAGE_DIR.glob("*/workflow.yaml")):
        typer.echo(path.parent.name)


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


_CODEX_WARNINGS = [
    "allowed_tools is ignored — Codex manages tool access via its own sandbox.",
    "skills is ignored — Codex has no skill-injection flag.",
    "mcp_config is ignored — Codex has no MCP config flag.",
    "cost_usd is always None — Codex stdout carries no cost information.",
]


def _make_backend(backend: str, api_base_url: str | None, api_key: str | None, model: str | None):
    if backend == "claude":
        return ClaudeCodeBackend(api_base_url=api_base_url, api_key=api_key)
    if backend == "pi":
        return PiBackend(api_base_url=api_base_url, api_key=api_key, model=model)
    if backend == "codex":
        typer.secho("Codex backend limitations:", fg=typer.colors.YELLOW, err=True)
        for w in _CODEX_WARNINGS:
            typer.secho(f"  • {w}", fg=typer.colors.YELLOW, err=True)
        return CodexBackend(api_base_url=api_base_url, api_key=api_key, model=model)
    raise typer.BadParameter(f"unknown backend '{backend}' (use 'claude', 'pi', or 'codex')")


@app.command()
def run(
    workflow: str = typer.Argument(..., help="Workflow name or path"),
    prompt: str = typer.Option("", "--prompt", "-p", help="Initial prompt for the start state"),
    backend: str = typer.Option(None, "--backend", help="Agent backend: claude | pi | codex"),
    api_base_url: str = typer.Option(None, "--api-base-url", help="API base URL (e.g. http://localhost:11434 for Ollama, https://openrouter.ai/api/v1)"),
    api_key: str = typer.Option(None, "--api-key", help="API key for the target endpoint"),
    model: str = typer.Option(None, "--model", help="Model id (e.g. gemma2:9b)"),
    source: str = typer.Option(None, "--source", help="phraseforge: source URL or file path"),
    level: str = typer.Option(None, "--level", help="phraseforge: CEFR level a1..c2"),
    translation_lang: str = typer.Option(None, "--translation-lang", help="phraseforge: gloss language"),
    cwd: str = typer.Option(None, "--cwd", help="phraseforge: base dir for docs/<lang>/<level>/"),
    retries: int = typer.Option(None, "--retries", help="Override per-node retry count"),
    out: str = typer.Option(None, "--out", help="Output directory for results"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Print node events and context snapshots to stdout"),
    log_file: str = typer.Option(None, "--log-file", help="Write verbose output to this file (in addition to stdout when --verbose)"),
) -> None:
    """Run a workflow end-to-end with the selected backend."""

    cfg = load_config()

    # Resolve: explicit CLI flag > config file > hardcoded default
    backend = backend or cfg.backend or "claude"
    model = model or cfg.model
    api_base_url = api_base_url or cfg.api_base_url
    api_key = api_key or cfg.api_key
    retries = retries if retries is not None else cfg.retries
    out = out or cfg.out or "runs/latest"
    verbose = verbose or (cfg.verbose or False)
    log_file = log_file or cfg.log_file
    translation_lang = translation_lang or cfg.phraseforge.translation_lang
    cwd = cwd or cfg.phraseforge.cwd
    level = level or cfg.phraseforge.level

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
    if translation_lang:
        initial_data["translation_lang"] = translation_lang
    if cwd:
        initial_data["cwd"] = cwd

    engine = Engine(_make_backend(backend, api_base_url, api_key, model))

    log_path = Path(log_file) if log_file else None
    with RunLogger(verbose=verbose, log_file=log_path) as logger:
        def on_event(kind: str, node_id: str, data: dict | None = None) -> None:
            if kind == "enter":
                typer.secho(f"→ {node_id}", fg=typer.colors.CYAN)
            logger(kind, node_id, data)

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
