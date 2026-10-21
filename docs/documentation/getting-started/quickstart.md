---
description: >-
  Install Vayl, pick a model, connect your MCP client, and store your first
  reconciled fact.
icon: bolt
---

# Quickstart

**Install Vayl, connect it to your MCP client, and watch a fact get superseded.** Your agent calls Vayl's MCP tools (`remember`, `recall`, `forget`, …); Vayl extracts facts from each message, reconciles them against what it already holds, and keeps the history.

## What you need

* **Python 3.10 or newer.**
* **A model.** One chat model extracts facts and writes answers, and an embedder powers retrieval. Use OpenAI, a local model through Ollama, or any OpenAI-compatible endpoint.
* **An MCP client**: Claude Desktop, Cursor, Claude Code, or your own agent.

{% hint style="info" %}
Vayl's only outbound calls are to the LLM and the embedder you configure (plus Vault, your OIDC provider, or Neo4j if you enable them). Reconciliation, storage, and history run locally.
{% endhint %}

{% stepper %}
{% step %}
### Install

```bash
pip install vayl-mcp
vayl-mcp --version
```

```
vayl-mcp 0.8.0
```

Optional extras:

| Extra | Adds |
| --- | --- |
| `vayl-mcp[server]` | `vayl-server`, the authenticated HTTP transport for a team |
| `vayl-mcp[postgres]` | The Postgres backend |
| `vayl-mcp[graph]` | The optional Neo4j graph projection |
| `vayl-mcp[sso]` | OIDC single sign-on for `vayl-server` |
{% endstep %}

{% step %}
### See reconciliation work, offline

```bash
vayl-demo
```

`vayl-demo` runs the real engine on pre-extracted facts. It needs no keys and no model, and writes no files. Output, trimmed:

```
  The conversation:
    you: We use Redux for state management.
    you: Actually, we moved off Redux to Zustand.
    you: We use Sentry for error monitoring.
    you: We dropped Sentry.

  What's true now  (the active set — what an agent gets back):
    state         : Zustand

  The history is still there  (nothing is lost — it just left the hot path):
    state = Redux        [SUPERSEDED]
    state = Zustand      [ACTIVE]
    monitoring = Sentry       [SUPERSEDED]
    monitoring = (retracted: Sentry) [HISTORICAL]
```

The switch superseded Redux, and the removal retracted Sentry. Both are still in history.
{% endstep %}

{% step %}
### Choose a model

{% tabs %}
{% tab title="OpenAI" %}
Set `OPENAI_API_KEY`. With only the key set, Vayl uses `gpt-5-mini` for chat and `text-embedding-3-small` for embeddings, both on OpenAI.

```bash
export OPENAI_API_KEY=sk-...
```

Override the chat model with `OPENAI_MODEL`. To send embeddings elsewhere, set `EMBED_BASE_URL` (and `EMBED_MODEL`).
{% endtab %}

{% tab title="Local model (Ollama)" %}
With no key and no base URL set, Vayl talks to Ollama at `http://localhost:11434/v1`, using `qwen2.5:3b` for chat and `nomic-embed-text` for embeddings. Pull both:

```bash
ollama pull qwen2.5:3b
ollama pull nomic-embed-text
```

Nothing leaves your machine. A 3B model names facts less consistently than a larger one; use a ~7B+ model (`OPENAI_MODEL`) or a built-in slot preset (`VAYL_SLOT_SCHEMA=preset:coding`) for more reliable reconciliation. See [Local models with Ollama](../guides/local-models-with-ollama.md).
{% endtab %}
{% endtabs %}

Any other OpenAI-compatible endpoint works through `OPENAI_BASE_URL` and `OPENAI_MODEL`. Every variable is in [Configuration](../reference/configuration.md).
{% endstep %}

{% step %}
### Connect your MCP client

Pick a place for the database and create its folder. Vayl does not create missing directories, and without `VAYL_DB` it writes `vayl.db` into whatever directory the client starts it in.

```bash
mkdir -p ~/.vayl
```

{% tabs %}
{% tab title="Claude Code" %}
```bash
claude mcp add vayl -e LLM_PROVIDER=openai -e OPENAI_API_KEY=sk-... -e VAYL_DB=$HOME/.vayl/vayl.db -- vayl-mcp
```

`LLM_PROVIDER=openai` matters here: if `ANTHROPIC_API_KEY` is set in your environment, Vayl otherwise picks Anthropic for its own model calls. Drop `-e OPENAI_API_KEY=...` for a local Ollama model. Check it with `claude mcp list`. More in [Claude Code](../integrations/claude-code.md).
{% endtab %}

