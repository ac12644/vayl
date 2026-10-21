---
description: >-
  Give Claude Code a project memory that stays current: add Vayl with one
  claude mcp add command, locally or against a team server.
icon: terminal
---

# Claude Code

Add Vayl to Claude Code and the agent keeps your project's decisions current across sessions: when you switch from Redux to Zustand, the old answer is retired instead of sitting next to the new one. You register Vayl as an MCP server with `claude mcp add`. Vayl tells the agent when to use its tools, and a `CLAUDE.md` rule is optional.

Two setups:

* **Local**: Claude Code starts `vayl-mcp` over stdio. Memory is a SQLite file on your machine. It runs as local admin, so no API key for Vayl itself.
* **Team server**: Claude Code connects to a shared `vayl-server` over HTTP with your `vayl_sk_...` key. Everyone on the team reads and writes the same memory.

## Local setup

{% stepper %}
{% step %}
### Install Vayl

```bash
pip install vayl-mcp
vayl-mcp --version
```

Claude Code must be able to find `vayl-mcp`. If you installed it in a virtual environment, use the absolute path from `which vayl-mcp` in the next step.
{% endstep %}

{% step %}
### Add the server

With OpenAI (chat `gpt-5-mini`, embeddings `text-embedding-3-small`):

```bash
claude mcp add vayl \
  -e LLM_PROVIDER=openai \
  -e OPENAI_API_KEY=sk-... \
  -e VAYL_DB=/abs/path/to/project/.vayl/vayl.db \
  -e VAYL_SLOT_SCHEMA=preset:coding \
  -- vayl-mcp
```

With local Ollama (nothing leaves the machine; pull `qwen2.5:3b` and `nomic-embed-text` first, see [Local models with Ollama](../guides/local-models-with-ollama.md)):

```bash
claude mcp add vayl \
  -e LLM_PROVIDER=openai \
  -e OPENAI_BASE_URL=http://localhost:11434/v1 \
  -e VAYL_DB=/abs/path/to/project/.vayl/vayl.db \
  -e VAYL_SLOT_SCHEMA=preset:coding \
  -- vayl-mcp
```

Put the server name (`vayl`) before the `-e` flags, and the command after `--`. Everything after `--` is the command Claude Code runs.

`LLM_PROVIDER=openai` is there on purpose. Without it, Vayl picks its provider from whichever key it sees first, and an `ANTHROPIC_API_KEY` in your shell environment would win over the OpenAI key.
{% endstep %}

{% step %}
### Check the connection

```bash
claude mcp list
```

`vayl` should show as connected. Inside a Claude Code session, `/mcp` shows the same status and the tool list.
{% endstep %}

{% step %}
### Run the health check

Ask Claude Code to "run the vayl health tool". It returns a report like this (it makes one small model call and one embedding call):

```
config: LLM_PROVIDER=openai, model=(default)
license: community
encryption: on · signing: on
db: ok
embedder: ok
llm: ok
graph: disabled
```

Any line reading `FAIL (...)` names the part to fix. See [Troubleshooting](../reference/troubleshooting.md).
{% endstep %}
{% endstepper %}

### Choose a scope

`claude mcp add` takes `-s` / `--scope`:

| Scope | Where it is stored | Use it for |
| --- | --- | --- |
| `local` (default) | Your Claude Code config, for the current project only | Personal memory for one repo |
| `project` | `.mcp.json` in the repo root, committed and shared | Giving every contributor the same server entry |
| `user` | Your Claude Code config, for every project | One memory across all your repos |

With `project` scope, don't commit keys. Claude Code expands environment variables in `.mcp.json`, so write `-e OPENAI_API_KEY='${OPENAI_API_KEY}'` (single quotes, so your shell doesn't expand it) and let each person set the key in their own environment.

### Keep projects separate

