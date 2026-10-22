---
description: >-
  Give your existing agent reconciling memory: as a memory backend for Google
  ADK, LlamaIndex and AutoGen, as tools for LangGraph, the OpenAI Agents SDK,
  CrewAI, the Vercel AI SDK and Mastra, or over MCP for Pydantic AI and Agno.
---

# Agent frameworks

Add Vayl to an agent you already build with a framework, and its memory stops going stale. There are two kinds of integration:

* **Memory backends** (Google ADK, LlamaIndex, AutoGen) plug into the framework's own memory slot. What the user says is stored, and current facts reach the model before each turn, without the agent deciding to call anything.
* **Tool adapters** (LangGraph, the OpenAI Agents SDK, CrewAI, the Vercel AI SDK, Mastra) give the agent five memory tools, `remember`, `recall`, `history`, `forget` and `list_memories`, and the agent decides when to call them. Pydantic AI and Agno get the same tools directly over MCP, with no adapter.

Because Vayl reconciles, the agent is never handed a fact and its replacement as if both were current:

```
"Acme Corp is on the Enterprise plan, account active."            → account_status = active
"Acme's invoice bounced. Account suspended until payment clears." → active retired, suspended current
recall("Is Acme's account in good standing?")                     → an answer built from "suspended" only
```

A plain vector store would return both. Vayl keeps the old value in `history` instead. `recall` answers are written by the model, so the exact wording varies.

## How the adapters work

Every adapter has the same shape: bind a memory to a [memory space](../core-concepts/memory-spaces.md) once, then hand its tools to your agent.

* **Same tools everywhere.** The five tools have the same names and model-facing descriptions in every framework.
* **Scope is bound in your code.** `user_id` / `agent_id` / `run_id` are set on the client, never as tool arguments, so the model can't read or change whose memory it touches.
* **One connection.** The adapter keeps one session open, either to a local `vayl-mcp` it starts over stdio, or to a shared `vayl-server` over HTTP.

| Framework | Language | Install | Import |
| --- | --- | --- | --- |
| LangGraph / LangChain | Python | `pip install 'vayl-mcp[langgraph]'` | `from vayl.integrations.langgraph import VaylMemory` |
| OpenAI Agents SDK | Python | `pip install 'vayl-mcp[openai-agents]'` | `from vayl.integrations.openai_agents import VaylMemory` |
| CrewAI | Python | `pip install 'vayl-mcp[crewai]'` | `from vayl.integrations.crewai import VaylMemory` |
| Google ADK | Python | `pip install 'vayl-mcp[adk]'` | `from vayl.integrations.adk import VaylMemoryService` |
| LlamaIndex | Python | `pip install 'vayl-mcp[llamaindex]'` | `from vayl.integrations.llamaindex import VaylMemory` |
| AutoGen | Python | `pip install 'vayl-mcp[autogen]'` | `from vayl.integrations.autogen import VaylMemory` |
| Pydantic AI | Python | `pip install vayl-mcp 'pydantic-ai-slim[openai,mcp]'` | `from pydantic_ai.mcp import MCPToolset` (no adapter) |
| Agno | Python | `pip install vayl-mcp agno openai` | `from agno.tools.mcp import MCPTools` (no adapter) |
| Vercel AI SDK | TypeScript | `npm i @vayl.dev/client ai zod @ai-sdk/openai` | `import { vaylTools } from "@vayl.dev/client/vercel"` |
| Mastra | TypeScript | `npm i @vayl.dev/client @mastra/core zod` | `import { vaylTools } from "@vayl.dev/client/mastra"` |

The TypeScript client starts `vayl-mcp` for local use, so install the Python package too (`pip install vayl-mcp`), or connect to a team server with `url` and `apiKey`.

## Two sets of model settings

Your agent and Vayl each call a model, and each is configured separately:

* **Your framework's model** (the one that runs the agent) reads its own settings, for example `OPENAI_API_KEY` for `gpt-4o`.
* **Vayl's model** (the one that extracts and reconciles facts) reads the environment of the `vayl-mcp` process. By default that process inherits your process environment, so one `OPENAI_API_KEY` covers both. To give Vayl different settings, pass `env={...}` (Python) or `env: {...}` (TypeScript), which replaces the inherited environment (only basics such as `PATH` and `HOME` are kept). See [Configuration](../reference/configuration.md).

