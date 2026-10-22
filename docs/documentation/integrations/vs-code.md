---
description: >-
  Add Vayl to GitHub Copilot's agent mode in VS Code, with the API key kept out of the config file.
icon: code
---

# VS Code

VS Code's Copilot agent mode can use Vayl as an MCP server. VS Code keeps MCP servers in an `mcp.json` file whose top-level key is `servers`.

{% stepper %}
{% step %}
### Install Vayl

```bash
pip install vayl-mcp
which vayl-mcp
```

If you installed it in a virtual environment, use the absolute path that `which` prints as the `command` below. The client may not share your shell's `PATH`.
{% endstep %}

{% step %}
### Add the server

Run **MCP: Open User Configuration** from the Command Palette (or create `.vscode/mcp.json` in a project) and add:

```json
{
  "inputs": [
    {"type": "promptString", "id": "openai-key", "description": "OpenAI API key for Vayl", "password": true}
  ],
  "servers": {
    "vayl": {
      "type": "stdio",
      "command": "vayl-mcp",
      "env": {
        "LLM_PROVIDER": "openai",
        "OPENAI_API_KEY": "${input:openai-key}",
        "VAYL_DB": "/absolute/path/to/vayl.db"
      }
    }
  }
}
```

VS Code asks for the key the first time the server starts and stores it, so it never sits in the file. From a terminal, `code --add-mcp` adds the same server entry, but not the `inputs` block, so add that by hand or put the key in `env`:

```bash
code --add-mcp '{"name":"vayl","type":"stdio","command":"vayl-mcp","env":{"LLM_PROVIDER":"openai","OPENAI_API_KEY":"sk-...","VAYL_DB":"/absolute/path/to/vayl.db"}}'
```
{% endstep %}

{% step %}
### Start it

Open the Chat view in **Agent** mode. VS Code lists `vayl` under the tools button; start it from there or from **MCP: List Servers**.
{% endstep %}

{% step %}
### Check the server

Ask the agent to "run the vayl health tool". Every line should read `ok`, or `graph: disabled`. A `FAIL (...)` line names the part to fix, for example `llm: FAIL (HTTP 401: Incorrect API key provided …)`; see [Troubleshooting](../reference/troubleshooting.md).
{% endstep %}
{% endstepper %}

{% hint style="info" %}
**How this page was checked:** `code --add-mcp` was tested with VS Code 1.142 and writes the `servers` entry above. A full Copilot chat session against Vayl has not been run.
{% endhint %}

## Using it

Vayl sends MCP server instructions when the client connects. They tell the model to call `remember` when you state or change a fact and `recall` before answering from what you said earlier, so "remember that we moved to Postgres" goes to Vayl without naming it. Clients differ in whether they pass those instructions to the model; if yours doesn't call Vayl on its own, add a line to its rules file: "Use the vayl tools to remember project decisions and to recall them before answering."

For a coding project, add `"VAYL_SLOT_SCHEMA": "preset:coding"` to `env` so a changed decision always replaces the old one, even on a small local model. To share one memory across a team, point the client at a [`vayl-server`](../guides/deploying-vayl-server.md) instead, if it supports remote MCP servers.

## Next steps

<table data-view="cards"><thead><tr><th></th><th></th><th data-hidden data-card-target data-type="content-ref"></th></tr></thead><tbody><tr><td><h4><i class="fa-database" style="color:$primary;">:database:</i> Memory tools</h4></td><td>What <code>remember</code>, <code>recall</code>, <code>history</code> and <code>forget</code> do.</td><td><a href="../mcp-tools/memory.md">memory.md</a></td></tr><tr><td><h4><i class="fa-sliders" style="color:$primary;">:sliders:</i> Configuration</h4></td><td>Every environment variable, including local models.</td><td><a href="../reference/configuration.md">configuration.md</a></td></tr></tbody></table>
