---
description: >-
  For agents where a wrong answer causes harm, memory must be correct under
  change, gated by a person, and provable afterwards. What Vayl provides.
---

# Auditable, gated memory for high-stakes AI agents

**When a wrong answer causes harm (clinical, financial, legal), agent memory needs three things: it stays correct when facts change, a person approves risky changes, and every decision can be reconstructed later.** Vayl provides controls for all three. Correct-under-change is the reconciling core described in [Why your agent's memory returns stale facts](why-your-agents-memory-returns-stale-facts.md); this page covers the rest.

The built-in presets `preset:clinical` and `preset:finance` declare typical gated and critical slots. Load one with `VAYL_SLOT_SCHEMA=preset:clinical` and set `VAYL_CRITICAL_CATEGORIES=critical` so its critical slots are always surfaced (see below).

## A person approves risky changes

* **Confirm-gated slots.** Declare a slot with `"confirm": true` (for example `active_medication`). A replacement, a removal, or an addition to a slot that already holds a value is then stored as a proposal and the current value stands. `pending_changes` lists the queue; `confirm_change` applies a proposal and `reject_change` discards it.
* **Approval needs the `approve` capability.** Since 0.6.0, only the `admin` and `member` roles have it, so an `agent` key cannot approve its own proposal. The recorded approver is the authenticated caller (`name [id]`); `decided_by` is only a note beside it.
* **Trusted sources are bound to keys.** A write whose `source` is listed in `VAYL_TRUSTED_SOURCES` skips the confirmation gate. Since 0.6.0 it is accepted only from a key whose principal name equals that source, or from a caller with `approve`. Anything else gets "Access denied" and is audited.

{% hint style="warning" %}
The local stdio server (`vayl-mcp`) runs as a local admin unless `VAYL_AUTH_REQUIRED` is on. Separating people from agents needs keys: run `vayl-server`, or turn auth on. See [Authentication and access](../core-concepts/authentication-and-access.md).
{% endhint %}

## Critical facts are never ranked out

Ordinary recall is semantic top-k, so a fact that does not rank is not seen. For categories where an omission is a safety failure (allergies, key covenants), name them as critical:

* deployment-wide with `VAYL_CRITICAL_CATEGORIES` (comma-separated), or
* per call with `recall(..., critical_categories="allergy,active_medication")`. An empty value falls back to the deployment setting; it never means "none".

A fact is critical when its `metadata.category`, or the category of its declared slot, matches one of those names. Critical facts are kept out of ranking and always added to the answer context. If more than `VAYL_CRITICAL_BUDGET` (default 200) facts match, recall raises an error instead of silently truncating them.

## Act-time checks

`check_before_act(subject)` returns SAFE, or BLOCKED with reasons (an unresolved conflict, low confidence, a stale value, or a value that just changed). `safe_recall` answers only when every fact behind the answer passes the same policy, and otherwise withholds the answer with the reasons. Call one of them before an irreversible action.

## Provable after the fact

* **Decision snapshots.** `record_decision` stores an action with the exact facts the agent used; `explain_decision` reconstructs it later and verifies it was not altered.
* **Tamper-evident audit chain.** Hash-chained and Ed25519-signed by default (`VAYL_SIGN=off` disables signing). `verify_audit` detects edits, reordering, and truncation, and `export_public_key` lets a third party verify offline.
* **Erasure with receipts.** `delete` and `delete_all` hard-erase a subject or a whole space and return a signed receipt. Since 0.4 erasure fails closed: the Neo4j graph purge runs first, and if it fails nothing is erased and no receipt is issued. `export_memory` supports access requests.

## Boundaries

Vayl is built to run in your environment, and its only outbound calls are to services you configure. It has **not** been independently audited or penetration-tested, and it does not make you compliant with any regulation. It gives you controls; your security and data-protection review decides whether they are enough.

## Next steps

<table data-view="cards"><thead><tr><th></th><th></th><th data-hidden data-card-target data-type="content-ref"></th></tr></thead><tbody><tr><td><h4><i class="fa-hospital" style="color:$primary;">:hospital:</i> Tutorial: a hospital assistant</h4></td><td>A gated, auditable clinical assistant, end to end.</td><td><a href="../guides/tutorial-a-hospital-medication-assistant.md">tutorial-a-hospital-medication-assistant.md</a></td></tr><tr><td><h4><i class="fa-gear" style="color:$primary;">:gear:</i> Safety gates &#x26; human approval</h4></td><td>Confirm gates, critical facts, and act-time checks in depth.</td><td><a href="../guides/safety-gates-and-human-approval.md">safety-gates-and-human-approval.md</a></td></tr><tr><td><h4><i class="fa-file-signature" style="color:$primary;">:file-signature:</i> Accountability</h4></td><td>Signed decisions, attestations, and the audit chain.</td><td><a href="../mcp-tools/accountability.md">accountability.md</a></td></tr></tbody></table>
