---
description: From install to your first reconciled memory, in five checks.
icon: clipboard-list
---

# Getting started checklist

1. **Install** on Python 3.11 or newer: `pip install vayl-mcp`, then `vayl-mcp --version`.
2. **See it work offline:** `vayl-demo` runs the real engine on a scripted conversation with no keys or model.
3. **Choose a model.** Set `OPENAI_API_KEY` (chat `gpt-5-mini`, embeddings `text-embedding-3-small`), or run [a local model with Ollama](how-do-i-use-a-local-model-instead-of-openai.md). If `ANTHROPIC_API_KEY` is also in your environment, set `LLM_PROVIDER=openai` too.
4. **Connect your MCP client**, with `VAYL_DB` pointing at an absolute path in a folder that exists. For Claude Code:

   ```bash
   mkdir -p ~/.vayl
   claude mcp add vayl -e LLM_PROVIDER=openai -e OPENAI_API_KEY=sk-... -e VAYL_DB=$HOME/.vayl/vayl.db -- vayl-mcp
   ```
5. **Check it's healthy:** ask your agent to run the `health` tool. Every line should read `ok` (or `disabled` for the graph).

For a shared team server, see [Deploying vayl-server](https://vayl.gitbook.io/vayl-docs/documentation/guides/deploying-vayl-server). The full walkthrough is the [Quickstart](https://vayl.gitbook.io/vayl-docs/documentation/getting-started/quickstart).
