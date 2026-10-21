---
description: >-
  The routes vayl-server serves, the transport your agents connect to, and how
  requests are authenticated.
icon: server
---

# Vayl HTTP API

`vayl-server` serves Vayl's MCP tools over the Model Context Protocol's **streamable HTTP** transport at `POST /mcp`, plus health probes and Prometheus metrics. There is no separate REST route per tool: every memory operation is an MCP `tools/call`. The tools themselves are documented in [MCP tools](https://vayl.gitbook.io/vayl-docs/documentation/reference/mcp-tools).

| Route          | Auth                          | Purpose                                                |
| -------------- | ----------------------------- | ------------------------------------------------------ |
| `POST /mcp`    | API key or OIDC token         | MCP JSON-RPC: `initialize`, `tools/list`, `tools/call` |
| `GET /healthz` | none                          | Liveness                                               |
| `GET /readyz`  | none                          | Readiness (checks the database)                        |
| `GET /metrics` | none, or `VAYL_METRICS_TOKEN` | Prometheus counters                                    |

## Authenticate

Send `Authorization: Bearer <credential>` on every `/mcp` request:

* **API key** (`vayl_sk_…`), created with the `create_principal` tool and shown once.
* **OIDC ID token**, with an Enterprise license granting `sso`.

The server speaks plain HTTP. Terminate TLS at a reverse proxy or ingress.

## Try it

```bash
curl -s -X POST http://127.0.0.1:8080/mcp \
  -H "Authorization: Bearer $VAYL_KEY" \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"license_status","arguments":{}}}'
```

The response is a server-sent-events stream; its `data:` line holds the JSON-RPC result.

{% hint style="info" %}
This reference is generated from [`openapi/vayl-server.yaml`](https://github.com/ac12644/vayl/blob/main/openapi/vayl-server.yaml) in the repository, which a test keeps in step with the server's routes.
{% endhint %}
