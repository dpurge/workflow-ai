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

Three backends implement the `AgentBackend` protocol. All require `--backend` to be set (no default; set it in the config file to avoid passing it every time).

- **Anthropic SDK** (`--backend anthropic`) — calls the Anthropic API directly
  via the Python SDK. Install with `pip install "workflow-ai[anthropic]"`. Enforces
  JSON output via tool-forcing (`emit_result`). Supports a multi-turn agentic tool
  loop (Read, Write, WebSearch, WebFetch) bounded by `max_turns` (default 10, cap 25).

- **OpenAI SDK** (`--backend openai`) — calls the OpenAI API directly via the
  Python SDK. Install with `pip install "workflow-ai[openai]"`. Enforces JSON output
  via `response_format` (with automatic fallback to `_extract_json` for models that
  don't support structured output, such as local Ollama models). Supports the same
  tool loop as the Anthropic backend. Also supports Azure OpenAI via
  `--azure-endpoint` and `--api-version`.

  Install both SDK backends at once: `pip install "workflow-ai[sdk]"`

- **GitHub Copilot** (`--backend copilot`) — calls the GitHub Copilot chat
  completions API directly via `httpx`. Requires a Copilot subscription; authenticate
  once with `workflow-ai copilot login` (GitHub device flow). Refreshes the session
  token automatically. Supports the same tool loop as the other backends.

### Backend limitations

- `mcp_config` is ignored — no SDK equivalent (a stderr warning is printed).
- `cost_usd` is always `None`.
- `max_turns` defaults to 10 and is hard-capped at 25.
- WebSearch uses DuckDuckGo HTML scraping (no API key required; best-effort).
- Anthropic + Azure: use `--api-base-url` pointing to the Azure endpoint and pass
  the Azure API key via `--api-key`; the Anthropic SDK has no `AzureOpenAI`
  constructor.

### Backend tools

When `allowed_tools` is set in a workflow node, the SDK backends execute tools
directly in Python:

| YAML name | What it does |
|---|---|
| `Read` | Read a file (up to 1 MB) |
| `Write` | Write a file within the current working directory (relative paths only) |
| `WebSearch` | DuckDuckGo search — returns titles, URLs, and snippets |
| `WebFetch` | HTTP GET a URL and return its text content (up to 500 KB) |

### Script tools (`research/tools/`)

Standalone PEP 723 scripts that workflows can invoke via `uv run --script`. No
manual installs — `uv` resolves each script's inline dependencies on first run
and caches them. All scripts emit JSON on stdout and exit 0 on failure (with a
note on stderr) so callers degrade gracefully.

| Script | Deps | Output shape | Key flags |
|---|---|---|---|
| `web-search.py` | stdlib | `[{url, title, snippet}]` | `--limit N` (default 5) |
| `wikipedia-search.py` | stdlib | `[{title, url, summary, lang}]` | `--limit N`, `--lang LANG` (default `en`) |
| `arxiv-search.py` | stdlib | `[{id, title, authors, abstract, published, pdf_url}]` | `--limit N`, `--category CAT` (e.g. `cs.LG`) |
| `rag-index.py` | fastembed, faiss-cpu, pyyaml, numpy | writes `<knowledge-dir>/.index/` | `--knowledge-dir PATH`, `--model NAME` |
| `rag-query.py` | fastembed, faiss-cpu, numpy | `[{id, file, headers, frontmatter, text, score}]` | `--top-k N`, `--min-score F`, `--tag TAG` |

`rag-index.py` builds a FAISS cosine-similarity index over Markdown files split
at H2 boundaries. `rag-query.py` queries it — exit code 1 when nothing scores
above `--min-score`, making it easy to chain a fallback:

```bash
# build once
uv run --script src/workflow_ai/research/tools/rag-index.py \
  --knowledge-dir ~/my-docs

# query — fall back to web search on miss
uv run --script src/workflow_ai/research/tools/rag-query.py "transformer attention" \
  || uv run --script src/workflow_ai/research/tools/web-search.py "transformer attention"
```

Knowledge directory defaults to `$ASSISTANT_KNOWLEDGE_DIR` or
`~/.assistant/knowledge/`. RAG model defaults to
`$ASSISTANT_RAG_MODEL` or `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`.

#### Making tools available to a workflow node

Tools are exposed to a node's model via a **skill file** — a Markdown document
injected into the system prompt at runtime. The skill describes what tools exist
and how to call them; the model produces `uv run --script` shell commands that
the backend (or a `kind: action` node) then executes.

**Step 1 — write a skill file** (e.g. `skills/search-tools.md`):

````markdown
## Available tools

Run these with `uv run --script <path> <args>`. Each prints JSON to stdout.

### web-search.py
Search the web via DuckDuckGo. Returns [{url, title, snippet}].
```
uv run --script tools/web-search.py "<query>" [--limit N]
```

### wikipedia-search.py
Search Wikipedia. Returns [{title, url, summary, lang}].
```
uv run --script tools/wikipedia-search.py "<query>" [--limit N] [--lang LANG]
```

### arxiv-search.py
Search arXiv papers. Returns [{id, title, authors, abstract, pdf_url}].
```
uv run --script tools/arxiv-search.py "<query>" [--limit N] [--category CAT]
```
````

**Step 2 — reference the skill in the node** and allow the `Bash` or `Write`
backend tool so the model can actually run the commands:

```yaml
nodes:
  gather:
    role: Research assistant with access to web, Wikipedia, and arXiv search tools.
    prompt: "Research this topic: {initial_prompt}"
    output_kind: text
    produces: findings
    skills:
      - ./skills/search-tools.md
    allowed_tools: [Bash, Write]
    next:
      synthesize: "Synthesize findings."
```

The `Bash` backend tool lets the model execute arbitrary shell commands (including
`uv run --script ...`). `Write` lets it persist intermediate results to disk.
Both are supported by the Anthropic, OpenAI, and Copilot backends.

**Step 3 — for RAG**, run `rag-index.py` once before the workflow, then add
`rag-query.py` to the skill:

````markdown
### rag-query.py
Query the local knowledge base. Returns [{file, headers, text, score}].
Exit code 1 on miss — chain a fallback if needed.
```
uv run --script tools/rag-query.py "<query>" [--top-k N] [--knowledge-dir PATH]
```
````

### Corporate authentication

```bash
# Custom API gateway / proxy
uv run workflow-ai run research --backend anthropic \
  --model claude-sonnet-4-6 \
  --api-base-url https://corp-gateway.example.com \
  --api-key "$CORP_API_KEY" \
  --default-header "X-Corp-Auth:Bearer $CORP_TOKEN" \
  --prompt "Research X"

# Azure OpenAI
uv run workflow-ai run research --backend openai \
  --azure-endpoint https://myresource.openai.azure.com \
  --api-version 2024-10-21 \
  --model my-deployment-name \
  --api-key "$AZURE_API_KEY" \
  --prompt "Research X"

# Local Ollama (OpenAI-compatible /v1 endpoint)
uv run workflow-ai run research --backend openai \
  --api-base-url http://localhost:11434/v1 \
  --api-key ollama \
  --model gemma4:latest \
  --prompt "Research X"
```

Persistent defaults can be set in `~/.config/workflow-ai/config.yaml`:

```yaml
backend: openai
model: gemma4:latest
api_base_url: http://localhost:11434/v1
api_key: ollama
default_headers:
  X-Corp-Auth: "Bearer mytoken"
azure_endpoint: null
api_version: null
```

### Not supported

- **opencode** (`sst/opencode`) — no headless mode. The CLI launches a full
  interactive TUI (Ink/React); there is no `--print`, `--quiet`, positional
  prompt, or stdin-prompt flag. Running it without a TTY hangs or crashes. An
  opencode backend will be straightforward to add once the project ships a
  non-interactive mode.

## Install

```bash
uv sync                              # core install
pip install "workflow-ai[anthropic]" # Anthropic backend
pip install "workflow-ai[openai]"    # OpenAI / Azure backend
pip install "workflow-ai[sdk]"       # both SDK backends

uv run workflow-ai list          # list built-in workflows
uv run workflow-ai validate <wf> # DAG-validate without running
```

### Development install (command on PATH)

To use `workflow-ai` directly without the `uv run` prefix, and have every code
edit take effect immediately without reinstalling:

```bash
uv tool install --editable .
```

This registers `workflow-ai` as a global uv tool backed by an editable install of
the current checkout. Edits to source files are reflected instantly. To include
optional SDK extras:

```bash
uv tool install --editable ".[sdk]"
```

To uninstall: `uv tool uninstall workflow-ai`

## Docker / CI

The `Dockerfile` builds a self-contained image with `workflow-ai` pre-installed.
Pass everything through environment variables; mount `/runs` to retrieve results.

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
| `WORKFLOW_BACKEND` | **yes** | Backend: `anthropic` \| `openai` \| `copilot` |
| `WORKFLOW_MODEL` | no | Model id (e.g. `claude-sonnet-4-6`, `gemma3:9b`) |
| `WORKFLOW_API_BASE_URL` | no | API base URL — redirect to Ollama, OpenRouter, etc. |
| `WORKFLOW_API_KEY` | no | API key for the target endpoint |
| `WORKFLOW_OUT` | no | Output directory inside the container (default `/runs`) |
| `WORKFLOW_SOURCE` | phraseforge | Source URL or file path (`--source`) |
| `WORKFLOW_LEVEL` | phraseforge | CEFR level a1..c2 (`--level`) |
| `WORKFLOW_TRANSLATION_LANG` | phraseforge | Gloss language (`--translation-lang`) |
| `WORKFLOW_CWD` | phraseforge | Base dir for lesson output (`--cwd`) |
| `ANTHROPIC_API_KEY` | anthropic | API key for Anthropic backend (not needed with Ollama/proxy) |
| `OPENAI_API_KEY` | openai | API key for OpenAI backend (not needed with Ollama/proxy) |
| `SSH_PRIVATE_KEY` | ssh | PEM private key for GitHub SSH auth |
| `SSH_KNOWN_HOSTS` | ssh | Known-hosts content; omit to auto-scan `github.com` |

Verbose output is always enabled — the workflow prints structured node events to
stdout so CI logs capture the full run.

### Local Ollama

Use `--backend openai` (Ollama exposes an OpenAI-compatible `/v1` API). Pass `ollama`
as the API key (Ollama accepts any non-empty string).

```bash
docker run --rm \
  -e WORKFLOW=research \
  -e WORKFLOW_PROMPT="Research lunar habitats" \
  -e WORKFLOW_BACKEND=openai \
  -e WORKFLOW_API_BASE_URL=http://host.docker.internal:11434/v1 \
  -e WORKFLOW_API_KEY=ollama \
  -e WORKFLOW_MODEL=gemma4:latest \
  -v "$(pwd)/runs:/runs" \
  workflow-ai
```

### OpenRouter

```bash
docker run --rm \
  -e WORKFLOW=research \
  -e WORKFLOW_PROMPT="Research X" \
  -e WORKFLOW_BACKEND=openai \
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

```bash
uv run workflow-ai run research \
  --backend anthropic \
  --model claude-sonnet-4-6 \
  --prompt "Research the tradeoffs of X" \
  --out runs/research
```

The start node classifies the topic and fans out to web + file gathering, then
synthesizes a summary.

### `phraseforge` — web page → language-lesson MDX

Writes the lesson to `docs/<lang>/<level>/<YYYY-MM-DD>-<seq>.mdx` under `--cwd`.

```bash
uv run workflow-ai run phraseforge --backend openai \
  --model gpt-5.4 \
  --source https://de.wikipedia.org/wiki/Kaffee \
  --level a2 \
  --out runs/phraseforge
```

**Local Ollama** (OpenAI-compatible `/v1` API):

```bash
uv run workflow-ai run phraseforge --backend openai \
  --api-base-url http://localhost:11434/v1 \
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
backend: anthropic       # anthropic | openai | copilot
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
| `--backend anthropic\|openai\|copilot` | agent backend (required; or set in config file) |
| `--api-base-url URL` | redirect to Ollama, OpenRouter, etc. (e.g. `http://localhost:11434/v1`) |
| `--api-key KEY` | API key for the target endpoint; use `ollama` for local Ollama |
| `--model ID` | model id to pass to the backend (omit to use the backend's default) |
| `--default-header KEY:VALUE` | extra HTTP header (repeatable); e.g. `--default-header Authorization:Bearer tok` |
| `--azure-endpoint URL` | Azure OpenAI resource endpoint (activates `AzureOpenAI` constructor) |
| `--api-version VER` | Azure OpenAI API version (e.g. `2024-10-21`) |
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
| `src/workflow_ai/backends/anthropic_sdk.py` | Anthropic SDK backend |
| `src/workflow_ai/backends/openai_sdk.py` | OpenAI / Azure SDK backend |
| `src/workflow_ai/backends/copilot.py` | GitHub Copilot backend (httpx) |
| `src/workflow_ai/research/workflow.yaml` | research workflow graph |
| `src/workflow_ai/research/schemas.py` | research output schemas |
| `src/workflow_ai/research/definitions.py` | research verifiers + updaters |
| `src/workflow_ai/research/skills/` | skill files injected into research nodes |
| `src/workflow_ai/research/tools/` | PEP 723 scripts: web-search, wikipedia-search, arxiv-search, rag-index, rag-query |
| `src/workflow_ai/phraseforge/workflow.yaml` | phraseforge workflow graph |
| `src/workflow_ai/phraseforge/schemas.py` | phraseforge output schemas |
| `src/workflow_ai/phraseforge/definitions.py` | phraseforge actions / router / verifiers |
| `src/workflow_ai/phraseforge/skills/` | skill files injected into phraseforge nodes |
| `Dockerfile` | self-contained image with workflow-ai |
| `entrypoint.sh` | Docker entrypoint: SSH setup, env-var → CLI arg mapping |

## Test

```bash
uv run pytest
```

Tests mock the backend via the `AgentBackend` protocol, so no live agent is
needed.

## Adding a backend

Implement the `AgentBackend` protocol (`backends/base.py`) — a single
`run(invocation) -> AgentResult`. See `backends/anthropic_sdk.py` or
`backends/copilot.py` for reference implementations.

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
