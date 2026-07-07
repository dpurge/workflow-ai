"""Typer CLI: run, validate, and list workflow graphs."""

from __future__ import annotations

from pathlib import Path

import typer

from . import phraseforge  # noqa: F401  (registers phraseforge schemas/actions/routers/verifiers)
from . import research  # noqa: F401  (registers research schemas/verifiers/updaters)
from .backends.anthropic_sdk import AnthropicBackend
from .backends.copilot import CopilotBackend
from .backends.openai_sdk import OpenAIBackend
from . import copilot_auth
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


def _make_backend(
    backend: str,
    api_base_url: str | None,
    api_key: str | None,
    model: str | None,
    default_headers: dict[str, str] | None = None,
    azure_endpoint: str | None = None,
    api_version: str | None = None,
    copilot_config: str | None = None,
):
    if backend == "anthropic":
        return AnthropicBackend(
            model=model,
            api_key=api_key,
            api_base_url=api_base_url,
            default_headers=default_headers,
        )
    if backend == "openai":
        return OpenAIBackend(
            model=model,
            api_key=api_key,
            api_base_url=api_base_url,
            default_headers=default_headers,
            azure_endpoint=azure_endpoint,
            api_version=api_version,
        )
    if backend == "copilot":
        return CopilotBackend(
            model=model,
            api_key=api_key,
            api_base_url=api_base_url,
            default_headers=default_headers,
            copilot_config=copilot_config,
        )
    raise typer.BadParameter(
        f"unknown backend '{backend}' (use 'anthropic', 'openai', or 'copilot')"
    )


@app.command()
def run(
    workflow: str = typer.Argument(..., help="Workflow name or path"),
    prompt: str = typer.Option("", "--prompt", "-p", help="Initial prompt for the start state"),
    backend: str = typer.Option(None, "--backend", help="Agent backend: anthropic | openai | copilot"),
    api_base_url: str = typer.Option(None, "--api-base-url", help="API base URL (e.g. http://localhost:11434 for Ollama, https://openrouter.ai/api/v1)"),
    api_key: str = typer.Option(None, "--api-key", help="API key for the target endpoint"),
    model: str = typer.Option(None, "--model", help="Model id (e.g. gemma2:9b)"),
    source: str = typer.Option(None, "--source", help="phraseforge: source URL or file path"),
    level: str = typer.Option(None, "--level", help="phraseforge: CEFR level a1..c2"),
    translation_lang: str = typer.Option(None, "--translation-lang", help="phraseforge: gloss language"),
    cwd: str = typer.Option(None, "--cwd", help="phraseforge: base dir for docs/<lang>/<level>/"),
    default_header: list[str] = typer.Option(
        None, "--default-header",
        help="HTTP header in KEY:VALUE format (repeatable). E.g. --default-header Authorization:Bearer token",
    ),
    azure_endpoint: str = typer.Option(None, "--azure-endpoint", help="Azure OpenAI resource endpoint"),
    api_version: str = typer.Option(None, "--api-version", help="Azure OpenAI API version"),
    copilot_config: str = typer.Option(None, "--copilot-config", help="Path to Copilot credentials file (default: ~/.config/workflow-ai/copilot.json)"),
    retries: int = typer.Option(None, "--retries", help="Override per-node retry count"),
    out: str = typer.Option(None, "--out", help="Output directory for results"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Print node events and context snapshots to stdout"),
    log_file: str = typer.Option(None, "--log-file", help="Write verbose output to this file (in addition to stdout when --verbose)"),
) -> None:
    """Run a workflow end-to-end with the selected backend."""

    cfg = load_config()

    # Resolve: explicit CLI flag > config file
    backend = backend or cfg.backend
    if not backend:
        raise typer.BadParameter(
            "no backend set; pass --backend or set 'backend:' in ~/.config/workflow-ai/config.yaml",
            param_hint="--backend",
        )
    model = model or cfg.model
    api_base_url = api_base_url or cfg.api_base_url
    api_key = api_key or cfg.api_key
    retries = retries if retries is not None else cfg.retries

    # Parse --default-header KEY:VALUE flags into a dict
    parsed_headers: dict[str, str] | None = None
    if default_header:
        parsed_headers = {}
        for h in default_header:
            if ":" not in h:
                raise typer.BadParameter(
                    f"--default-header must be KEY:VALUE, got: {h!r}",
                    param_hint="--default-header",
                )
            k, _, v = h.partition(":")
            parsed_headers[k.strip()] = v.strip()

    # Resolve from config if not set via CLI
    if parsed_headers is None and cfg.default_headers:
        parsed_headers = cfg.default_headers
    azure_endpoint = azure_endpoint or cfg.azure_endpoint
    api_version = api_version or cfg.api_version
    copilot_config = copilot_config or cfg.copilot_config
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

    engine = Engine(_make_backend(backend, api_base_url, api_key, model, parsed_headers, azure_endpoint, api_version, copilot_config=copilot_config))

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


# ---------------------------------------------------------------------------
# Copilot sub-app
# ---------------------------------------------------------------------------

copilot_app = typer.Typer(help="Manage GitHub Copilot credentials.")
app.add_typer(copilot_app, name="copilot")


@copilot_app.command("login")
def copilot_login(
    client_id: str = typer.Option(
        copilot_auth.DEFAULT_CLIENT_ID,
        "--client-id",
        help="GitHub OAuth App client ID for device flow",
    ),
    copilot_config: str = typer.Option(
        None,
        "--copilot-config",
        help="Path to Copilot credentials file",
    ),
) -> None:
    """Authenticate with GitHub Copilot via device flow."""
    from .backends.base import AgentOutputError

    try:
        copilot_auth.login(client_id=client_id, config_path=copilot_config)
    except AgentOutputError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(1)


@copilot_app.command("status")
def copilot_status(
    copilot_config: str = typer.Option(
        None,
        "--copilot-config",
        help="Path to Copilot credentials file",
    ),
) -> None:
    """Show current GitHub Copilot credential status."""
    from .backends.base import AgentOutputError

    try:
        copilot_auth.status(config_path=copilot_config)
    except AgentOutputError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(1)


if __name__ == "__main__":
    app()
