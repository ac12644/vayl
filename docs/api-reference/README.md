---
description: >-
  Call Vayl's 32 tools over plain HTTP: one REST operation per tool, JSON in
  and out, Bearer auth.
icon: server
---

# Vayl HTTP API

`vayl-server` serves every Vayl tool as a REST operation: `POST /v1/tools/<name>` with the tool's arguments as a JSON object, returning `{"result": "..."}`. The same tools are available over MCP at `POST /mcp` for MCP clients and agent frameworks. Both paths run the same code, with the same authentication, capability checks, scoping and audit log.

## Quick start

Start a server and create an API key (see [Deploying vayl-server](https://vayl.gitbook.io/vayl-docs/documentation/guides/deploying-vayl-server)), then:

```bash
curl -s localhost:8080/v1/tools/remember \
  -H "Authorization: Bearer $VAYL_KEY" \
  -H "Content-Type: application/json" \
  -d '{"text": "Acme Corp is on the Enterprise plan, account active.", "user_id": "cust_5521"}'
```

```json
{"result":"Stored: [ADD] plan = Enterprise; [ADD] account_status = active"}
```

A later fact replaces the old one, and an agent can check before it acts:

```bash
curl -s localhost:8080/v1/tools/remember -H "Authorization: Bearer $VAYL_KEY" -H "Content-Type: application/json" \
  -d '{"text": "Acme'\''s invoice bounced. Account suspended until payment clears.", "user_id": "cust_5521"}'
curl -s localhost:8080/v1/tools/check_before_act -H "Authorization: Bearer $VAYL_KEY" -H "Content-Type: application/json" \
  -d '{"subject": "account_status", "user_id": "cust_5521", "block_on_recent_change_days": 1}'
```

```json
{"result":"Stored: [ADD] acme_invoice_payment_status = bounced; [SUPERSEDE] account_status = suspended"}
{"result":"⛔ BLOCKED — do NOT act on 'account_status':\n  • recently changed 0.0d ago — may be unsettled"}
```

These are real responses from `vayl-server` with the support preset (`VAYL_SLOT_SCHEMA=preset:support`). `recall` answers are written by your model, so their wording varies.

## Endpoints

| Route | Auth | Purpose |
| --- | --- | --- |
| `POST /v1/tools/<name>` | API key or OIDC token | Run one tool. See the tool pages: [Memory](tools/memory.md), [Safety and approval](tools/safety-and-approval.md), [Accountability](tools/accountability.md), [Compliance](tools/compliance.md), [Administration](tools/administration.md). |
| `GET /v1/tools` | API key or OIDC token | Every tool with its JSON Schema, the same as MCP `tools/list`. |
| `POST /mcp` | API key or OIDC token | MCP JSON-RPC: `initialize`, `tools/list`, `tools/call`. See [MCP endpoint](mcp-endpoint.md). |
| `GET /healthz`, `GET /readyz` | none | Liveness and readiness. See [Health probes](health-probes.md). |
| `GET /metrics` | none, or `VAYL_METRICS_TOKEN` | Prometheus counters. See [Metrics](metrics.md). |

## Authentication

Send `Authorization: Bearer <credential>` on every `/v1` and `/mcp` request:

* an API key (`vayl_sk_…`), created with the [`create_principal`](tools/administration.md) tool and shown once; or
* an OIDC ID token, with an Enterprise license granting `sso`.

A key's role decides which tools it may call, and its `scopes` decide which `user_id`s it may touch. The server speaks plain HTTP, so terminate TLS at a reverse proxy or ingress.

## Status codes

| Code | Meaning | Body |
| --- | --- | --- |
| `200` | The tool ran. A tool that couldn't finish still answers `200`, with `Vayl couldn't complete that (ref …)` in `result`. | `{"result": "..."}` |
| `400` | The body isn't a JSON object. | `{"error": "..."}` |
| `401` | No credential, or one that isn't an active principal. | `{"error": "unauthorized: ..."}` |
| `403` | The key's role lacks the tool's capability, or the `user_id` is outside its scopes. The denial is audited. | `{"result": "Access denied: ..."}` |
| `404` | No tool by that name. `GET /v1/tools` lists them. | `{"error": "..."}` |
| `413` | The body is larger than `VAYL_MAX_BODY` (1 MiB by default). | `{"error": "request body too large"}` |
| `422` | An argument is missing, unknown or the wrong type. `detail` names it. | `{"error": "invalid arguments", "detail": "..."}` |
| `429` | This client IP went over `VAYL_RATE_PER_MIN` (120 by default, per process). | `{"error": "rate limit exceeded"}` |

Every response carries an `X-Request-ID` header. Send your own (1 to 64 characters of `A-Z a-z 0-9 . _ : -`) to match proxy and Vayl logs, and quote it, with any `ref`, when you report a problem.

## REST or MCP?

Use REST from backend code, scripts and anything that isn't an MCP client: it's one HTTP request per call, with no JSON-RPC envelope or event stream. Use MCP for chat clients (Claude Code, Cursor) and agent frameworks, which discover the tools and let the model call them. The Python and TypeScript clients use MCP; see [Calling Vayl from code](https://vayl.gitbook.io/vayl-docs/documentation/getting-started/calling-vayl-from-code).

{% hint style="info" %}
This reference is generated from [`openapi/vayl-server.yaml`](https://github.com/ac12644/vayl/blob/main/openapi/vayl-server.yaml), which `openapi/generate.py` builds from the server's tool registry. The examples on the tool pages are real responses, captured by `openapi/capture_examples.py`. A test fails if the spec drifts from the code.
{% endhint %}

{% openapi-operation spec="vayl-server" path="/v1/tools" method="get" %}
[OpenAPI vayl-server](https://raw.githubusercontent.com/ac12644/vayl/main/openapi/vayl-server.yaml)
{% endopenapi-operation %}
