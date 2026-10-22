---
description: >-
  Call Vayl's tools from your own code: the Python and TypeScript clients, a
  raw MCP session over stdio or HTTP, and how to get an API key.
icon: code
---

# Calling Vayl from code

**Vayl's tools are MCP tools, so any MCP client can call them: the model in your agent, the small Python or TypeScript client, or a raw MCP session.** The tool names and arguments are the same on every path; only the transport and auth differ.

## The usual path: your agent calls the tools

In a chat client (Claude Desktop, Cursor, Claude Code) or an agent framework you configure Vayl once (see the [Quickstart](quickstart.md)), and the model calls `remember`, `recall`, and the rest from the tool descriptions Vayl advertises.

> **You:** Remember that Acme's account is suspended until their payment clears.

The client sends an MCP tool call like this, with arguments filled in by the model:

```json
{
  "method": "tools/call",
  "params": {
    "name": "remember",
    "arguments": { "text": "Acme's account is suspended until their payment clears", "user_id": "cust_5521" }
  }
}
```

## From Python: the `vayl` client

`pip install vayl-mcp` includes a small synchronous client, so you call methods instead of writing `tools/call` JSON.

```python
from vayl import Vayl

# Local: spawns `vayl-mcp` over stdio. The child inherits your environment
# (OPENAI_API_KEY, VAYL_DB, ...).
with Vayl(user_id="proj_7") as m:
    print(m.remember("We use Postgres as our primary database"))
    # Stored: [ADD] primary_database = Postgres   (subject name chosen by the model)
    print(m.recall("what database do we use?"))
    # model-generated, e.g. "We use Postgres."

# A team server over authenticated streamable HTTP:
with Vayl(url="https://memory.example.com/mcp", api_key="vayl_sk_...", user_id="cust_5521") as m:
    print(m.recall("what plan are they on?"))
```

| Parameter | Default | Meaning |
| --- | --- | --- |
| `url` | `None` | Team-server URL. When unset, the client uses stdio. |
| `api_key` | `None` | Sent as `Authorization: Bearer <key>` over HTTP. |
| `command`, `args` | `"vayl-mcp"`, `[]` | The stdio server to spawn. |
| `env` | your current environment | Environment for the spawned server. |
| `user_id`, `agent_id`, `run_id` | `""` | Default memory space, sent only to tools that accept it; override per call. |

`remember`, `recall`, and `forget` are named methods. Any other tool is a method too, called with keyword arguments: `m.history(subject="plan")`, `m.check_before_act(subject="plan")`, or `m.call("list_memories")`. Every method returns the tool's text, and raises `VaylError` when the tool reports an error such as "Access denied".

## From TypeScript: `@vayl.dev/client`

```bash
npm install @vayl.dev/client
```

The client spawns `vayl-mcp`, so install that too (`pip install vayl-mcp`) unless you connect to a team server.

```ts
import { Vayl } from "@vayl.dev/client";

const m = await Vayl.connect({ userId: "proj_7" });      // stdio: spawns vayl-mcp
console.log(await m.remember("We use Postgres as our primary database"));
console.log(await m.recall("what database do we use?")); // model-generated
await m.close();

// Team server:
const team = await Vayl.connect({ url: "https://memory.example.com/mcp", apiKey: "vayl_sk_...", userId: "cust_5521" });
```

`m.remember`, `m.recall`, `m.forget`, and `m.call(tool, args)` for any other tool. `withVayl(opts, async (m) => { ... })` connects, runs your function, and always closes. Options mirror the Python client: `url`, `apiKey`, `command`, `args`, `env`, `userId`, `agentId`, `runId`. Vercel AI SDK and Mastra adapters ship as `@vayl.dev/client/vercel` and `@vayl.dev/client/mastra`; see [Agent frameworks](../integrations/agent-frameworks.md).

