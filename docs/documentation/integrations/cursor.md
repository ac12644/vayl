---
description: >-
  Give Cursor reconciling memory: configure the MCP server (OpenAI or keyless
  local Ollama) and a project rule so the agent keeps your facts current.
icon: i-cursor
---

# Cursor

Vayl gives Cursor a memory where new facts replace stale ones, explicit retractions are remembered as removals, and history stays queryable. Cursor decides on its own when to call MCP tools, so this setup has two parts: the MCP server, and a project rule that tells the agent when to use it.

{% stepper %}
{% step %}
### Install Vayl

```bash
pip install vayl-mcp
vayl-mcp --version
```

If you installed it in a virtual environment, Cursor may not find `vayl-mcp` on its `PATH`. Use the absolute path from `which vayl-mcp` as the `command` below.
{% endstep %}

{% step %}
### Configure the MCP server

Put the configuration in one of:

* `~/.cursor/mcp.json` to make Vayl available in every project
* `<project>/.cursor/mcp.json` to enable it for one project

Create the directory if it doesn't exist, then pick a provider.

{% tabs %}
{% tab title="OpenAI" %}
```json
{
  "mcpServers": {
    "vayl": {
      "command": "vayl-mcp",
      "env": {
        "LLM_PROVIDER": "openai",
        "OPENAI_API_KEY": "sk-...",
        "VAYL_DB": "/absolute/path/to/project/.vayl/memory.db",
        "VAYL_SLOT_SCHEMA": "preset:coding"
      }
    }
  }
}
```

The key alone is enough. Since 0.6.0, Vayl sends both chat (`gpt-5-mini`) and embeddings (`text-embedding-3-small`) to OpenAI when `OPENAI_API_KEY` is set and no base URL is. Set `OPENAI_MODEL` or `EMBED_MODEL` only to change those defaults.
{% endtab %}

{% tab title="Local Ollama" %}
Pull the chat and embedding models before starting Cursor:

```bash
ollama pull qwen2.5:3b
ollama pull nomic-embed-text
```

```json
{
  "mcpServers": {
    "vayl": {
      "command": "vayl-mcp",
      "env": {
        "LLM_PROVIDER": "openai",
        "OPENAI_BASE_URL": "http://localhost:11434/v1",
        "OPENAI_MODEL": "qwen2.5:3b",
        "VAYL_DB": "/absolute/path/to/project/.vayl/memory.db",
        "VAYL_SLOT_SCHEMA": "preset:coding"
      }
    }
  }
}
```

Ollama needs no API key. Embeddings follow `OPENAI_BASE_URL` and default to `nomic-embed-text`. To use other models, see [Local models with Ollama](../guides/local-models-with-ollama.md).
{% endtab %}
{% endtabs %}

`LLM_PROVIDER=openai` pins the provider. Without it, an `ANTHROPIC_API_KEY` or `GROQ_API_KEY` that reaches the process would take priority.
{% endstep %}

{% step %}
### Restart Cursor and check the server

Restart Cursor after changing `mcp.json`. In Cursor's MCP settings, `vayl` should be enabled and list its tools. Ask the agent to "run the vayl health tool": every line should read `ok` (or `graph: disabled`). A `FAIL (...)` line names the part to fix; see [Troubleshooting](../reference/troubleshooting.md).
{% endstep %}

{% step %}
### Tell Cursor when to use memory

Vayl sends MCP server instructions that say when to call `remember` and `recall`. Claude Code passes them to its model; a project rule makes the same behaviour explicit in Cursor.

{% hint style="info" %}
**Needs verification:** whether Cursor shows MCP server instructions to its model. The rule below works either way.
{% endhint %}

Create `<project>/.cursor/rules/vayl.mdc`:

```markdown
---
description: Use Vayl to keep project memory current
alwaysApply: true
---

- Call `recall` before answering when stored project context could affect the answer.
- Call `remember` when the user states a durable fact, changes a fact, or retracts one.
- Prefer recalled current facts over stale assumptions, and ask when a conflict remains unclear.
```

Installing the server alone does not make Cursor recall or store anything. The rule is what makes it happen.
{% endstep %}
{% endstepper %}

## Keep projects isolated

Memory follows `VAYL_DB`. Two projects that point at the same database share memory, even if each has its own `.cursor/mcp.json`. Give each project its own absolute path under a project-specific `.vayl/` directory, and add `.vayl/` to `.gitignore` so personal memory isn't committed.

## Use a shared team server

If your team runs `vayl-server`, point Cursor at it instead of starting a local process:

```json
{
  "mcpServers": {
    "vayl": {
      "url": "https://your-host/mcp",
      "headers": { "Authorization": "Bearer vayl_sk_..." }
    }
  }
}
```

The server uses its own model configuration, so no model key is needed here. See [Deploying vayl-server](../guides/deploying-vayl-server.md).

## Model quality and the coding preset

Reconciliation asks the model to tell additions, corrections and retractions apart. Capable models do this more reliably. Small local models can mislabel subtle changes, so review important memories, or use a stronger model when correctness matters.

{% hint style="success" %}
`VAYL_SLOT_SCHEMA=preset:coding` (in both configs above) declares the usual facts of a codebase: language, framework, state library, database, test runner, conventions and more. For a declared slot, a switch retires the old value deterministically, even on a small local model. The full slot list is on the [Claude Code](claude-code.md) page.
{% endhint %}

## Next steps

<table data-view="cards"><thead><tr><th></th><th></th><th data-hidden data-card-target data-type="content-ref"></th></tr></thead><tbody><tr><td><h4><i class="fa-brain" style="color:$primary;">:brain:</i> Your first memory</h4></td><td>Store, change and retract a fact, and read the history.</td><td><a href="../getting-started/your-first-memory.md">your-first-memory.md</a></td></tr><tr><td><h4><i class="fa-house-laptop" style="color:$primary;">:house-laptop:</i> Local models with Ollama</h4></td><td>Run Vayl with no data leaving your machine.</td><td><a href="../guides/local-models-with-ollama.md">local-models-with-ollama.md</a></td></tr><tr><td><h4><i class="fa-database" style="color:$primary;">:database:</i> Memory tools</h4></td><td>Every tool the agent calls: remember, recall, history, and more.</td><td><a href="../mcp-tools/memory.md">memory.md</a></td></tr></tbody></table>