{% tab title="Claude Desktop / Cursor" %}
Add Vayl to the client's MCP config: `claude_desktop_config.json` for Claude Desktop (Settings → Developer → Edit Config), `~/.cursor/mcp.json` for Cursor.

```json
{
  "mcpServers": {
    "vayl": {
      "command": "vayl-mcp",
      "env": {
        "OPENAI_API_KEY": "sk-...",
        "VAYL_DB": "/Users/you/.vayl/vayl.db"
      }
    }
  }
}
```

Leave out `OPENAI_API_KEY` for a local Ollama model. Use absolute paths: GUI clients do not expand `~` or `$HOME`.

{% hint style="warning" %}
If you installed Vayl in a virtualenv, GUI clients usually do not see that venv's `PATH`. Set `command` to the absolute path from `which vayl-mcp`, for example `/Users/you/projects/.venv/bin/vayl-mcp`.
{% endhint %}

More in [Cursor](../integrations/cursor.md).
{% endtab %}
{% endtabs %}

Restart the client so it starts the server. `vayl-mcp` is launched by the client over stdio; you do not run it by hand. (`vayl-mcp --help` only prints usage.)

The database file gets two companions, `vayl.db.key` (at-rest encryption key) and `vayl.db.sign.key` (audit signing key). Back them up with the database.
{% endstep %}

{% step %}
### Store your first fact

Tell your agent:

> **You:** Remember that we use Redux for state management.
>
> **You:** Actually, we switched to Zustand.
>
> **You:** What do we use for state?

Behind these turns, the agent calls `remember` twice and `recall` once. The `remember` returns are deterministic:

```
Stored: [ADD] state = Redux
Stored: [SUPERSEDE] state = Zustand
```

The model chose the subject name `state`; yours may differ. A message with nothing durable in it returns `No durable fact found (looked like chatter or a question).`

`recall` returns an answer written by the model from the active facts, for example:

```
We use Zustand for state management.
```

The wording varies. What does not vary is that Redux is no longer an active fact, so it cannot be returned as current. Ask _"what did we use before?"_ and the agent can call `recall` with `include_history=True`, or `history`, to reach Redux.
{% endstep %}

{% step %}
### Check that it's healthy

Ask your agent to run the `health` tool. It checks the database, embedder, LLM, and graph with one small call each, and returns one line per check:

```
config: LLM_PROVIDER=(unset), model=(default)
license: community
encryption: on · signing: on
db: ok
embedder: ok
llm: ok
graph: disabled
```

A failing dependency shows as `FAIL` with only the error type, such as `embedder: FAIL (NewConnectionError)` when nothing is listening at the embedder URL (the exact type depends on your HTTP stack; details go to the server log). `model=(default)` means `OPENAI_MODEL` is unset and the default above applies.
{% endstep %}
{% endstepper %}

## Local or team

| | `vayl-mcp` (local) | `vayl-server` (team) |
| --- | --- | --- |
| Transport | stdio, started by your MCP client | Streamable HTTP at `/mcp` (default `127.0.0.1:8080`) |
| Auth | Runs as the local admin unless `VAYL_AUTH_REQUIRED` is on | Every request needs an API key (`Bearer vayl_sk_...`) or an OIDC token |
| Storage | SQLite file at `VAYL_DB` | SQLite, or Postgres via `VAYL_DATABASE_URL` |

To run a shared server, see [Deploying vayl-server](../guides/deploying-vayl-server.md).

## Next steps

<table data-view="cards"><thead><tr><th></th><th></th><th data-hidden data-card-target data-type="content-ref"></th></tr></thead><tbody><tr><td><h4><i class="fa-compass" style="color:$primary;">:compass:</i> Your first memory</h4></td><td>Store, supersede, retract, and read history, with the tool calls behind each.</td><td><a href="your-first-memory.md">your-first-memory.md</a></td></tr><tr><td><h4><i class="fa-code" style="color:$primary;">:code:</i> Calling Vayl from code</h4></td><td>The Python and TypeScript clients, and raw MCP.</td><td><a href="calling-vayl-from-code.md">calling-vayl-from-code.md</a></td></tr><tr><td><h4><i class="fa-book" style="color:$primary;">:book:</i> Reconciliation</h4></td><td>Supersede, retract, flag, and the same-slot invariant.</td><td><a href="../core-concepts/core-concepts.md">core-concepts.md</a></td></tr></tbody></table>
