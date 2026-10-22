---
description: All 32 MCP tools Vayl exposes, grouped by job, with the capability each one needs.
icon: toolbox
---

# MCP tools

Vayl's API is a set of 32 tools. Any MCP client connects over stdio (`vayl-mcp`) or authenticated HTTP (`vayl-server`) and calls them by name, and `vayl-server` also serves each one as `POST /v1/tools/<name>` with JSON in and out; see the [HTTP API reference](https://vayl.gitbook.io/vayl-docs/api-reference). Usually the LLM in your agent picks the tool; you can also call them from your own code, see [Calling Vayl from code](../getting-started/calling-vayl-from-code.md).

## Reading these pages

Every tool section shows:

* the **signature** with its real defaults, copied from `src/vayl/api/mcp_server.py`;
* the **capability** a caller's role must grant (see [Capabilities](#capabilities));
* the tool's **MCP annotations** (read-only, destructive, open-world);
* a **real return string**, captured by running the tool offline with the model stubbed. Where the text comes from the answering model (`recall`, `recall_related`, `safe_recall` when it answers, the `based on:` line of `record_decision`), the page says so and the example is representative only.

Over MCP you pass arguments as a JSON object:

```json
{"name": "remember", "arguments": {"text": "We use Postgres", "user_id": "proj_7"}}
```

For the exact, always-current schema of every tool, call `tools/list` (see [The MCP interface](the-mcp-interface.md)).

## Memory-space arguments

A [memory space](../core-concepts/memory-spaces.md) is the triple `(user_id, agent_id, run_id)`. Tools that read or write memory take those three arguments, defaulting to `user_id="default"`, `agent_id=""`, `run_id=""`. Not every tool does:

| Takes `user_id`, `agent_id`, `run_id` | Takes only `user_id` | Takes none |
| --- | --- | --- |
| `remember`, `recall`, `recall_related`, `forget`, `get_memory`, `update_memory`, `history`, `list_memories`, `record_decision`, `check_before_act`, `safe_recall`, `pending_changes`, `confirm_change`, `reject_change`, `set_reconcile_policy`, `get_reconcile_policy`, `attest`, `delete`, `delete_all`, `export_memory`, `purge_expired` | `explain_decision` (default `"default"`), `audit_log` (default `""` = the whole tenant) | `verify_receipt`, `verify_audit`, `export_public_key`, `create_principal`, `list_principals`, `revoke_principal`, `license_status`, `stats`, `health` |

## Capabilities

On `vayl-server` every call is checked before the tool runs, and denials are written to the audit log. Over stdio (`vayl-mcp`) the caller is a trusted local admin, unless `VAYL_AUTH_REQUIRED` is on.

| Capability | Roles that have it | Tools |
| --- | --- | --- |
| `read` | admin, member, agent, viewer | `recall`, `recall_related`, `get_memory`, `history`, `list_memories`, `check_before_act`, `safe_recall`, `pending_changes`, `get_reconcile_policy`, `export_memory` |
| `write` | admin, member, agent | `remember`, `forget`, `update_memory`, `record_decision`, `attest` |
| `delete` | admin, member | `delete` |
| `verify` | all five roles | `explain_decision`, `verify_receipt`, `verify_audit`, `export_public_key`, `audit_log`, `license_status`, `stats`, `health` |
| `approve` | admin, member | `confirm_change`, `reject_change` |
| `admin` | admin | `delete_all`, `purge_expired`, `set_reconcile_policy`, `create_principal`, `list_principals`, `revoke_principal` |

Three tools add a check of their own: `audit_log` without a `user_id` needs `admin`; `verify_receipt` only verifies receipts owned by a space in the caller's scope; `remember` with a trusted `source` needs a key named after that source, or `approve`. On a multi-tenant deployment, some admin tools also depend on the caller's tenant: see [The deployment operator](../core-concepts/authentication-and-access.md#the-deployment-operator). See [Authentication & access](../core-concepts/authentication-and-access.md) for roles, scopes and tenants.

## Groups

| Group | Tools |
| --- | --- |
| [Memory](memory.md) | `remember`, `recall`, `recall_related`, `forget`, `history`, `list_memories`, `get_memory`, `update_memory` |
| [Safety and gating](safety-and-gating.md) | `check_before_act`, `safe_recall`, `pending_changes`, `confirm_change`, `reject_change`, `set_reconcile_policy`, `get_reconcile_policy` |
| [Accountability](accountability.md) | `record_decision`, `explain_decision`, `attest`, `verify_receipt`, `audit_log`, `verify_audit`, `export_public_key` |
| [Compliance (GDPR)](compliance-gdpr.md) | `delete`, `delete_all`, `export_memory`, `purge_expired` |
| [Administration](administration.md) | `create_principal`, `list_principals`, `revoke_principal`, `license_status`, `stats`, `health` |

## Next steps

<table data-view="cards"><thead><tr><th></th><th></th><th data-hidden data-card-target data-type="content-ref"></th></tr></thead><tbody><tr><td><h4><i class="fa-plug" style="color:$primary;">:plug:</i> The MCP interface</h4></td><td>Transports, <code>tools/list</code>, the call envelope, annotations, and errors.</td><td><a href="the-mcp-interface.md">the-mcp-interface.md</a></td></tr><tr><td><h4><i class="fa-database" style="color:$primary;">:database:</i> Memory</h4></td><td>The core store-and-recall tools.</td><td><a href="memory.md">memory.md</a></td></tr><tr><td><h4><i class="fa-lock" style="color:$primary;">:lock:</i> Authentication &#x26; access</h4></td><td>Roles, capabilities, scopes, and tenants.</td><td><a href="../core-concepts/authentication-and-access.md">authentication-and-access.md</a></td></tr></tbody></table>