{% hint style="info" %}
**Needs verification:** that `@vayl.dev/client` is published on npm at the version you expect. The source is in [`clients/typescript`](https://github.com/ac12644/vayl/tree/main/clients/typescript) and builds with `npm run build`.
{% endhint %}

## REST (any language, against vayl-server)

`vayl-server` serves each tool as `POST /v1/tools/<name>`. Send the arguments as a JSON object and read `result`:

```bash
curl -s https://memory.example.com/v1/tools/remember \
  -H "Authorization: Bearer vayl_sk_..." \
  -H "Content-Type: application/json" \
  -d '{"text": "Acme Corp is on the Enterprise plan, account active.", "user_id": "cust_5521"}'
```

```json
{"result":"Stored: [ADD] plan = Enterprise; [ADD] account_status = active"}
```

Errors use HTTP status codes: `401` for a bad key, `403` for a missing capability or scope, `404` for an unknown tool, `422` for a bad argument. Every tool, with its schema and a real example, is in the [HTTP API reference](https://vayl.gitbook.io/vayl-docs/api-reference).

## Raw MCP (any language, no Vayl client)

The clients above wrap a standard MCP session. In another language, or for direct control, use your MCP SDK. Python over stdio:

```python
import asyncio

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

params = StdioServerParameters(
    command="vayl-mcp",
    env={"OPENAI_API_KEY": "sk-...", "VAYL_DB": "/abs/path/vayl.db"},
)

async def main():
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        res = await session.call_tool("remember", {"text": "We use Postgres", "user_id": "proj_7"})
        print(res.content[0].text)   # Stored: [ADD] ... = Postgres
        res = await session.call_tool("recall", {"question": "what database do we use?", "user_id": "proj_7"})
        print(res.content[0].text)   # model-generated answer

asyncio.run(main())
```

Against a team server over streamable HTTP, pass the key through an `httpx2` client. This is the MCP Python SDK v2 API (`mcp>=2`, which Vayl 0.8 and later use); v1 called the function `streamablehttp_client` and took `headers=` directly.

```python
import asyncio

import httpx2
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

async def main():
    headers = {"Authorization": "Bearer vayl_sk_..."}
    async with httpx2.AsyncClient(headers=headers, timeout=120) as http, \
               streamable_http_client("https://memory.example.com/mcp", http_client=http) as (read, write), \
               ClientSession(read, write) as session:
        await session.initialize()
        res = await session.call_tool("license_status", {})
        print(res.content[0].text)

asyncio.run(main())
```

```
Community edition — up to 3 principals. No license installed.
  principals in use: 1 / 3
```

That's the reply for an admin key in the `default` tenant. Any other key gets `(seat usage is visible to the deployment operator)` in place of the second line.

Or one JSON-RPC call with curl:

```bash
curl -sN https://memory.example.com/mcp \
  -H "Authorization: Bearer vayl_sk_..." \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call",
       "params":{"name":"license_status","arguments":{}}}'
```

The reply is a server-sent event whose `data:` line holds the JSON-RPC result. A request without a valid key gets `401`.

## Getting an API key

`vayl-server` requires a key on every request. A fresh database has no principals, so create the first admin over stdio, where you run as the local admin, against the **same database** the server will use:

```bash
VAYL_DB=/abs/path/vayl.db python -c \
  "from vayl.api import mcp_server as s; print(s.create_principal('ops', role='admin'))"
```

```
Created principal prin_2d33615f4f3c 'ops' (role: admin, kind: agent, tenant: default).
  API key (shown once — save it now):
  vayl_sk_...
```

With the Docker setup, run the same thing inside the container:

```bash
docker compose run --rm -e VAYL_AUTH_REQUIRED=0 vayl python -c "from vayl.api import mcp_server as s; print(s.create_principal('admin', role='admin'))"
```

Use that admin key to create keys for people and agents with `create_principal` (roles `admin`, `member`, `agent`, `viewer`, `auditor`). See [Authentication and access](../core-concepts/authentication-and-access.md).

## Discovering the exact schema

For the exact arguments of every tool, list them from any MCP session:

```python
tools = await session.list_tools()
for t in tools.tools:
    print(t.name, t.input_schema)
```

Not every tool takes `user_id` / `agent_id` / `run_id`: principal, license, and audit-verification tools do not. The Vayl clients check this for you and only send the scope keys a tool accepts.

## Next steps

<table data-view="cards"><thead><tr><th></th><th></th><th data-hidden data-card-target data-type="content-ref"></th></tr></thead><tbody><tr><td><h4><i class="fa-database" style="color:$primary;">:database:</i> Memory tools</h4></td><td>Every tool you'll call, with arguments and returns.</td><td><a href="../mcp-tools/memory.md">memory.md</a></td></tr><tr><td><h4><i class="fa-plug" style="color:$primary;">:plug:</i> The MCP interface</h4></td><td>Transports, the call envelope, annotations, and errors.</td><td><a href="../mcp-tools/the-mcp-interface.md">the-mcp-interface.md</a></td></tr><tr><td><h4><i class="fa-server" style="color:$primary;">:server:</i> Deploying vayl-server</h4></td><td>Run the authenticated HTTP server for a team.</td><td><a href="../guides/deploying-vayl-server.md">deploying-vayl-server.md</a></td></tr></tbody></table>