When you connect to a team server with `url=` and `api_key=`, Vayl uses the server's model settings, and only your framework needs a key.

## Python

{% tabs %}
{% tab title="LangGraph" %}
```bash
pip install 'vayl-mcp[langgraph]' langchain-openai
export OPENAI_API_KEY=sk-...
```

`langchain-openai` is not part of the `[langgraph]` extra. You need it for the `"openai:..."` model string below; without it, `mem.agent(...)` fails with an `ImportError` asking you to install it. Use the LangChain package for your provider instead if you don't use OpenAI.

```python
from vayl.integrations.langgraph import VaylMemory

with VaylMemory(user_id="proj_7") as mem:
    agent = mem.agent("openai:gpt-4o-mini")      # a ready agent with the memory tools and a memory-aware prompt
    result = agent.invoke({"messages": [("user", "Acme's payment cleared, the account is active again. Can I renew their licenses?")]})
    print(result["messages"][-1].content)
```

To use your own agent or graph, bind the tools directly:

```python
from langchain.agents import create_agent

agent = create_agent("openai:gpt-4o-mini", tools=mem.tools())
```

`mem.agent()` uses LangChain v1's `create_agent`, and falls back to `langgraph.prebuilt.create_react_agent` on older installs.
{% endtab %}

{% tab title="OpenAI Agents SDK" %}
```bash
pip install 'vayl-mcp[openai-agents]'
export OPENAI_API_KEY=sk-...
```

```python
from agents import Runner
from vayl.integrations.openai_agents import VaylMemory

with VaylMemory(user_id="proj_7") as mem:
    agent = mem.agent(model="gpt-4o")      # or: Agent(name=..., tools=mem.tools())
    result = Runner.run_sync(agent, "Acme's payment cleared, the account is active again. Can I renew their licenses?")
    print(result.final_output)
```

The memory tools are SDK function tools. An SDK `Session` and Vayl work together: the `Session` for within-conversation continuity, Vayl for facts that last across sessions.
{% endtab %}

{% tab title="CrewAI" %}
```bash
pip install 'vayl-mcp[crewai]'
```

```python
from crewai import Agent
from vayl.integrations.crewai import VaylMemory

with VaylMemory(user_id="proj_7") as mem:
    analyst = Agent(role="Analyst", goal="Answer with current facts",
                    backstory="Uses long-term memory.", tools=mem.tools())
    # build your tasks and Crew as usual, inside the with-block
```

`mem.agent(role=..., llm=...)` returns a ready `crewai.Agent` with a memory-aware backstory. The adapter targets the CrewAI v1 tools API (`crewai.tools.tool`).

{% hint style="warning" %}
**Needs verification.** CrewAI is not installed in Vayl's CI, so the CrewAI adapter is checked for safe import and the missing-package error only. Its runtime behaviour has not been verified for this page.
{% endhint %}
{% endtab %}
{% endtabs %}

In every Python adapter:

* `mem.tools(include=[...])` or `mem.tools(exclude=[...])` narrows the tool set.
* `mem.remember(...)`, `mem.recall(...)`, `mem.history(...)`, `mem.forget(...)` and `mem.list_memories()` call memory directly from your own code.
* `VaylMemory(url="https://your-host/mcp", api_key="vayl_sk_...", user_id=...)` connects to a team server instead of starting `vayl-mcp`.
* `VaylMemory(client=existing_vayl_client)` reuses a connection you already opened; the adapter then leaves closing it to you.

## Memory backends

{% tabs %}
{% tab title="Google ADK" %}
`VaylMemoryService` is an ADK `BaseMemoryService`. Pass it to your `Runner`, and ADK's `load_memory` tool and your own `search_memory` calls answer from Vayl:

```python
from google.adk.runners import Runner
from vayl.integrations.adk import VaylMemoryService

memory = VaylMemoryService()          # starts vayl-mcp; or url=..., api_key=... for a team server
runner = Runner(agent=agent, app_name="support", session_service=sessions, memory_service=memory)

# after a conversation:
await memory.add_session_to_memory(session)

found = await memory.search_memory(app_name="support", user_id="cust_5521", query="What plan is Acme on?")
print(found.memories[0].content.parts[0].text)   # Acme is on the Pro plan.
```

