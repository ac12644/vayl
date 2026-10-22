---
description: >-
  Add Vayl to Google's Gemini CLI with one command.
icon: terminal
---

# Gemini CLI

Google's Gemini CLI adds MCP servers with `gemini mcp add`, into `.gemini/settings.json` for a project or `~/.gemini/settings.json` for your user.

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

```bash
gemini mcp add -s user -e LLM_PROVIDER=openai -e 'OPENAI_API_KEY=$OPENAI_API_KEY' \
  -e VAYL_DB=/absolute/path/to/vayl.db vayl vayl-mcp
gemini mcp list
```

The single quotes keep `$OPENAI_API_KEY` as a reference: Gemini CLI expands it from your environment when it starts the server, so the key isn't written to the file. Without `-s user` the server is added to the current project, which Gemini CLI only allows in a trusted folder. Server names can't contain underscores.

`gemini mcp list` should show `✓ vayl … (stdio) - Connected`.
{% endstep %}

{% step %}
### Check the server

Ask the agent to "run the vayl health tool". Every line should read `ok`, or `graph: disabled`. A `FAIL (...)` line names the part to fix, for example `llm: FAIL (HTTP 401: Incorrect API key provided …)`; see [Troubleshooting](../reference/troubleshooting.md).
{% endstep %}
{% endstepper %}

{% hint style="info" %}
**How this page was checked:** Tested with Gemini CLI 0.65.0: `gemini mcp add` with the `$OPENAI_API_KEY` reference, and `gemini mcp list` reported the server as Connected. A Gemini chat session against Vayl has not been run.
{% endhint %}

## Using it

Vayl sends MCP server instructions when the client connects. They tell the model to call `remember` when you state or change a fact and `recall` before answering from what you said earlier, so "remember that we moved to Postgres" goes to Vayl without naming it. Clients differ in whether they pass those instructions to the model; if yours doesn't call Vayl on its own, add a line to its rules file: "Use the vayl tools to remember project decisions and to recall them before answering."

For a coding project, add `"VAYL_SLOT_SCHEMA": "preset:coding"` to `env` so a changed decision always replaces the old one, even on a small local model. To share one memory across a team, point the client at a [`vayl-server`](../guides/deploying-vayl-server.md) instead, if it supports remote MCP servers.

## Next steps

<table data-view="cards"><thead><tr><th></th><th></th><th data-hidden data-card-target data-type="content-ref"></th></tr></thead><tbody><tr><td><h4><i class="fa-database" style="color:$primary;">:database:</i> Memory tools</h4></td><td>What <code>remember</code>, <code>recall</code>, <code>history</code> and <code>forget</code> do.</td><td><a href="../mcp-tools/memory.md">memory.md</a></td></tr><tr><td><h4><i class="fa-sliders" style="color:$primary;">:sliders:</i> Configuration</h4></td><td>Every environment variable, including local models.</td><td><a href="../reference/configuration.md">configuration.md</a></td></tr></tbody></table>
