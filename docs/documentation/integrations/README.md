---
description: >-
  Connect Vayl to your coding agent, agent framework, or own code, over local
  stdio or a shared HTTP server.
icon: puzzle-piece
---

# Integrations

Vayl is a standard Model Context Protocol (MCP) server, so any MCP client can use it, and it talks to any OpenAI-compatible model. It ships two transports:

| Transport | Command | Who it is for | Auth |
| --- | --- | --- | --- |
| stdio | `vayl-mcp` | One person on one machine. The client starts the process. | Runs as local admin unless `VAYL_AUTH_REQUIRED` is on |
| Streamable HTTP | `vayl-server` | A team sharing one memory at `https://your-host/mcp` | Always required: `Authorization: Bearer vayl_sk_...` |

Both expose the same tools. Pick the page for the tool you already use.

## Coding agents and clients

<table data-view="cards"><thead><tr><th></th><th></th><th data-hidden data-card-target data-type="content-ref"></th></tr></thead><tbody><tr><td><h4><i class="fa-terminal" style="color:$primary;">:terminal:</i> Claude Code</h4></td><td>Add Vayl with <code>claude mcp add</code>, locally or against a team server, plus a CLAUDE.md snippet.</td><td><a href="claude-code.md">claude-code.md</a></td></tr><tr><td><h4><i class="fa-i-cursor" style="color:$primary;">:i-cursor:</i> Cursor</h4></td><td>MCP config for OpenAI or local Ollama, a project rule, and per-project memory.</td><td><a href="cursor.md">cursor.md</a></td></tr><tr><td><h4><i class="fa-code" style="color:$primary;">:code:</i> VS Code</h4></td><td>Copilot agent mode, with the API key kept out of the config.</td><td><a href="vs-code.md">vs-code.md</a></td></tr><tr><td><h4><i class="fa-wind" style="color:$primary;">:wind:</i> Windsurf</h4></td><td>Cascade's MCP config.</td><td><a href="windsurf.md">windsurf.md</a></td></tr><tr><td><h4><i class="fa-bolt" style="color:$primary;">:bolt:</i> Zed</h4></td><td>A context server in settings.json.</td><td><a href="zed.md">zed.md</a></td></tr><tr><td><h4><i class="fa-robot" style="color:$primary;">:robot:</i> Cline</h4></td><td>MCP settings with safe auto-approve.</td><td><a href="cline.md">cline.md</a></td></tr><tr><td><h4><i class="fa-forward" style="color:$primary;">:forward:</i> Continue</h4></td><td>An MCP server in agent mode.</td><td><a href="continue.md">continue.md</a></td></tr><tr><td><h4><i class="fa-terminal" style="color:$primary;">:terminal:</i> Codex CLI</h4></td><td>One <code>codex mcp add</code> command.</td><td><a href="codex.md">codex.md</a></td></tr><tr><td><h4><i class="fa-terminal" style="color:$primary;">:terminal:</i> Gemini CLI</h4></td><td>One <code>gemini mcp add</code> command.</td><td><a href="gemini-cli.md">gemini-cli.md</a></td></tr><tr><td><h4><i class="fa-plug" style="color:$primary;">:plug:</i> Any MCP client</h4></td><td>Claude Desktop and others: the tool list and how the protocol maps to Vayl.</td><td><a href="../mcp-tools/the-mcp-interface.md">the-mcp-interface.md</a></td></tr></tbody></table>

## Agent frameworks and your own code

<table data-view="cards"><thead><tr><th></th><th></th><th data-hidden data-card-target data-type="content-ref"></th></tr></thead><tbody><tr><td><h4><i class="fa-diagram-project" style="color:$primary;">:diagram-project:</i> Agent frameworks</h4></td><td>A memory backend for Google ADK, LlamaIndex and AutoGen; tools for LangGraph, the OpenAI Agents SDK, CrewAI, the Vercel AI SDK and Mastra; MCP for Pydantic AI and Agno.</td><td><a href="agent-frameworks.md">agent-frameworks.md</a></td></tr><tr><td><h4><i class="fa-code" style="color:$primary;">:code:</i> Calling Vayl from code</h4></td><td>The Python and TypeScript clients: call <code>remember</code> and <code>recall</code> as methods.</td><td><a href="../getting-started/calling-vayl-from-code.md">calling-vayl-from-code.md</a></td></tr></tbody></table>

## Shared team memory

<table data-view="cards"><thead><tr><th></th><th></th><th data-hidden data-card-target data-type="content-ref"></th></tr></thead><tbody><tr><td><h4><i class="fa-server" style="color:$primary;">:server:</i> Deploying vayl-server</h4></td><td>Run the authenticated HTTP server with Docker or Postgres and issue API keys to your team.</td><td><a href="../guides/deploying-vayl-server.md">deploying-vayl-server.md</a></td></tr></tbody></table>

## Models and embedders

Vayl needs a chat model to extract and reconcile facts, and an embedder for retrieval. Both use any OpenAI-compatible endpoint.

| Setup | What to set | Result |
| --- | --- | --- |
| OpenAI | `OPENAI_API_KEY` | Chat `gpt-5-mini`, embeddings `text-embedding-3-small` |
| Local Ollama | Nothing, or `OPENAI_BASE_URL=http://localhost:11434/v1` | Chat `qwen2.5:3b`, embeddings `nomic-embed-text`. Nothing leaves the machine. |
| vLLM, LM Studio, other OpenAI-compatible | `OPENAI_BASE_URL`, `OPENAI_MODEL`, and `EMBED_MODEL` if the server has no `nomic-embed-text` | Your endpoint for both |
| Anthropic or Groq for chat | `ANTHROPIC_API_KEY` or `GROQ_API_KEY` (or `LLM_PROVIDER`) | Chat on that provider; embeddings still need an OpenAI-compatible endpoint |

See [Local models with Ollama](../guides/local-models-with-ollama.md) and [Configuration](../reference/configuration.md) for every variable.

## Storage

| Backend | Set | Use it for |
| --- | --- | --- |
| SQLite (default) | `VAYL_DB=/abs/path/vayl.db` | One user, or a small server. One file, nothing to operate. |
| Postgres | `VAYL_DATABASE_URL=postgresql://...` and `pip install 'vayl-mcp[postgres]'` | Several server processes writing to one store |
| Neo4j graph projection (optional) | `VAYL_GRAPH=on` and `NEO4J_*`, `pip install 'vayl-mcp[graph]'` | Multi-hop relational queries on top of the main store |

## Next steps

<table data-view="cards"><thead><tr><th></th><th></th><th data-hidden data-card-target data-type="content-ref"></th></tr></thead><tbody><tr><td><h4><i class="fa-rocket" style="color:$primary;">:rocket:</i> Quickstart</h4></td><td>Install Vayl, connect a client, and store your first fact.</td><td><a href="../getting-started/quickstart.md">quickstart.md</a></td></tr><tr><td><h4><i class="fa-terminal" style="color:$primary;">:terminal:</i> Claude Code</h4></td><td>Local or team memory for Claude Code in two commands.</td><td><a href="claude-code.md">claude-code.md</a></td></tr><tr><td><h4><i class="fa-sliders" style="color:$primary;">:sliders:</i> Configuration</h4></td><td>Every environment variable, default, and accepted value.</td><td><a href="../reference/configuration.md">configuration.md</a></td></tr></tbody></table>