* Only what the **user** said is stored: the agent's own replies are the model's words, not facts. Pass `include_agent=True` to store them too.
* Adding the same session again stores nothing twice, so you can call `add_session_to_memory` after every turn.
* `add_events_to_memory` (a delta) and `add_memory` (explicit entries) work too.
* ADK's `user_id` is Vayl's `user_id`, and its `app_name` is Vayl's `agent_id`.
{% endtab %}

{% tab title="LlamaIndex" %}
`VaylMemory(...).block()` is a memory block for LlamaIndex's `Memory`:

```python
from llama_index.core.memory import Memory
from vayl.integrations.llamaindex import VaylMemory

vayl = VaylMemory(user_id="cust_5521")
memory = Memory.from_defaults(session_id="support-42", memory_blocks=[vayl.block()])

response = await agent.run("What plan is Acme on?", memory=memory)
```

Messages that leave short-term memory are stored through `remember`. Before each turn the block asks `recall` about the latest user message, and LlamaIndex puts the answer in the prompt:

```
<memory>
<vayl>
Acme is on the Pro plan.
</vayl>
</memory>
```

The block has priority `0`, so LlamaIndex never truncates it. `vayl.tools()` also gives the agent explicit memory tools, as `FunctionTool`s.
{% endtab %}

{% tab title="AutoGen" %}
`VaylMemory` implements autogen-core's `Memory`:

```python
from autogen_agentchat.agents import AssistantAgent
from autogen_core.memory import MemoryContent, MemoryMimeType
from vayl.integrations.autogen import VaylMemory

memory = VaylMemory(user_id="cust_5521")
await memory.add(MemoryContent(content="Acme downgraded to the Pro plan this morning.",
                               mime_type=MemoryMimeType.TEXT))

agent = AssistantAgent("support", model_client=model_client, memory=[memory])
```

Before each model call the agent runs `update_context`, which asks `recall` about the latest user message and adds a system message:

```
From long-term memory (current facts): Acme is on the Pro plan.
```

`add` is `remember` and `query` is `recall`. `clear()` raises instead of erasing every fact, because that is `delete_all`: irreversible, with an erasure receipt. Call `memory.client.call("delete_all")` if you mean it.
{% endtab %}
{% endtabs %}

## Frameworks with MCP support

Pydantic AI and Agno connect to MCP servers themselves, so they use Vayl's tools with no adapter.

{% tabs %}
{% tab title="Pydantic AI" %}
```python
import os
from fastmcp.client.transports import StdioTransport
from pydantic_ai import Agent
from pydantic_ai.mcp import MCPToolset

vayl = MCPToolset(
    StdioTransport(command="vayl-mcp", args=[], env={
        "LLM_PROVIDER": "openai", "OPENAI_API_KEY": os.environ["OPENAI_API_KEY"],
        "VAYL_DB": "/absolute/path/to/vayl.db"}),
    include_instructions=True,
)
agent = Agent("openai:gpt-5-mini", toolsets=[vayl])

async with agent:
    await agent.run("Remember: Acme Corp is on the Enterprise plan.")
    await agent.run("Acme downgraded to the Pro plan this morning.")
    print((await agent.run("What plan was Acme on before?")).output)
```

`include_instructions=True` passes Vayl's server instructions to the model, which tell it when to call `remember` and `recall`, and to set `include_history` for questions about the past. For a team server, pass its URL instead: `MCPToolset("https://your-host/mcp", headers={"Authorization": "Bearer vayl_sk_..."})`.
{% endtab %}

{% tab title="Agno" %}
```python
import os
from agno.agent import Agent
from agno.models.openai import OpenAIResponses
from agno.tools.mcp import MCPTools

async with MCPTools(command="vayl-mcp", env={
        "LLM_PROVIDER": "openai", "OPENAI_API_KEY": os.environ["OPENAI_API_KEY"],
        "PATH": os.environ["PATH"], "VAYL_DB": "/absolute/path/to/vayl.db"}) as vayl:
    agent = Agent(model=OpenAIResponses(id="gpt-5-mini"), tools=[vayl])
    await agent.arun("Remember: Acme Corp is on the Enterprise plan.")
```

