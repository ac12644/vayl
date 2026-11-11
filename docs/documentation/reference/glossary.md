---
description: Short definitions of the terms used across the Vayl docs.
icon: bookmark
---

# Glossary

**Attestation / receipt:** A signed record of what was known (`attest`) or erased (`delete`, `delete_all`). Anyone can check it with `verify_receipt` and the public key from `export_public_key`.

**Audit chain:** The log of every operation, including denied calls. Each row is hash-chained to the one before it. With signing on (`VAYL_SIGN`, default on), each entry is also Ed25519-signed and a signed head checkpoint detects truncation. `verify_audit` checks the chain.

**Capability:** A permission a tool requires: `read`, `write`, `delete`, `verify`, `approve` or `admin`. Roles grant capabilities. `approve` (held by `admin` and `member`) is needed to confirm or reject a pending change.

**Critical fact:** A fact whose `category` is listed in the operator's `VAYL_CRITICAL_CATEGORIES`, or in a call's `critical_categories`. It skips ranking and always reaches the recall context, up to `VAYL_CRITICAL_BUDGET`.

**Declared slot:** A slot defined in the slot schema (`VAYL_SLOT_SCHEMA`) with a canonical name, aliases, and optional `category`, `multi`, `confirm` and `verbatim` settings.

**Deployment operator:** An admin of the `default` tenant, including the local stdio admin. Only the operator reaches across tenants: it creates, lists and revokes principals in every tenant, sees seat usage in `license_status`, reads the audit log across tenants, and may purge it with `include_audit`. An admin of any other tenant runs only that tenant, and `stats` is denied to every caller outside `default`.

**Event vs. state:** State holds until it is replaced and obeys the same-slot invariant. An event happened at a point in time and is never superseded. The kind is stored in the fact's metadata.

**Flag:** The reconcile action that stores a fact as `FLAGGED_CONFLICT` for a person to review, instead of applying it.

**Hot path:** The rows loaded for writes and ordinary recall: `ACTIVE` and `FLAGGED_CONFLICT` only. Retired rows stay on disk, so they can't be returned as current.

**Memory space:** An isolated store keyed by `tenant` plus `(user_id, agent_id, run_id)`. Reconciliation and recall never cross spaces.

**Pending change:** A proposed replacement, removal or addition on a `confirm` slot. It is held as `FLAGGED_CONFLICT` until someone with `approve` calls `confirm_change` or `reject_change`. `pending_changes` lists them.

**Preset:** A built-in slot schema enabled with `VAYL_SLOT_SCHEMA=preset:<name>`: `assistant`, `clinical`, `coding`, `finance`, `sales` or `support`.

**Principal:** An authenticated identity (a person, agent or service) with an API key or SSO token, a role, optional scopes, and a tenant.

**Reconcile policy:** How a shared space resolves conflicts between different sources: `RECENCY` (default), `AUTHORITY` or `REVIEW`. Set per space with `set_reconcile_policy`, which requires `admin`.

**Reconciliation:** Deciding, on every write, how a new fact relates to stored facts. The result is one of nine actions: `ADD`, `DEDUP`, `REFINE`, `SUPERSEDE`, `COEXIST`, `FLAG`, `SKIP`, `ARCHIVE` or `RETRACT`.

**Retract:** Remove a fact with no replacement. The fact becomes `SUPERSEDED` and a tombstone is written, so recall returns nothing current for it.

**Same-slot invariant:** The engine rule that a single-valued slot holds at most one `ACTIVE` value. Declared list slots (`multi`) and events are the exceptions.

**Scope (of a principal):** The `user_id`s a principal may touch. Empty means unrestricted. Scopes don't restrict `agent_id` or `run_id`, and `admin` ignores them.

**Scope (of a fact):** A qualifier inside a space, such as `global`, `web` or `mobile`. The same subject in two scopes is two slots, which is what `COEXIST` records.

**Slot:** A fact's `(subject, scope)` within a memory space.

**Source:** Who or what asserted a fact, passed as `source` to `remember`. Reconcile policies and trusted sources use it.

**Status:** The state of a stored fact: `ACTIVE` (current), `FLAGGED_CONFLICT` (held for review), `SUPERSEDED` (replaced or retracted) or `HISTORICAL` (past fact, tombstone or rejected proposal).

**Supersede:** Replace a value. The old fact becomes `SUPERSEDED` and stays in history.

**Tenant:** The hard organization partition, taken from the calling principal (`create_principal(..., tenant=…)`). Every memory query filters by it, and the default is `default`.

**Tombstone:** The `HISTORICAL` row a retraction writes, with value `(retracted: <old value>)`. It records that the fact was removed and when.

**Trusted source:** A source listed in `VAYL_TRUSTED_SOURCES` whose changes skip the confirmation gate. Only a key named after that source, or a caller with `approve`, may write as it.

## Next steps

<table data-view="cards"><thead><tr><th></th><th></th><th data-hidden data-card-target data-type="content-ref"></th></tr></thead><tbody><tr><td><h4><i class="fa-book" style="color:$primary;">:book:</i> Reconciliation</h4></td><td>These terms in context: actions, statuses and the invariant.</td><td><a href="../core-concepts/core-concepts.md">core-concepts.md</a></td></tr><tr><td><h4><i class="fa-sliders" style="color:$primary;">:sliders:</i> Configuration</h4></td><td>The environment variables behind these terms.</td><td><a href="configuration.md">configuration.md</a></td></tr><tr><td><h4><i class="fa-toolbox" style="color:$primary;">:toolbox:</i> MCP tools</h4></td><td>The tools that put these concepts to work.</td><td><a href="../mcp-tools/README.md">README.md</a></td></tr></tbody></table>
