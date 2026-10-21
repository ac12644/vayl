---
description: The routes vayl-server serves, their auth, status codes, and a verified curl session.
icon: network-wired
---

# HTTP endpoints

`vayl-server` serves MCP over streamable HTTP at `POST /mcp`, plus three unauthenticated-by-default operational routes. Every response carries an `X-Request-ID` header. Run it behind a reverse proxy that terminates TLS; don't expose it directly.

| Route | Auth | Purpose |
| --- | --- | --- |
| `POST /mcp` | Bearer API key or OIDC JWT | MCP JSON-RPC (stateless streamable HTTP). |
| `GET /healthz` | none | Liveness. |
| `GET /readyz` | none | Readiness: the database answers. |
| `GET /metrics` | none, or Bearer `VAYL_METRICS_TOKEN` when set | Prometheus counters. |

Any other path is also behind auth, so an unauthenticated request gets `401` rather than `404`.

## Request pipeline

Every request passes through three layers, in this order, before a tool runs:

1. **Request ID.** An incoming `X-Request-ID` is reused if it matches `[A-Za-z0-9._:-]{1,64}`; anything else is replaced with a random 16-hex-character id. The id is echoed in the `X-Request-ID` response header and stamped on every log line for the request, so a tool's `ref` can be traced to its HTTP request.
2. **Limits**, applied before auth so floods are cheap to shed:
   * Body larger than `VAYL_MAX_BODY` (default 1 MiB), by `Content-Length` or by counting a chunked body: `413` with `{"error": "request body too large"}`.
   * More than `VAYL_RATE_PER_MIN` (default 120) requests in 60 seconds from one client IP: `429` with `{"error": "rate limit exceeded"}`. The limit is in memory and per process; `0` turns it off. Behind proxies, set `VAYL_TRUSTED_PROXY_HOPS` so the client IP is read from `X-Forwarded-For`.
3. **Auth** (everything except `/healthz`, `/readyz`, `/metrics`): a missing or invalid credential gets `401`.

The MCP app also enforces DNS-rebinding protection: the `Host` header must match `VAYL_ALLOWED_HOSTS` (default `127.0.0.1:*`, `localhost:*`, `[::1]:*`) and a browser `Origin` must match `VAYL_ALLOWED_ORIGINS`. A server bound to `0.0.0.0` behind a proxy must set `VAYL_ALLOWED_HOSTS` to its public host, or legitimate requests are rejected. See [Configuration](configuration.md).

## POST /mcp

The MCP endpoint. It runs in **stateless** mode: no session id is issued or required, and each request authenticates on its own.

**Credentials.** `Authorization: Bearer vayl_sk_…` (a key from `create_principal`), or `Authorization: Bearer <OIDC JWT>` when `VAYL_OIDC_*` is configured and the license grants `sso`.

**Headers.** Send `Content-Type: application/json` and `Accept: application/json, text/event-stream`. With any other `Accept` the server returns `406`:

```json
{"jsonrpc":"2.0","id":"server-error","error":{"code":-32600,"message":"Not Acceptable: Client must accept both application/json and text/event-stream"}}
```

**Responses** are Server-Sent Events (`content-type: text/event-stream`): an `event: message` line, then a `data:` line holding the JSON-RPC response.

**Unauthorized** (missing, malformed, revoked or unknown credential):

```
HTTP/1.1 401 Unauthorized
content-type: application/json
www-authenticate: Bearer

{"error":"unauthorized: send Authorization: Bearer vayl_sk_..."}
```

