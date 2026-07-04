# workflow-ai

A strict-DAG harness that forces a **headless coding agent**
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

Three backends implement the `AgentBackend` protocol:

- **Claude Code** (`--backend claude`, default) — `claude -p` headless; enforces
  JSON via `--json-schema`.
- **Pi** (`--backend pi`) — `pi --print --mode json` headless; reconstructs
  assistant text from Pi's event stream (no native schema, so the engine
  validates JSON and retries). Redirect to Ollama or OpenRouter via
  `--api-base-url` and `--api-key`.
- **Codex** (`--backend codex`) — `codex --quiet --approval-mode full-auto`
  headless; returns plain text (no native schema, engine extracts/validates JSON
  and retries). Requires an OpenAI API key — pass via `--api-key` or set
  `OPENAI_API_KEY` directly. Redirect to OpenRouter or Ollama via
  `--api-base-url` and `--api-key`. Pass `--model <id>` to override the default
  (`o4-mini`).

  **Limitations** (printed as warnings at startup):
  - `allowed_tools` is ignored — Codex manages tool access via its own sandbox.
  - `skills` is ignored — Codex has no skill-injection flag.
  - `mcp_config` is ignored — Codex has no MCP config flag.
  - `cost_usd` is always `None` — Codex stdout carries no cost information.

### Not supported

- **opencode** (`sst/opencode`) — no headless mode. The CLI launches a full
  interactive TUI (Ink/React); there is no `--print`, `--quiet`, positional
  prompt, or stdin-prompt flag. Running it without a TTY hangs or crashes. An
  opencode backend will be straightforward to add once the project ships a
  non-interactive mode.

## Install

```bash
uv sync
uv run workflow-ai list          # list built-in workflows
uv run workflow-ai validate <wf> # DAG-validate without running
```

## Docker / CI

The `Dockerfile` builds a self-contained image with `claude`, `pi`, `codex`, and
`workflow-ai` pre-installed. Pass everything through environment variables; mount
`/runs` to retrieve results.

```bash
docker build -t workflow-ai .

docker run --rm \
  -e WORKFLOW=research \
  -e WORKFLOW_PROMPT="Research lunar habitats" \
  -e ANTHROPIC_API_KEY="${ANTHROPIC_API_KEY}" \
  -v "$(pwd)/runs:/runs" \
  workflow-ai
```

### Environment variables

| Variable | Required | Description |
|----------|----------|-------------|
| `WORKFLOW` | **yes** | Workflow name (`research`, `phraseforge`, …) |
| `WORKFLOW_PROMPT` | no | Initial prompt passed to the start node |
| `WORKFLOW_BACKEND` | no | Backend: `claude` (default) \| `pi` \| `codex` |
| `WORKFLOW_MODEL` | no | Model id (e.g. `claude-sonnet-4-6`, `gemma3:9b`) |
| `WORKFLOW_API_BASE_URL` | no | API base URL — redirect to Ollama, OpenRouter, etc. |
| `WORKFLOW_API_KEY` | no | API key for the target endpoint |
| `WORKFLOW_OUT` | no | Output directory inside the container (default `/runs`) |
| `WORKFLOW_SOURCE` | phraseforge | Source URL or file path (`--source`) |
| `WORKFLOW_LEVEL` | phraseforge | CEFR level a1..c2 (`--level`) |
| `WORKFLOW_TRANSLATION_LANG` | phraseforge | Gloss language (`--translation-lang`) |
| `WORKFLOW_CWD` | phraseforge | Base dir for lesson output (`--cwd`) |
| `ANTHROPIC_API_KEY` | claude | Claude API key (not needed when using Ollama) |
| `OPENAI_API_KEY` | codex | OpenAI API key (not needed when using Ollama) |
| `SSH_PRIVATE_KEY` | ssh | PEM private key for GitHub SSH auth |
| `SSH_KNOWN_HOSTS` | ssh | Known-hosts content; omit to auto-scan `github.com` |

Verbose output is always enabled — the workflow prints structured node events to
stdout so CI logs capture the full run.

### Local Ollama

