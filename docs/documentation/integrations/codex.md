---
description: >-
  Add Vayl to OpenAI's Codex CLI with one command.
icon: terminal
---

# Codex CLI

OpenAI's Codex CLI adds MCP servers with `codex mcp add` and stores them in `~/.codex/config.toml`.

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
codex mcp add vayl --env LLM_PROVIDER=openai --env OPENAI_API_KEY=sk-... \
  --env VAYL_DB=/absolute/path/to/vayl.db -- vayl-mcp
codex mcp list
```

That writes:

```toml
[mcp_servers.vayl]
command = "vayl-mcp"

[mcp_servers.vayl.env]
LLM_PROVIDER = "openai"
OPENAI_API_KEY = "sk-..."
VAYL_DB = "/absolute/path/to/vayl.db"
```

`codex mcp list` should show `vayl` as `enabled`.
{% endstep %}

{% step %}
### Approve memory writes

Codex asks before running a tool that changes something, such as `remember`. Approve the call when Codex proposes it. To change that per tool, see `default_tools_approval_mode` and `tools.<name>.approval_mode` in [Codex's MCP docs](https://developers.openai.com/codex/mcp). A non-interactive `codex exec` run with approvals off can't store memories, because it rejects every call that needs approval.
{% endstep %}

{% step %}
### Check the server

Ask the agent to "run the vayl health tool". Every line should read `ok`, or `graph: disabled`. A `FAIL (...)` line names the part to fix, for example `llm: FAIL (HTTP 401: Incorrect API key provided …)`; see [Troubleshooting](../reference/troubleshooting.md).
{% endstep %}
{% endstepper %}

{% hint style="info" %}
**How this page was checked:** Tested with Codex CLI 0.163.0 and `gpt-6-luna`: `codex mcp add` and `codex mcp list` as above, and Codex chose Vayl's tools for "Remember for this customer account: …" without being told to. Unattended writes in `codex exec` were refused for approval, as described.
{% endhint %}

## Using it

Vayl sends MCP server instructions when the client connects. They tell the model to call `remember` when you state or change a fact and `recall` before answering from what you said earlier, so "remember that we moved to Postgres" goes to Vayl without naming it. Clients differ in whether they pass those instructions to the model; if yours doesn't call Vayl on its own, add a line to its rules file: "Use the vayl tools to remember project decisions and to recall them before answering."

For a coding project, add `"VAYL_SLOT_SCHEMA": "preset:coding"` to `env` so a changed decision always replaces the old one, even on a small local model. To share one memory across a team, point the client at a [`vayl-server`](../guides/deploying-vayl-server.md) instead, if it supports remote MCP servers.

## Next steps

<table data-view="cards"><thead><tr><th></th><th></th><th data-hidden data-card-target data-type="content-ref"></th></tr></thead><tbody><tr><td><h4><i class="fa-database" style="color:$primary;">:database:</i> Memory tools</h4></td><td>What <code>remember</code>, <code>recall</code>, <code>history</code> and <code>forget</code> do.</td><td><a href="../mcp-tools/memory.md">memory.md</a></td></tr><tr><td><h4><i class="fa-sliders" style="color:$primary;">:sliders:</i> Configuration</h4></td><td>Every environment variable, including local models.</td><td><a href="../reference/configuration.md">configuration.md</a></td></tr></tbody></table>
