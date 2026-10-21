# @vayl.dev/client (TypeScript)

TypeScript client for [Vayl](https://github.com/ac12644/vayl) — reconciling memory for AI agents.
Call the MCP tools as methods instead of hand-writing `tools/call` JSON.

```bash
npm install @vayl.dev/client
```

You also need the Vayl MCP server: `pip install vayl-mcp` (the client spawns `vayl-mcp` over stdio),
or point it at a running team server over HTTP.

## Usage

```ts
import { Vayl } from "@vayl.dev/client";

// local: spawns `vayl-mcp` over stdio
const m = await Vayl.connect({ userId: "proj_7" });
await m.remember("We moved off Redux to Zustand");
console.log(await m.recall("what do we use for state?")); // -> "Zustand"
await m.close();

// a shared team server (authenticated streamable-HTTP):
const m2 = await Vayl.connect({
  url: "https://memory.acme.com/mcp",
  apiKey: "vayl_sk_…",
  userId: "cust_5521",
});
```

Or scope it so it always closes:

```ts
import { withVayl } from "@vayl.dev/client";

await withVayl({ userId: "proj_7" }, async (m) => {
  await m.remember("We use Postgres as our primary database");
  console.log(await m.recall("what database do we use?"));
});
```

## Framework integrations

Give an agent Vayl's *reconciling* memory as tools it can call — the agent never gets handed both
"Redux" and "Zustand". Each adapter is a subpath import and takes a peer dependency you install
alongside `@vayl.dev/client` (plus `zod`). Scope (`userId` / `agentId` / `runId`) is bound on the client, never a
tool the model can set.

**Vercel AI SDK** — `npm i @vayl.dev/client ai zod`:

```ts
import { generateText, stepCountIs } from "ai";
import { openai } from "@ai-sdk/openai";
import { Vayl } from "@vayl.dev/client";
import { vaylTools } from "@vayl.dev/client/vercel";

const m = await Vayl.connect({ userId: "proj_7" });
const { text } = await generateText({
  model: openai("gpt-4o"),
  tools: vaylTools(m),                 // remember / recall / history / forget / list_memories
  stopWhen: stepCountIs(5),
  prompt: "We moved off Redux to Zustand. What do we use now?",
});
await m.close();
```

**Mastra** — `npm i @vayl.dev/client @mastra/core zod`:

```ts
import { Agent } from "@mastra/core/agent";
import { Vayl } from "@vayl.dev/client";
import { vaylTools } from "@vayl.dev/client/mastra";

const m = await Vayl.connect({ userId: "proj_7" });
const agent = new Agent({
  id: "assistant",
  name: "Assistant",
  instructions: "Call recall before answering; call remember when the user states or changes a fact.",
  model: "openai/gpt-4o",              // Mastra v1 model-router string
  tools: vaylTools(m),
});
```

`vaylTools(m, { include: [...] , exclude: [...] })` narrows the tool set. (Python agents — LangGraph,
OpenAI Agents SDK, CrewAI — are served from the [Python package](https://github.com/ac12644/vayl).)

## API

- `Vayl.connect(opts)` / `new Vayl(opts)` + `await m.connect()`
- `m.remember(text, extra?)` · `m.recall(question, extra?)` · `m.forget(text, extra?)`
- `m.call(tool, args?)` — **any** other tool (`m.call("check_before_act", { subject: "…" })`,
  `m.call("history", { subject: "…" })`, `m.call("list_memories")`)
- `m.close()`

Every method returns the tool's text result and throws `VaylError` on a tool error. A default
`userId` / `agentId` / `runId` set on the client is sent on every call and can be overridden per call.

### Options

| Option | Description |
|---|---|
| `url`, `apiKey` | Connect to a team server over streamable-HTTP with a Bearer key. |
| `command`, `args`, `env` | stdio: the command to spawn (default `vayl-mcp`) and its environment. |
| `userId`, `agentId`, `runId` | Default [memory-space](https://vayl.gitbook.io/vayl-docs) keys. |

## License

Apache-2.0
