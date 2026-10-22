<div align="center">

# Vayl

The reconciling memory layer for AI agents.

[![PyPI](https://img.shields.io/pypi/v/vayl-mcp?color=346ddb&label=pypi)](https://pypi.org/project/vayl-mcp/)
![Python](https://img.shields.io/badge/python-3.11%2B-blue)
![License](https://img.shields.io/badge/license-Apache%202.0-blue)
[![OpenSSF Scorecard](https://api.scorecard.dev/projects/github.com/ac12644/vayl/badge)](https://scorecard.dev/viewer/?uri=github.com/ac12644/vayl)
[![Docs](https://img.shields.io/badge/docs-vayl.gitbook.io-346DDB)](https://vayl.gitbook.io/vayl-docs)

</div>

<!-- mcp-name: io.github.ac12644/vayl -->

When a fact changes, most agent memory keeps both versions and lets the model guess which is current. Vayl replaces the old value, keeps it in history, and can hold high-stakes changes until a person approves them. It runs locally on SQLite and works with any MCP client: Claude Code, Cursor, Claude Desktop, or your own agent.

<p align="center">
  <img src="assets/vayl-demo.gif" alt="A real run with a fictional patient: warfarin is recorded, then replaced by apixaban. With approval required (VAYL_CONFIRM_MODE=review), the change waits in pending_changes until a clinician confirms it. Recall then answers apixaban now, warfarin before, and refuses amoxicillin because of the recorded penicillin allergy." width="860">
</p>

<p align="center"><sub>A real run with <code>gpt-6-luna</code> and the clinical preset, with every change set to wait for approval. The patient is fictional. Script: <a href="assets/demo/clinical_demo.py"><code>assets/demo/clinical_demo.py</code></a></sub></p>

## Features

- **Reconciles changes.** A new value supersedes the old one, and "we stopped X" retracts X. A retired fact is never returned as current.
- **Keeps history.** Ask what is true now or what was true before. Every change goes on a signed, tamper-evident audit chain.
- **Automatic, with a person in the loop only when it matters.** On gated slots such as medications, a clear change applies by itself and an uncertain one (hedged, negated, or one that would leave two anticoagulants active) waits for approval; set `VAYL_CONFIRM_MODE=review` to approve every change. `check_before_act` blocks an action that depends on a disputed, stale or just-changed fact.
- **Small and local.** SQLite by default, Postgres for teams. One model call to store a fact and one to answer. Any OpenAI-compatible model, Anthropic, Groq or a local Ollama model.
- **Built-in presets** for clinical, support, finance, sales, coding and assistant memory.

## Install

```bash
pip install vayl-mcp
```

Add it to Claude Code:

```bash
claude mcp add vayl -e LLM_PROVIDER=openai -e OPENAI_API_KEY=sk-... -e VAYL_DB=$HOME/.vayl/vayl.db -- vayl-mcp
```

For Cursor, Claude Desktop and other clients, see the [quickstart](https://vayl.gitbook.io/vayl-docs/documentation/getting-started/quickstart). Run `vayl-demo` to try reconciliation with no API key.

## Use it from code

```python
from vayl import Vayl

with Vayl(user_id="cust_5521") as m:
    m.remember("Acme's invoice bounced. Account suspended until payment clears.")
    print(m.recall("Is Acme's account in good standing?"))
    # No. Acme's account is suspended until payment clears.
```

## Integrations

**Agent frameworks.** Backends plug into the framework's own memory, so facts are stored and recalled on every turn. Tool adapters give the agent memory tools to call.

| Framework | Install | Use |
|---|---|---|
| Google ADK | `pip install 'vayl-mcp[adk]'` | `from vayl.integrations.adk import VaylMemoryService` (memory backend) |
| LlamaIndex | `pip install 'vayl-mcp[llamaindex]'` | `from vayl.integrations.llamaindex import VaylMemory` (memory block) |
| AutoGen | `pip install 'vayl-mcp[autogen]'` | `from vayl.integrations.autogen import VaylMemory` (memory backend) |
| LangGraph / LangChain | `pip install 'vayl-mcp[langgraph]'` | `from vayl.integrations.langgraph import VaylMemory` (tools) |
| OpenAI Agents SDK | `pip install 'vayl-mcp[openai-agents]'` | `from vayl.integrations.openai_agents import VaylMemory` (tools) |
| CrewAI | `pip install 'vayl-mcp[crewai]'` | `from vayl.integrations.crewai import VaylMemory` (tools) |
| Pydantic AI | `pip install vayl-mcp 'pydantic-ai-slim[openai,mcp]'` | `MCPToolset` over MCP, no adapter |
| Agno | `pip install vayl-mcp agno openai` | `MCPTools` over MCP, no adapter |
| Vercel AI SDK | `npm i @vayl.dev/client ai zod` | `import { vaylTools } from "@vayl.dev/client/vercel"` |
| Mastra | `npm i @vayl.dev/client @mastra/core zod` | `import { vaylTools } from "@vayl.dev/client/mastra"` |

Examples for each are in [Agent frameworks](https://vayl.gitbook.io/vayl-docs/documentation/integrations/agent-frameworks).

**MCP clients.** Install `vayl-mcp`, then add it to your client: [Claude Code](https://vayl.gitbook.io/vayl-docs/documentation/integrations/claude-code) · [Cursor](https://vayl.gitbook.io/vayl-docs/documentation/integrations/cursor) · [VS Code](https://vayl.gitbook.io/vayl-docs/documentation/integrations/vs-code) · [Windsurf](https://vayl.gitbook.io/vayl-docs/documentation/integrations/windsurf) · [Zed](https://vayl.gitbook.io/vayl-docs/documentation/integrations/zed) · [Cline](https://vayl.gitbook.io/vayl-docs/documentation/integrations/cline) · [Continue](https://vayl.gitbook.io/vayl-docs/documentation/integrations/continue) · [Codex CLI](https://vayl.gitbook.io/vayl-docs/documentation/integrations/codex) · [Gemini CLI](https://vayl.gitbook.io/vayl-docs/documentation/integrations/gemini-cli). For a shared team memory, run [`vayl-server`](https://vayl.gitbook.io/vayl-docs/documentation/guides/deploying-vayl-server) and call it over MCP or [REST](https://vayl.gitbook.io/vayl-docs/api-reference).

## Documentation

| | |
|---|---|
| [Quickstart](https://vayl.gitbook.io/vayl-docs/documentation/getting-started/quickstart) | Install and connect a client |
| [How Vayl works](https://vayl.gitbook.io/vayl-docs/documentation/concepts/how-vayl-works) | Extraction, reconciliation and retrieval |
| [Tutorial: a hospital medication assistant](https://vayl.gitbook.io/vayl-docs/documentation/guides/tutorial-a-hospital-medication-assistant) | Clinical preset, critical facts and approval |
| [Tutorial: a customer-support assistant](https://vayl.gitbook.io/vayl-docs/documentation/guides/tutorial-a-customer-support-assistant) | Account state and safety checks |
| [MCP tools](https://vayl.gitbook.io/vayl-docs/documentation/reference/mcp-tools) | All 30+ tools |
| [Deploying vayl-server](https://vayl.gitbook.io/vayl-docs/documentation/guides/deploying-vayl-server) | Team server, auth, Docker, Postgres |
| [Benchmarks](https://vayl.gitbook.io/vayl-docs/documentation/why-vayl/why-your-agents-memory-returns-stale-facts) | Stale-value and retraction results against Mem0 and Graphiti |

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). The unit tests run offline, with no API key. Good starting points are labeled [good first issue](https://github.com/ac12644/vayl/labels/good%20first%20issue).

## Security

Data is encrypted at rest by default, and Vayl sends nothing anywhere except to the model endpoints you configure. Vayl is not certified for clinical use and has not been audited by a third party. Report vulnerabilities as described in [SECURITY.md](SECURITY.md).

## License

[Apache-2.0](LICENSE)
</content>
</invoke>