Authorization beyond that (capability and scope) happens inside the tool and comes back as an `Access denied: …` text result with HTTP `200`; see [The MCP interface](../mcp-tools/the-mcp-interface.md#errors).

## Call a tool with curl

This session was run against a real `vayl-server` 0.8.0 on a temporary database, with the key redacted.

{% stepper %}
{% step %}
### Create an admin key

A fresh database has no principals. Create one against the same `VAYL_DB` the server will use:

```bash
export VAYL_DB=/tmp/vayl-demo/vayl.db VAYL_ENCRYPT=off
python -c "from vayl.api import mcp_server as s; print(s.create_principal('ops', role='admin'))"
```

```
Created principal prin_… 'ops' (role: admin, kind: agent, tenant: default).
  API key (shown once — save it now):
  vayl_sk_…
```
{% endstep %}

{% step %}
### Start the server

```bash
VAYL_PORT=8080 vayl-server
```
{% endstep %}

{% step %}
### Initialize

```bash
curl -si -X POST http://127.0.0.1:8080/mcp \
  -H "Authorization: Bearer $VAYL_KEY" \
  -H 'content-type: application/json' \
  -H 'accept: application/json, text/event-stream' \
  -H 'X-Request-ID: docs-demo-1' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"curl","version":"0"}}}'
```

```
HTTP/1.1 200 OK
cache-control: no-cache, no-transform
connection: keep-alive
content-type: text/event-stream
x-accel-buffering: no
x-request-id: docs-demo-1
Transfer-Encoding: chunked

event: message
data: {"jsonrpc":"2.0","id":1,"result":{"protocolVersion":"2025-06-18","capabilities":{"logging":{},"prompts":{"listChanged":true},"resources":{"subscribe":false,"listChanged":true},"tools":{"listChanged":true}},"serverInfo":{"name":"vayl","version":"0.8.0"}}}
```

`serverInfo.version` is Vayl's version, the same as `vayl-server --version` prints. Because the server is stateless, you can call `tools/list` or `tools/call` without initializing first.
{% endstep %}

{% step %}
### Call a tool

```bash
curl -si -X POST http://127.0.0.1:8080/mcp \
  -H "Authorization: Bearer $VAYL_KEY" \
  -H 'content-type: application/json' \
  -H 'accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"list_memories","arguments":{"user_id":"proj_7"}}}'
```

```
HTTP/1.1 200 OK
content-type: text/event-stream
x-request-id: 5bfe3bb7bab2c7bb

event: message
data: {"jsonrpc":"2.0","id":2,"result":{"_meta":{"fastmcp":{"wrap_result":true}},"content":[{"type":"text","text":"(no memories yet)"}],"structuredContent":{"result":"(no memories yet)"},"isError":false}}
```

To pull out the JSON, strip the SSE prefix: `… | sed -n 's/^data: //p' | jq`. A `tools/list` call the same way returns 32 tools.
{% endstep %}
{% endstepper %}

## GET /healthz

Liveness. Always `200` while the process serves requests; it checks nothing else.

```
HTTP/1.1 200 OK
content-type: application/json

{"status":"ok"}
```

## GET /readyz

Readiness: runs `SELECT 1` against the database.

```
HTTP/1.1 200 OK
content-type: application/json

{"status":"ready"}
```

If the database doesn't answer, it returns `503` with `{"status":"not-ready"}` and no error detail (the route is unauthenticated).

## GET /metrics

Prometheus text format (`content-type: text/plain; version=0.0.4`). It carries no memory content, but it does reveal usage volume and the number of active principals, so keep it on an internal network.

When `VAYL_METRICS_TOKEN` is set, the route requires `Authorization: Bearer <token>` (compared in constant time). Without it:

```
HTTP/1.1 401 Unauthorized
content-type: text/plain; charset=utf-8
www-authenticate: Bearer

unauthorized
```

With the token (or when no token is configured):

```
# TYPE vayl_tool_calls_total counter
# TYPE vayl_tool_errors_total counter
# TYPE vayl_tool_latency_avg_ms gauge
# TYPE vayl_action_total counter
# TYPE vayl_principals_active gauge
vayl_tool_calls_total{tool="create_principal"} 1
vayl_tool_errors_total{tool="create_principal"} 0
vayl_tool_latency_avg_ms{tool="create_principal"} 1.5
vayl_tool_calls_total{tool="list_memories"} 1
vayl_tool_errors_total{tool="list_memories"} 0
vayl_tool_latency_avg_ms{tool="list_memories"} 1.1
vayl_principals_active 1
vayl_license_edition_info{edition="community"} 1
```

| Metric | Type | Labels | Meaning |
| --- | --- | --- | --- |
| `vayl_tool_calls_total` | counter | `tool` | Calls per tool, including denied calls. |
| `vayl_tool_errors_total` | counter | `tool` | Failed calls per tool. |
| `vayl_tool_latency_avg_ms` | gauge | `tool` | Average call latency in milliseconds. |
| `vayl_action_total` | counter | `action` | Reconciliation actions taken (`ADD`, `SUPERSEDE`, `RETRACT`, …). Appears once any action has been recorded. |
| `vayl_principals_active` | gauge | none | Active (non-revoked) principals. |
| `vayl_license_edition_info` | info (value `1`, no `# TYPE` line) | `edition` | The running license edition. |

The counters come from the deployment's metrics tables, so they cover every process sharing the database, and `stdio` calls too.

## Next steps

<table data-view="cards"><thead><tr><th></th><th></th><th data-hidden data-card-target data-type="content-ref"></th></tr></thead><tbody><tr><td><h4><i class="fa-plug" style="color:$primary;">:plug:</i> The MCP interface</h4></td><td>Discovery, the call envelope, annotations, and tool-level errors.</td><td><a href="../mcp-tools/the-mcp-interface.md">the-mcp-interface.md</a></td></tr><tr><td><h4><i class="fa-globe" style="color:$primary;">:globe:</i> Deploying vayl-server</h4></td><td>TLS, Docker, Postgres, and the proxy settings above.</td><td><a href="../guides/deploying-vayl-server.md">deploying-vayl-server.md</a></td></tr><tr><td><h4><i class="fa-sliders" style="color:$primary;">:sliders:</i> Configuration</h4></td><td>Every <code>VAYL_*</code> variable referenced on this page.</td><td><a href="configuration.md">configuration.md</a></td></tr></tbody></table>