Memory follows `VAYL_DB`. Two projects that point at the same file share memory. Give each project its own absolute path, for example `<project>/.vayl/vayl.db`, and add `.vayl/` to `.gitignore`. With `user` scope, all projects share one file unless you separate them with `user_id` (see [Memory spaces](../core-concepts/memory-spaces.md)).

## Team server setup

If your team runs `vayl-server` (see [Deploying vayl-server](../guides/deploying-vayl-server.md)), ask an admin for an API key and add the server over HTTP:

```bash
claude mcp add --transport http vayl https://your-host/mcp \
  --header "Authorization: Bearer vayl_sk_..."
```

The server needs no model key from you: it uses its own configuration. What you can do is set by your key's role. A `member` key can store and recall and can approve gated changes. An `agent` key can store and recall but cannot approve (see [Authentication & access](../core-concepts/authentication-and-access.md)).

Check it the same way: `claude mcp list`, then the `health` tool.

## Use the coding preset

`VAYL_SLOT_SCHEMA=preset:coding` declares the facts a codebase usually has, so a switch always retires the old value, even on a small local model. The preset's slots:

| Slot | Kind |
| --- | --- |
| `language`, `framework`, `state_library`, `package_manager`, `runtime_version`, `database`, `orm`, `api_style`, `styling`, `auth_provider`, `deploy_target`, `ci_provider`, `test_runner`, `build_tool`, `default_branch`, `repo_url` | One value. A new one supersedes the old. |
| `external_service`, `architecture_decision` | A list. New entries add to it. |
| `convention` | A list, in the `guardrail` category |

Add `-e VAYL_CRITICAL_CATEGORIES=guardrail` to put every stored convention into `recall`'s context on each call, instead of only the best-matching facts. Nothing in the preset needs human approval: changes apply immediately.

## Tell Claude Code when to use memory

Vayl sends MCP server instructions when Claude Code connects. They tell the model to call `remember` when you state or change a decision and `recall` before answering questions about the project, so "Remember for this project: our database is Postgres" goes to Vayl without naming it. To make the rules explicit for your team, or to change them, add a short instruction to your project's `CLAUDE.md`.

{% hint style="info" %}
This is a suggested starting point, not a required format. Adjust it to how your team works.
{% endhint %}

```markdown
## Project memory (Vayl)

- Before answering questions about this project's stack, conventions, or past decisions, call the `vayl` `recall` tool.
- When we make or change a decision (a library, a convention, a service), call `remember` with one plain sentence, e.g. "We moved off Redux to Zustand."
- When something is no longer true and has no replacement, call `forget`.
- If recall says it doesn't know, say so instead of guessing.
```

`recall` answers are written by the model from the stored facts, so the wording varies between calls. `remember` returns a fixed-format line that tells you what Vayl did. With the coding preset, "We use Redux for state." then "We moved off Redux to Zustand." return:

```
Stored: [ADD] state_library = Redux
Stored: [SUPERSEDE] state_library = Zustand
```

The subject name (`state_library`) comes from the preset slot. Without a preset, the model picks the name, so it can differ.

## Next steps

<table data-view="cards"><thead><tr><th></th><th></th><th data-hidden data-card-target data-type="content-ref"></th></tr></thead><tbody><tr><td><h4><i class="fa-brain" style="color:$primary;">:brain:</i> Your first memory</h4></td><td>Store, change and retract a fact, and read the history.</td><td><a href="../getting-started/your-first-memory.md">your-first-memory.md</a></td></tr><tr><td><h4><i class="fa-database" style="color:$primary;">:database:</i> Memory tools</h4></td><td>Every tool Claude Code gets: remember, recall, history, and more.</td><td><a href="../mcp-tools/memory.md">memory.md</a></td></tr><tr><td><h4><i class="fa-server" style="color:$primary;">:server:</i> Deploying vayl-server</h4></td><td>Run one shared memory for your whole team.</td><td><a href="../guides/deploying-vayl-server.md">deploying-vayl-server.md</a></td></tr></tbody></table>
