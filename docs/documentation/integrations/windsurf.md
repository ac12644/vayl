---
description: >-
  Add Vayl to Windsurf's Cascade agent as an MCP server.
icon: wind
---

# Windsurf

Windsurf's Cascade agent can use Vayl as an MCP server.

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

Open the MCP settings from the Cascade panel and edit `mcp_config.json`. Its current location, per Windsurf's docs, is `~/.config/devin/mcp_config.json` on macOS and Linux and `%APPDATA%\devin\mcp_config.json` on Windows. Add:

```json
{
  "mcpServers": {
    "vayl": {
      "command": "vayl-mcp",
      "env": {
        "LLM_PROVIDER": "openai",
        "OPENAI_API_KEY": "${env:OPENAI_API_KEY}",
        "VAYL_DB": "/absolute/path/to/vayl.db"
      }
    }
  }
}
```

`${env:OPENAI_API_KEY}` reads the key from your environment instead of the file. Cascade can use at most 100 tools across all servers; Vayl has 32, and `disabledTools` turns off any you don't need.
{% endstep %}

{% step %}
### Check the server

Ask the agent to "run the vayl health tool". Every line should read `ok`, or `graph: disabled`. A `FAIL (...)` line names the part to fix, for example `llm: FAIL (HTTP 401: Incorrect API key provided …)`; see [Troubleshooting](../reference/troubleshooting.md).
{% endstep %}
{% endstepper %}

{% hint style="info" %}
**How this page was checked:** Taken from [Windsurf's MCP docs](https://docs.windsurf.com/windsurf/cascade/mcp), which now redirect to devin.ai. Not run against Windsurf here.
{% endhint %}

## Using it

Vayl sends MCP server instructions when the client connects. They tell the model to call `remember` when you state or change a fact and `recall` before answering from what you said earlier, so "remember that we moved to Postgres" goes to Vayl without naming it. Clients differ in whether they pass those instructions to the model; if yours doesn't call Vayl on its own, add a line to its rules file: "Use the vayl tools to remember project decisions and to recall them before answering."

For a coding project, add `"VAYL_SLOT_SCHEMA": "preset:coding"` to `env` so a changed decision always replaces the old one, even on a small local model. To share one memory across a team, point the client at a [`vayl-server`](../guides/deploying-vayl-server.md) instead, if it supports remote MCP servers.

## Next steps

<table data-view="cards"><thead><tr><th></th><th></th><th data-hidden data-card-target data-type="content-ref"></th></tr></thead><tbody><tr><td><h4><i class="fa-database" style="color:$primary;">:database:</i> Memory tools</h4></td><td>What <code>remember</code>, <code>recall</code>, <code>history</code> and <code>forget</code> do.</td><td><a href="../mcp-tools/memory.md">memory.md</a></td></tr><tr><td><h4><i class="fa-sliders" style="color:$primary;">:sliders:</i> Configuration</h4></td><td>Every environment variable, including local models.</td><td><a href="../reference/configuration.md">configuration.md</a></td></tr></tbody></table>
