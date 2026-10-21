---
description: >-
  Connect to Vayl over stdio or HTTP, discover its tools, call them, and read
  results, annotations, and errors.
icon: plug
---

# The MCP interface

Vayl's API is its Model Context Protocol surface: 32 tools that any MCP client can discover and call. This page covers the protocol-level details: transports, credentials, discovery, the call envelope, annotations, and errors. Each tool's arguments are on its group page. For raw HTTP routes and status codes, see [HTTP endpoints](../reference/http-endpoints.md).

## Transports

| Transport | Command | Caller identity |
| --- | --- | --- |
| stdio | `vayl-mcp` | One local user. The client launches the process and talks over stdin/stdout. The caller is a trusted local **admin**, unless `VAYL_AUTH_REQUIRED` is on, in which case every tool call is denied (stdio has no way to present a key). |
| streamable HTTP | `vayl-server` | A shared team deployment. Requests go to `POST /mcp`, each with its own credential. `vayl-server` always requires auth. |

`vayl-server` runs the MCP app in **stateless** mode: every request is handled on its own, with no session carrying identity between requests. That is why every request must present a credential.

## Credentials (HTTP only)

Every request to `/mcp` must carry one of:

* an API key: `Authorization: Bearer vayl_sk_…`, issued by [`create_principal`](administration.md#create_principal);
* an OIDC JWT: `Authorization: Bearer <jwt>`, accepted only when OIDC is configured (`VAYL_OIDC_ISSUER`, `VAYL_OIDC_AUDIENCE`, …) **and** the license grants the `sso` feature. API keys keep working either way.

A missing or invalid credential gets `401` before any tool runs:

```
HTTP/1.1 401 Unauthorized
content-type: application/json
www-authenticate: Bearer

{"error":"unauthorized: send Authorization: Bearer vayl_sk_..."}
```

`/healthz` and `/readyz` are unauthenticated. `/metrics` is unauthenticated unless `VAYL_METRICS_TOKEN` is set, in which case it needs `Authorization: Bearer <that token>`. See [HTTP endpoints](../reference/http-endpoints.md).

## Discover the tools with tools/list

`tools/list` returns the authoritative list of tools with their argument schemas:

```json
{ "jsonrpc": "2.0", "id": 1, "method": "tools/list" }
```

Each entry has `name`, `description`, `inputSchema` (JSON Schema of the arguments, with defaults), and `annotations`. Against a 0.7.0 server it returns 32 tools. Prefer it over any static list when you need exact argument names.

## Call a tool with tools/call

Pass the arguments as a JSON object under `params.arguments`:

```json
{
  "jsonrpc": "2.0", "id": 2, "method": "tools/call",
  "params": {
    "name": "remember",
    "arguments": { "text": "Acme's invoice bounced. Account suspended until payment clears.", "user_id": "cust_5521" }
  }
}
```

Every Vayl tool returns one text string. The MCP result wraps it in `content`, and FastMCP also mirrors it in `structuredContent.result`:

```json
{
  "jsonrpc": "2.0", "id": 2,
  "result": {
    "_meta": {"fastmcp": {"wrap_result": true}},
    "content": [{"text": "Stored: [ARCHIVE] acme_invoice_payment_status = bounced; [SUPERSEDE] account_status = suspended", "type": "text"}],
    "isError": false,
    "structuredContent": {"result": "Stored: [ARCHIVE] acme_invoice_payment_status = bounced; [SUPERSEDE] account_status = suspended"}
  }
}
```

In a client SDK, read `result.content[0].text`. The subject name (`state` here) is chosen by the extraction model, so yours may differ.

{% hint style="info" %}
Over HTTP the response body is Server-Sent Events: an `event: message` line followed by a `data: {…}` line holding the JSON-RPC message above. Send `Accept: application/json, text/event-stream`, or the server answers `406 Not Acceptable`. A full curl session is in [HTTP endpoints](../reference/http-endpoints.md#call-a-tool-with-curl).
{% endhint %}

## Safety annotations

Each tool carries MCP `ToolAnnotations` so a client can auto-run safe tools and ask before risky ones. These are the values `tools/list` reports:

| Annotation | Meaning | Tools |
| --- | --- | --- |
| `readOnlyHint: true` | Does not change memory (the call is still audited and counted). | `recall`, `recall_related`, `get_memory`, `history`, `list_memories`, `check_before_act`, `safe_recall`, `pending_changes`, `get_reconcile_policy`, `explain_decision`, `verify_receipt`, `verify_audit`, `export_public_key`, `audit_log`, `export_memory`, `list_principals`, `license_status`, `stats`, `health` (19) |
| `readOnlyHint: false`, `destructiveHint: false` | Writes, but nothing is lost: supersede and retract keep history. | `remember`, `forget`, `update_memory`, `record_decision`, `attest`, `set_reconcile_policy`, `confirm_change`, `reject_change`, `create_principal` (9) |
| `destructiveHint: true` (also `idempotentHint: true`) | Irreversible. | `delete`, `delete_all`, `purge_expired`, `revoke_principal` (4) |
| `openWorldHint: true` | Declared as talking to an external service (the LLM or embedder). | `remember`, `recall`, `recall_related`, `forget`, `update_memory`, `record_decision`, `safe_recall`, `health` (8) |

Every tool also has a human-readable `title`, for example `"Remember (extract & reconcile facts)"`.

## Capabilities and scoping

On `vayl-server` every call is checked twice before the tool body runs, fail-closed:

1. **Capability.** The caller's role must grant the tool's capability (`read`, `write`, `delete`, `verify`, `approve`, or `admin`). The per-tool table is in [MCP tools](README.md#capabilities).
2. **Scope.** For tools that take a `user_id`, a key created with `scopes` may only touch those `user_id`s. Admin keys are unrestricted. Tenants are a separate, harder partition: every store query is filtered by the key's tenant.

See [Authentication & access](../core-concepts/authentication-and-access.md).

## Errors

Tool-level problems come back as a normal text result, never as a crash:

| Situation | Returned text |
| --- | --- |
| No principal (stdio with `VAYL_AUTH_REQUIRED` on) | `Access denied: authentication required. Provide a valid API key (Authorization: Bearer vayl_sk_…).` |
| Role lacks the capability | `Access denied: 'confirm_change' requires the 'approve' capability; your role(s) ['agent'] do not grant it.` |
| `user_id` outside the key's scope | `Access denied: 'recall' targets a memory space outside your assigned scope.` (the requested `user_id` is deliberately not echoed) |
| Missing LLM configuration | `Vayl config error: missing 'OPENAI_API_KEY'. Set OPENAI_API_KEY (or ANTHROPIC_API_KEY / GROQ_API_KEY) and LLM_PROVIDER in the server's env, then restart.` |
| Any other failure | `Vayl couldn't complete that (ref 1a2b3c4d). Retry if it was a transient blip; otherwise the full detail is in the server logs under that reference.` |

Every denial is recorded in the audit log as `access_denied`. HTTP-level failures (`401`, `406`, `413`, `429`) and JSON-RPC protocol errors happen before a tool runs; see [HTTP endpoints](../reference/http-endpoints.md).

## Next steps

<table data-view="cards"><thead><tr><th></th><th></th><th data-hidden data-card-target data-type="content-ref"></th></tr></thead><tbody><tr><td><h4><i class="fa-database" style="color:$primary;">:database:</i> Memory</h4></td><td>Each tool's arguments, starting with the core store-and-recall set.</td><td><a href="memory.md">memory.md</a></td></tr><tr><td><h4><i class="fa-network-wired" style="color:$primary;">:network-wired:</i> HTTP endpoints</h4></td><td>Routes, status codes, and a verified curl session.</td><td><a href="../reference/http-endpoints.md">http-endpoints.md</a></td></tr><tr><td><h4><i class="fa-code" style="color:$primary;">:code:</i> Calling Vayl from code</h4></td><td>Runnable stdio and HTTP clients.</td><td><a href="../getting-started/calling-vayl-from-code.md">calling-vayl-from-code.md</a></td></tr></tbody></table>
