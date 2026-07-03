# workflow-ai

A strict-DAG harness that forces a **headless coding agent** (Claude Code in v1)
through a defined **workflow graph** instead of letting it free-roam.

Each node runs the agent in a **fresh context** (no shared history), produces
**schema-validated JSON**, has its output **verified** and folded into a Pydantic
**workflow context**, and chooses its **next state(s)** from a declared, described
set — so control flow belongs to the framework, not the model.

## Concepts

- **DAG** — one start node, internal nodes, one or more terminal nodes. Validated
  for acyclicity and reachability at load time.
- **Hybrid definition** — the graph is **YAML**; the per-node logic (output
  schema, verifier, updater, action, router) is **Python**, referenced by name
  from the YAML and registered via decorators.
- **Node kinds** — `model` (runs the agent) or `action` (runs registered Python:
  fetch a URL, render a file). Model output is `json` (schema-validated) or
  `text` (raw prose — more reliable for small models).
- **Transitions** — resolved by, in order: a declared `router` (code), a single
  static successor, or a model-chosen `next_state`/`next_states`.
- **`reads`/`produces`** — a node sees only the context slice it `reads` and
  stores its output under `produces` (keeps prompts small).
- **Fan-out** — a node may return a list of next states; each becomes an
  independent branch with a **deep-copied context**, collected at its terminal.
- **Retries** — parse/schema/verify failures retry up to N (default 3), feeding
  the error back into the next attempt's prompt.

## Backends

Two backends implement the `AgentBackend` protocol:

- **Claude Code** (`--backend claude`, default) — `claude -p` headless; enforces
  JSON via `--json-schema`.
- **Pi** (`--backend pi`) — `pi --print --mode json` headless; reconstructs
  assistant text from Pi's event stream (no native schema, so the engine
  validates JSON and retries). Point Pi at a local model by configuring
  `~/.pi/agent/models.json` and passing `--provider ollama --model <id>`.

## Install

```bash
uv sync
uv run workflow-ai list          # list built-in workflows
uv run workflow-ai validate <wf> # DAG-validate without running
```

## Running each workflow

Results are always written to `--out` (default `runs/latest`): `result.json` plus
one context file per terminal branch.

### `research` — fan-out web/file research → summary

Uses the **Claude Code** backend (default). Requires the `claude` CLI on PATH.

```bash
uv run workflow-ai run research \
  --prompt "Research the tradeoffs of X" \
  --out runs/research
```

The start node classifies the topic and fans out to web + file gathering, then
synthesizes a summary.

### `phraseforge` — web page → language-lesson MDX

Mirrors Pi's `phraseforge-mdx` workflow. Writes the lesson to
`docs/<lang>/<level>/<YYYY-MM-DD>-<seq>.mdx` under `--cwd`. Requires the `pi` CLI,
`uv`, and Pi's `mdx-export.py` (reused as the renderer + validation gate).

**Use Pi's configured default model** (whatever `~/.pi/agent/settings.json` points
to — e.g. github-copilot):

```bash
uv run workflow-ai run phraseforge --backend pi \
  --source https://de.wikipedia.org/wiki/Kaffee \
  --level a2 \
  --out runs/phraseforge
```

**Pin a local Ollama model** (e.g. on a machine where Pi is set up for Ollama) —
configure `~/.pi/agent/models.json` with an `ollama` provider first, then:

```bash
uv run workflow-ai run phraseforge --backend pi \
  --provider ollama --model gemma4:e4b \
  --source https://de.wikipedia.org/wiki/Kaffee \
  --level a2
```

`--source` accepts a URL or a local file path. `--level` is the CEFR level
(`a1`..`c2`). `--translation-lang` sets the gloss language (default `pol`).
Latin/Cyrillic/Greek sources skip the transcription step automatically.

### Run options (both workflows)

| Option | Meaning |
|--------|---------|
| `--backend claude\|pi` | agent backend (default `claude`) |
| `--provider`, `--model` | backend provider / model id (omit to use the backend's default) |
| `--retries N` | override per-node retry count (default 3) |
| `--out DIR` | results directory |
| `--source`, `--level`, `--translation-lang`, `--cwd` | `phraseforge` inputs |

## Layout

| Path | Purpose |
|------|---------|
| `src/workflow_ai/graph.py` | YAML load + DAG validation |
| `src/workflow_ai/registry.py` | schema / verifier / updater registries |
| `src/workflow_ai/definitions.py` | registered logic for sample workflows |
| `src/workflow_ai/backends/claude_code.py` | headless `claude -p` adapter |
| `src/workflow_ai/backends/pi.py` | headless `pi --mode json` adapter |
| `src/workflow_ai/engine.py` | worklist executor (action/model, json/text, router, fan-out, retry) |
| `src/workflow_ai/cli.py` | Typer CLI |
| `src/workflow_ai/lessons/` | phraseforge schemas + actions/router/verifiers |
| `src/workflow_ai/workflows/*.yaml` | workflow graphs (`research`, `phraseforge`) |

## Test

```bash
uv run pytest
```

Tests mock the backend via the `AgentBackend` protocol, so no live agent is
needed.

## Adding a backend

Implement the `AgentBackend` protocol (`backends/base.py`) — a single
`run(invocation) -> AgentResult`. The Pi coding agent is the planned second
backend.