Pass `PATH` in `env` so Agno can find `vayl-mcp`. With gpt-6 models, use `OpenAIResponses`: `OpenAIChat` sends a setting those models reject together with tools.
{% endtab %}
{% endtabs %}

Ask one question at a time. "What plan is Acme on, and what was it before?" can come back as "I don't know", because one `recall` can't answer about the present and the past at once; asked separately, both are answered.

{% hint style="info" %}
**How these were checked:** each was run against a real `vayl-mcp` with `gpt-6-luna`, using Google ADK 2.12, LlamaIndex 0.14, AutoGen 0.7, Pydantic AI 2.59 and Agno 3.1, through the store-a-plan, change-it, ask-now, ask-before sequence. The ADK, LlamaIndex and AutoGen backends also have offline tests in `tests/test_memory_backends.py`.
{% endhint %}

## TypeScript

{% tabs %}
{% tab title="Vercel AI SDK" %}
```bash
npm i @vayl.dev/client ai zod @ai-sdk/openai
export OPENAI_API_KEY=sk-...
```

`@ai-sdk/openai` provides the `openai(...)` model below. It is not a dependency of `@vayl.dev/client`.

```typescript
import { generateText, stepCountIs } from "ai";
import { openai } from "@ai-sdk/openai";
import { Vayl } from "@vayl.dev/client";
import { vaylTools } from "@vayl.dev/client/vercel";

const m = await Vayl.connect({ userId: "proj_7" });
const { text } = await generateText({
  model: openai("gpt-4o"),
  tools: vaylTools(m),
  stopWhen: stepCountIs(5),
  prompt: "Acme's payment cleared, the account is active again. Can I renew their licenses?",
});
console.log(text);
await m.close();
```

The adapter targets AI SDK v7 (`ai@>=7`).
{% endtab %}

{% tab title="Mastra" %}
```bash
npm i @vayl.dev/client @mastra/core zod
export OPENAI_API_KEY=sk-...
```

```typescript
import { Agent } from "@mastra/core/agent";
import { Vayl } from "@vayl.dev/client";
import { vaylTools } from "@vayl.dev/client/mastra";

const m = await Vayl.connect({ userId: "proj_7" });
const agent = new Agent({
  id: "assistant",
  name: "Assistant",
  instructions: "Call recall before answering; call remember when the user states or changes a fact.",
  model: "openai/gpt-4o",
  tools: vaylTools(m),
});
```

`"openai/gpt-4o"` is a Mastra v1 model-router string, so no `@ai-sdk/*` import is needed. The adapter targets `@mastra/core@>=1`.
{% endtab %}
{% endtabs %}

`vaylTools(m, { include: [...], exclude: [...] })` narrows the tool set. For a team server, connect with `Vayl.connect({ url: "https://your-host/mcp", apiKey: "vayl_sk_...", userId: "proj_7" })`.

## Framework memory and Vayl together

Each tool-adapter framework has its own memory or store, and those accumulate what you write. Vayl reconciles, and keeps an auditable history. Use the framework's store for raw conversation history, and Vayl for the durable facts the agent must keep current.

## Next steps

<table data-view="cards"><thead><tr><th></th><th></th><th data-hidden data-card-target data-type="content-ref"></th></tr></thead><tbody><tr><td><h4><i class="fa-code" style="color:$primary;">:code:</i> Calling Vayl from code</h4></td><td>The Python and TypeScript clients these adapters build on.</td><td><a href="../getting-started/calling-vayl-from-code.md">calling-vayl-from-code.md</a></td></tr><tr><td><h4><i class="fa-database" style="color:$primary;">:database:</i> Memory tools</h4></td><td>What each tool does and returns.</td><td><a href="../mcp-tools/memory.md">memory.md</a></td></tr><tr><td><h4><i class="fa-sitemap" style="color:$primary;">:sitemap:</i> Memory spaces</h4></td><td>How <code>user_id</code> / <code>agent_id</code> / <code>run_id</code> isolate memory.</td><td><a href="../core-concepts/memory-spaces.md">memory-spaces.md</a></td></tr></tbody></table>