Works with any backend. Pass the Ollama base URL and `ollama` as the key
(Ollama accepts any non-empty string as the API key).

**Claude + Ollama** (Ollama exposes an Anthropic-compatible API):

```bash
docker run --rm \
  -e WORKFLOW=research \
  -e WORKFLOW_PROMPT="Research lunar habitats" \
  -e WORKFLOW_BACKEND=claude \
  -e WORKFLOW_API_BASE_URL=http://host.docker.internal:11434 \
  -e WORKFLOW_API_KEY=ollama \
  -e WORKFLOW_MODEL=gemma3:9b \
  -v "$(pwd)/runs:/runs" \
  workflow-ai
```

**Pi + Ollama** (Ollama exposes an OpenAI-compatible `/v1` API):

```bash
docker run --rm \
  -e WORKFLOW=phraseforge \
  -e WORKFLOW_BACKEND=pi \
  -e WORKFLOW_API_BASE_URL=http://host.docker.internal:11434/v1 \
  -e WORKFLOW_API_KEY=ollama \
  -e WORKFLOW_MODEL=gemma3:9b \
  -e WORKFLOW_SOURCE="https://de.wikipedia.org/wiki/Kaffee" \
  -v "$(pwd)/runs:/runs" \
  workflow-ai
```

### OpenRouter

```bash
docker run --rm \
  -e WORKFLOW=research \
  -e WORKFLOW_PROMPT="Research X" \
  -e WORKFLOW_BACKEND=claude \
  -e WORKFLOW_API_BASE_URL=https://openrouter.ai/api/v1 \
  -e WORKFLOW_API_KEY="${OPENROUTER_API_KEY}" \
  -e WORKFLOW_MODEL=anthropic/claude-sonnet-4-6 \
  -v "$(pwd)/runs:/runs" \
  workflow-ai
```

### SSH for GitHub workflows

```bash
docker run --rm \
  -e WORKFLOW=research \
  -e WORKFLOW_PROMPT="..." \
  -e ANTHROPIC_API_KEY="${ANTHROPIC_API_KEY}" \
  -e SSH_PRIVATE_KEY="$(cat ~/.ssh/id_ed25519)" \
  -v "$(pwd)/runs:/runs" \
  workflow-ai
```

The entrypoint writes the key to `~/.ssh/id_rsa` (mode 600) and pre-trusts
`github.com` in `known_hosts` so workflows can clone and push without
interactive prompts.

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

**Pin a local Ollama model** (Pi backend — Ollama exposes an OpenAI-compatible `/v1` API):

```bash
uv run workflow-ai run phraseforge --backend pi \
  --api-base-url http://localhost:11434/v1 \
  --api-key ollama \
  --model gemma4:e4b \
  --source https://de.wikipedia.org/wiki/Kaffee \
  --level a2
```

**Claude + Ollama** (Ollama exposes an Anthropic-compatible API):

```bash
uv run workflow-ai run phraseforge \
  --api-base-url http://localhost:11434 \
  --api-key ollama \
  --model gemma4:e4b \
  --source https://de.wikipedia.org/wiki/Kaffee \
  --level a2
```

`--source` accepts a URL or a local file path. `--level` is the CEFR level
(`a1`..`c2`). `--translation-lang` sets the gloss language (set a default in the
config file). Latin/Cyrillic/Greek/Korean (Hangul) sources skip the transcription step automatically.

### Config file

Create `~/.config/workflow-ai/config.yaml` to set persistent defaults. All
fields are optional; a missing file is silently ignored. CLI flags always take
precedence over config values.

```yaml
backend: claude          # claude | pi | codex
model: null              # default model id
api_base_url: null       # API base URL (e.g. http://localhost:11434 for Ollama)
api_key: null            # API key for the target endpoint
retries: 3               # per-node retry count
out: runs/latest         # output directory
verbose: false           # print node events to stdout
log_file: null           # write verbose output to this file

phraseforge:
  level: null            # CEFR level a1..c2
  translation_lang: pol  # gloss language
  cwd: .                 # base dir for docs/<lang>/<level>/
```

