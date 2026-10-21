---
description: >-
  Set up a Vayl checkout, run the offline tests, lint and type checks, and the
  Postgres and Neo4j integration suites the way CI does.
icon: laptop-code
---

# Local development

This page gets you from a fresh clone to the same checks CI runs. The unit tests are offline and deterministic, so the everyday loop needs no API key, no model, and no database server.

## Requirements

* Python 3.10, 3.11 or 3.12 (CI tests all three)
* Git
* Docker, only for the Postgres and Neo4j integration suites
* Node.js 18 or later, only for the TypeScript client (CI uses Node 20)

## Set up a checkout

{% stepper %}
{% step %}
### Clone and create a virtual environment

```bash
git clone https://github.com/ac12644/vayl && cd vayl
python -m venv .venv && source .venv/bin/activate
```

Keep the environment activated when you run tests. `tests/test_client.py` starts the `vayl-mcp` executable and skips itself if it isn't on your `PATH`.
{% endstep %}

{% step %}
### Install in editable mode with the dev extras

```bash
pip install -e ".[dev,server,postgres]"
```

| Extra | Adds | Needed for |
| --- | --- | --- |
| `dev` | pytest, ruff, mypy, `langchain-core`, `openai-agents` | Tests, lint, type check, and the LangGraph and OpenAI Agents adapter tests |
| `server` | uvicorn | Running `vayl-server` locally |
| `postgres` | `psycopg[binary]` | The Postgres backend and its integration suite |
| `graph` | neo4j driver | The Neo4j projection and its integration suite |
| `crewai` | crewai | The CrewAI adapter tests (skipped without it; CI doesn't install it) |

CI's unit job installs only `.[dev]`. The integration job installs `.[dev,postgres,graph]`.
{% endstep %}

{% step %}
### Run the checks

```bash
pytest          # offline unit tests
ruff check .    # lint
mypy            # type check
```

These are the three checks CI runs on every Python version. All three should pass before you open a pull request.
{% endstep %}
{% endstepper %}

## Tests

`pytest` reads its settings from `pyproject.toml`: tests live in `tests/`, and output is quiet (`-q`). Run a single file or test the usual way:

```bash
pytest tests/test_tools.py
pytest tests/test_tools.py -k supersedes
```

### The offline guard

`tests/conftest.py` refuses every connection to ports 11434 (Ollama), 443 and 80 and fails the test that tried, even if the code under test caught the error. A test that passes on your machine only because Ollama happens to be running will fail. Stub the model seams instead:

```python
from vayl.memory import llm_memory
from vayl.storage import store as store_mod

def test_something(monkeypatch):
    monkeypatch.setattr(llm_memory, "llm_extract_classify", fake_extract)
    monkeypatch.setattr(llm_memory, "_qa", lambda context, question: context)
    monkeypatch.setattr("vayl.memory.llm_client._embed", lambda texts: [[0.1, 0.2] for _ in texts])
    monkeypatch.setattr(store_mod, "_embed", lambda texts: [[0.1, 0.2] for _ in texts])
```

`tests/test_tools.py` shows the full pattern: a scripted `fake_extract` that returns the facts a model would extract for each sentence, driving the real MCP tool functions end to end.

Checks that need a real model (reconciliation quality, LoCoMo, scale) live in `benchmarks/` and are run by hand. They are not part of `pytest`.

### Integration suites (Postgres and Neo4j)

`tests/test_postgres.py` and `tests/test_graph_store.py` run against real servers and skip when none is reachable. CI runs them in a separate `integration` job and fails the job if anything is skipped. To run them locally, start the same services CI uses:

```bash
docker run -d --name vayl-test-postgres \
  -e POSTGRES_USER=vayl -e POSTGRES_PASSWORD=vayl -e POSTGRES_DB=vayl \
  -p 5432:5432 postgres:17

docker run -d --name vayl-test-neo4j \
  -e NEO4J_AUTH=neo4j/ci-only-throwaway \
  -p 7687:7687 neo4j:5
```

Neo4j takes a little while to accept connections. Then install the extras, set the variables CI sets, and run the two suites:

```bash
pip install -e ".[dev,postgres,graph]"

export VAYL_TEST_DATABASE_URL=postgresql://vayl:vayl@localhost:5432/vayl
export NEO4J_URI=bolt://localhost:7687
export NEO4J_PASSWORD=ci-only-throwaway

pytest tests/test_postgres.py tests/test_graph_store.py -o addopts="" -q -rs
```

`-rs` prints the reason for any skip. A skip here means the suite didn't reach its database, so treat it as a failure, as CI does. Remove the containers afterwards with `docker rm -f vayl-test-postgres vayl-test-neo4j`.

## Lint and type check

| Tool | Config (`pyproject.toml`) | Notes |
| --- | --- | --- |
| ruff | `[tool.ruff]`: line length 120, rules `E`, `F`, `W`, `I`, `B`, `UP` | `benchmarks/` and `research/` are excluded. Several purely stylistic rules (`E501`, `E701`, `E702`, `E401`, `E741`) are ignored on purpose: the codebase uses compact one-liners. |
| mypy | `[tool.mypy]`: checks `src/vayl` for Python 3.10, with `check_untyped_defs` | `ignore_missing_imports` is on, because optional extras aren't installed everywhere. Run plain `mypy`; the file list comes from the config. |

`ruff check . --fix` fixes import order and other safe issues.

## TypeScript client

The TypeScript client (`@vayl.dev/client`) lives in `clients/typescript/`. It has no test suite; CI type-checks and builds it when files under `clients/typescript/` change.

```bash
cd clients/typescript
npm install
npm run typecheck
npm run build        # compiles src/ to dist/
```

The TypeScript CI workflow is not a required check for Python pull requests.

## Run Vayl from your checkout

With the editable install, `vayl-mcp`, `vayl-server` and `vayl-demo` run your working copy:

```bash
vayl-demo            # the reconciliation demo; needs no key or network
vayl-mcp --version
vayl-server --help
```

Point an MCP client at the `vayl-mcp` in `.venv/bin/` to try changes end to end. Use a throwaway `VAYL_DB` so you don't touch real memory.

## Debugging

* **Log level.** `VAYL_LOG_LEVEL=DEBUG` logs full error detail, including tracebacks. Error text can contain memory content, so don't enable it on a server holding real data, and don't paste DEBUG logs into public issues. Logs go to stderr; on stdio, stdout belongs to the MCP protocol.
* **Error refs.** When a tool fails, it returns `Vayl couldn't complete that (ref 1a2b3c4d)...` and logs the same ref at `ERROR` with the exception type. Search the log for the ref.
* **Request IDs.** `vayl-server` gives each HTTP request an ID, returns it in the `X-Request-ID` response header, and adds it to every log line written while serving it. Send your own `X-Request-ID` to correlate with a proxy. `VAYL_LOG_FORMAT=json` makes the logs machine-readable.
* **Health.** The `health` tool reports database, embedder, model and graph status in one call.

## Repository structure

```
vayl/
├── src/vayl/
│   ├── api/            MCP tool definitions (mcp_server.py) and the HTTP server (server.py)
│   ├── auth/           API-key principals, roles and capabilities (auth.py); OIDC SSO (sso.py)
│   ├── memory/         Extraction, reconciliation, retrieval, slot schemas, the LLM client
│   ├── storage/        SQLite/Postgres database layer, the store, migrations, Neo4j projection
│   ├── security/       Encryption at rest, the signed audit chain, KMS, safety gates
│   ├── licensing/      Edition licenses, receipts, and the vendor-side license minting tool
│   ├── presets/        Built-in slot-schema presets (coding, support, clinical, ...) as JSON
│   ├── integrations/   Python framework adapters: LangGraph, OpenAI Agents SDK, CrewAI
│   ├── telemetry/      In-process metrics behind the stats tool
│   ├── cli.py          Entry points for vayl-mcp and vayl-server (--help, --version)
│   ├── client.py       The synchronous Python client (from vayl import Vayl)
│   ├── config.py       Strict parsing of environment settings
│   └── demo.py         The vayl-demo command
├── tests/              Offline unit tests plus the Postgres and Neo4j integration suites
├── benchmarks/         Model-dependent evaluations, run by hand; results in benchmarks/results/
├── examples/           Runnable examples, such as a coding assistant that remembers decisions
├── clients/typescript/ The @vayl.dev/client package, with Vercel AI SDK and Mastra adapters
├── docs/               The docs site, synced with GitBook: one folder per section (documentation/, home/,
│                       api-reference/, changelog/, help-center/) plus gitbook-docs.yaml
├── openapi/            vayl-server.yaml, the OpenAPI spec behind the API Reference
└── .github/workflows/  CI (unit + integration), TS SDK CI, CodeQL, Scorecard, docs-sync, release, npm publish
```

`docs/` is kept in sync with GitBook through the `docs-sync` branch. Edit docs in GitBook or open a pull request; see [Contributing](contributing.md#contributing-to-the-docs).

## Next steps

<table data-view="cards"><thead><tr><th></th><th></th><th data-hidden data-card-target data-type="content-ref"></th></tr></thead><tbody><tr><td><h4><i class="fa-code-pull-request" style="color:$primary;">:code-pull-request:</i> Contributing</h4></td><td>Branches, required checks, conventions, and how to send a pull request.</td><td><a href="contributing.md">contributing.md</a></td></tr><tr><td><h4><i class="fa-diagram-project" style="color:$primary;">:diagram-project:</i> How Vayl works</h4></td><td>The data flow from a sentence to a reconciled fact.</td><td><a href="../how-vayl-works.md">how-vayl-works.md</a></td></tr><tr><td><h4><i class="fa-sliders" style="color:$primary;">:sliders:</i> Configuration</h4></td><td>Every environment variable and its default.</td><td><a href="../reference/configuration.md">configuration.md</a></td></tr></tbody></table>