### Run options (both workflows)

| Option | Meaning |
|--------|---------|
| `--backend claude\|pi\|codex` | agent backend (default `claude`) |
| `--api-base-url URL` | redirect to Ollama, OpenRouter, etc. (e.g. `http://localhost:11434`) |
| `--api-key KEY` | API key for the target endpoint; use `ollama` for local Ollama |
| `--model ID` | model id to pass to the backend (omit to use the backend's default) |
| `--retries N` | override per-node retry count (default 3) |
| `--out DIR` | results directory |
| `--verbose` / `-v` | print node events and context snapshots to stdout as the run progresses |
| `--log-file PATH` | write the same verbose output to a file (in addition to stdout when `--verbose`; file-only otherwise) |
| `--source`, `--level`, `--translation-lang`, `--cwd` | `phraseforge` inputs |

## Layout

| Path | Purpose |
|------|---------|
| `src/workflow_ai/graph.py` | YAML load + DAG validation |
| `src/workflow_ai/registry.py` | schema / verifier / updater registries |
| `src/workflow_ai/engine.py` | worklist executor (action/model, json/text, router, fan-out, retry) |
| `src/workflow_ai/cli.py` | Typer CLI |
| `src/workflow_ai/config.py` | config file loader (`~/.config/workflow-ai/config.yaml`) |
| `src/workflow_ai/backends/claude.py` | headless `claude -p` adapter |
| `src/workflow_ai/backends/pi.py` | headless `pi --mode json` adapter |
| `src/workflow_ai/backends/codex.py` | headless `codex --quiet` adapter |
| `src/workflow_ai/research/workflow.yaml` | research workflow graph |
| `src/workflow_ai/research/schemas.py` | research output schemas |
| `src/workflow_ai/research/definitions.py` | research verifiers + updaters |
| `src/workflow_ai/research/skills/` | skill files injected into research nodes |
| `src/workflow_ai/phraseforge/workflow.yaml` | phraseforge workflow graph |
| `src/workflow_ai/phraseforge/schemas.py` | phraseforge output schemas |
| `src/workflow_ai/phraseforge/definitions.py` | phraseforge actions / router / verifiers |
| `src/workflow_ai/phraseforge/skills/` | skill files injected into phraseforge nodes |
| `Dockerfile` | self-contained image with all three backends + workflow-ai |
| `entrypoint.sh` | Docker entrypoint: SSH setup, env-var → CLI arg mapping |

## Test

```bash
uv run pytest
```

Tests mock the backend via the `AgentBackend` protocol, so no live agent is
needed.

## Adding a backend

Implement the `AgentBackend` protocol (`backends/base.py`) — a single
`run(invocation) -> AgentResult`. See `backends/claude.py`, `backends/pi.py`,
and `backends/codex.py` for reference implementations.

---

## Tutorial: Writing Workflows

This section explains how workflows are structured, how to modify an existing
one, and how to build a new one from scratch.

### How the two parts fit together

Every workflow lives in its own package directory with three files:

```
src/workflow_ai/my_workflow/
  workflow.yaml       ← graph topology: nodes, edges, prompts
  schemas.py          ← Pydantic output contracts (@schema registrations)
  definitions.py      ← verifiers, updaters, actions, routers
```

The YAML references Python by name (e.g. `schema: PlanOut`). The Python side
registers those names via decorators in `registry.py`. The engine loads the YAML,
looks up each name in the registry, and runs the graph.

### Anatomy of a node

```yaml
nodes:
  classify:                        # unique node id
    role: |
      You are a research analyst.  # injected as system prompt
    prompt: |
      Classify this: {initial_prompt}   # user prompt; supports {tokens}
    kind: model                    # "model" = call agent, "action" = call Python fn
    output_kind: json              # "json" = schema-validated, "text" = raw prose
    schema: ClassifyOut            # name of a @schema-registered Pydantic class
    verifier: topic_present        # name of a @verifier function (optional)
    updater: store_topic           # name of a @updater function (optional)
    reads: [topic, findings]       # which context.data keys to expose in the prompt
    produces: topic                # where to store output in context.data
    allowed_tools: [WebSearch]     # tools the agent may call
    retries: 3                     # retry attempts on parse/verify failure
    next:
      search_web: "search the web"   # target_node_id: description shown to agent
      read_files: "read local files"
```

**`kind`**

| Value | What runs |
|-------|-----------|
| `model` | Calls the agent backend (Claude, Pi) |
| `action` | Calls a `@action`-registered Python function — no agent invoked |

**`output_kind`** (for `model` nodes)

| Value | Agent must return | Enforced how |
|-------|-------------------|--------------|
| `json` | JSON matching `schema` | Pydantic-validated; retried on failure |
| `text` | Free prose | Stored raw; `produces` is required |

Terminal nodes have no `next` block. The engine collects their final context
into `RunResult.branches`.

### Python registrations

Place schemas in `schemas.py` and verifiers / updaters / actions / routers in
`definitions.py` inside the workflow package. The package `__init__.py` imports
both so the decorators run when the CLI imports the package at startup.

```python
from typing import Literal
from pydantic import BaseModel
from workflow_ai.registry import schema, verifier, updater, action, router, skill_resolver
from workflow_ai.models import VerifyResult, WorkflowContext

# Output contract for a node — field names become {tokens} if listed in reads:
@schema("ClassifyOut")
class ClassifyOut(BaseModel):
    topic: str
    rationale: str
    # fan-out: agent picks one or more successors from the declared Literal set
    next_states: list[Literal["search_web", "read_files"]]

# Semantic check beyond schema validation
@verifier("topic_present")
def topic_present(output: ClassifyOut, context: WorkflowContext) -> VerifyResult:
    if not output.topic.strip():
        return VerifyResult(ok=False, errors=["topic is empty"])
    return VerifyResult(ok=True)

# Fold output into context.data so later nodes can read it
@updater("store_topic")
def store_topic(output: ClassifyOut, context: WorkflowContext) -> None:
    context.data["topic"] = output.topic
    context.data["rationale"] = output.rationale

# For kind: action nodes — pure Python, no agent
@action("fetch_source")
def fetch_source(context: WorkflowContext) -> dict:
    url = context.data["url"]
    ...
    return {"source": text}

# For router: my_fn nodes — code decides next states instead of the agent
@router("branch_on_script")
def branch_on_script(output, context: WorkflowContext) -> list[str]:
    if output.script in {"latn", "cyrl", "grek"}:
        return ["translate"]
    return ["transcribe"]
```

### How context flows between nodes

Each node runs in a shared `WorkflowContext` that accumulates state via updaters:

```
classify (updater: store_topic)
  → context.data["topic"] = "coffee"

search_web (updater: append_findings)
  → context.data["findings"] += [...]

synthesize (reads: [topic, findings])
  → sees topic and findings in its prompt
```

**`reads:`** declares which keys to inject into the prompt. It does two things:
limits what `{data}` contains (keeps prompts small), and unlocks each key as its
own `{key}` token.

**`produces:`** sets the key under which text-output nodes store their result.
JSON-output nodes are stored by their updater instead.

### Using context values in prompts

Prompts use Python's `str.format()`. The following tokens are always available:

```yaml
prompt: "User asked: {initial_prompt}"
```

With `reads:` declared, each listed key and a `{data}` JSON blob are also
available:

```yaml
reads:
  - topic
  - findings
prompt: |
  Topic: {topic}

  Findings so far:
  {findings}

  Original request: {initial_prompt}
```

Without `reads:`, `{data}` is the entire `context.data` dict as JSON. Individual
`{key}` tokens are only available for keys listed in `reads:`. Referencing an
unlisted key raises a `WorkflowError` at runtime.

### How transitions are resolved

The engine tries three mechanisms in order:

1. **Router (code)** — if `router: fn_name` is set, calls the registered Python
   function with `(output, context)`. Returns a list of successor ids.
2. **Single static edge** — if `next:` has exactly one entry, always goes there.
3. **Model-chosen** — the agent's JSON output must include `next_state: "id"`
   (single successor) or `next_states: ["a", "b"]` (fan-out). The schema must
   declare this field, constrained with `Literal` to the declared transitions.

Fan-out creates independent branches: each successor receives a **deep copy** of
the context at that point. Branches run independently and are collected at their
terminal nodes.

### Skill resolvers

A skill is a `SKILL.md` file injected into the agent as extra system context
before the node runs. Skills can be static paths or dynamic references resolved
at runtime.

```yaml
# static — same file every time
skills:
  - /path/to/SKILL.md

# dynamic — resolved by a registered @skill_resolver
skills:
  - "@lang"
```

A `@skill_resolver` function receives the `@name` token and the live `context`,
and returns the concrete file path. This lets you inject a different skill
depending on data accumulated earlier in the run:

```python
@skill_resolver("lang")
def resolve_lang_skill(ref: str, context: WorkflowContext) -> str:
    lang = context.data.get("language", "")   # set by a prior node's updater
    return f"~/.pi/agent/skills/phraseforge-lang-{lang}/SKILL.md"
```

The decorator key (`"lang"`) must match the part after `@` in the YAML. Resolution
happens at node execution time, so the full `context.data` is available.

### Writing a new workflow from scratch

**Step 1 — Design the graph.** Identify the start node, internal nodes, and
terminal node(s). Decide which nodes fan out. Keep each node's responsibility
narrow.

**Step 2 — Create the package directory and YAML** at `src/workflow_ai/my_workflow/workflow.yaml`:

```yaml
name: my_workflow
start: plan

nodes:
  plan:
    role: You are a planning agent.
    prompt: "Goal: {initial_prompt}"
    kind: model
    output_kind: json
    schema: PlanOut
    updater: store_plan
    retries: 3
    next:
      execute: "proceed to execution"
      skip: "nothing to do"

  execute:
    role: You are a worker agent.
    prompt: "Plan: {plan}"
    kind: model
    output_kind: text
    reads: [plan]
    produces: result
    allowed_tools: [Read, Write]
    next:
      done: "finished"

  skip:
    next:
      done: "go to terminal"

  done:
    terminal: true
```

**Step 3 — Register Python logic.** Create `src/workflow_ai/my_workflow/schemas.py`
for output contracts and `src/workflow_ai/my_workflow/definitions.py` for
verifiers and updaters:

```python
# schemas.py
from typing import Literal
from pydantic import BaseModel
from workflow_ai.registry import schema

@schema("PlanOut")
class PlanOut(BaseModel):
    plan: str
    next_state: Literal["execute", "skip"]   # agent picks one
```

```python
# definitions.py
from workflow_ai.registry import updater

@updater("store_plan")
def store_plan(output, context) -> None:
    context.data["plan"] = output.plan
```

Add `src/workflow_ai/my_workflow/__init__.py` to trigger registration on import:

```python
from . import definitions, schemas  # noqa: F401
```

**Step 4 — Import the package** in `src/workflow_ai/cli.py` alongside the existing ones:

```python
from . import my_workflow  # noqa: F401
```

`workflow-ai list` will pick up `my_workflow` automatically — it globs
`*/workflow.yaml` under the package directory.

**Step 5 — Validate the graph:**

```bash
uv run workflow-ai validate my_workflow
```

This checks for cycles, unreachable nodes, missing schema/verifier/updater
registrations, and malformed terminals.

**Step 6 — Run it:**

```bash
uv run workflow-ai run my_workflow "do something useful"
```

### Quick reference

| Goal | Where to change |
|------|----------------|
| Node prompts / roles | YAML `role:` / `prompt:` |
| Transitions | YAML `next:` |
| Output shape | `@schema` Pydantic class |
| Semantic validation beyond schema | `@verifier` function |
| How output updates context | `@updater` function |
| Pure Python node (no agent) | `@action` function + `kind: action` |
| Routing logic in code | `@router` function + `router: fn_name` |
| Tools available to a node | YAML `allowed_tools:` |
| Dynamic skill injection | `@skill_resolver` + `skills: ["@name"]` |
| Retry count | YAML `retries: N` (default 3) |
