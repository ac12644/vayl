---
description: >-
  Vayl is memory for AI agents where a wrong answer has consequences: one
  active value per fact, real removal, and a verifiable history.
---

# Memory for AI agents that must not be silently wrong

**Vayl is memory for agents where a stale answer has consequences.** It reconciles on write, so a changed or removed fact is retired instead of left to compete at retrieval time, and it records every change on a signed, hash-chained audit trail. Why appending memory fails is covered once, in [Why your agent's memory returns stale facts](why-your-agents-memory-returns-stale-facts.md). This page lists what Vayl guarantees and where the guarantees stop.

## What Vayl guarantees

* **One active value per single-valued fact.** At most one active value per `(subject, scope)`, enforced by the store, not by the prompt. Two exceptions hold several active values on purpose: declared list slots (`multi`) and events. Different scopes of one subject (web vs. mobile) are separate facts.
* **Removal is real.** `forget("We dropped Sentry")` retires the fact. A normal `recall` does not load retired facts, so it cannot return Sentry as current.
* **History is kept.** Superseded and retracted values stay queryable through `history` or `recall(..., include_history=True)`. They are never returned by a normal recall.
* **Changes are verifiable.** The audit trail is hash-chained and Ed25519-signed by default; `verify_audit` detects an edited, reordered, or truncated log. Signing can be turned off with `VAYL_SIGN=off`, and then this guarantee is gone.

## What it depends on

* **Extraction is done by an LLM.** The model turns text into `subject = value` facts and picks the subject name. If it names the same fact two ways, the store sees two facts. [Declared slots](../core-concepts/core-concepts.md) (or a built-in preset such as `VAYL_SLOT_SCHEMA=preset:coding`) fix the names for the fields you care about.
* **Answers are written by an LLM.** `recall` returns free text generated from the active facts. It can be phrased differently each time; it cannot draw on a retired fact.

## How it runs

| | |
| --- | --- |
| Storage | One SQLite file for the local stdio server (`vayl-mcp`). Postgres (`VAYL_DATABASE_URL`) for a shared `vayl-server`. |
| Outbound calls | The LLM and the embedder you configure. Also HashiCorp Vault, your OIDC provider's JWKS, and Neo4j, only when you enable them. No telemetry. |
| At rest | Fact content is encrypted by default (`VAYL_ENCRYPT`). The key is a file next to the database unless you set a `VAYL_KEY` passphrase or use Vault. |

## Not for you if

You need Q&A over a static document corpus (that is RAG), or deep multi-hop graph queries as your main workload (a dedicated graph database does that better). Vayl keeps changing facts current and sits alongside those tools.

## Next steps

<table data-view="cards"><thead><tr><th></th><th></th><th data-hidden data-card-target data-type="content-ref"></th></tr></thead><tbody><tr><td><h4><i class="fa-bolt" style="color:$primary;">:bolt:</i> Quickstart</h4></td><td>Install Vayl and store your first reconciled fact.</td><td><a href="../getting-started/quickstart.md">quickstart.md</a></td></tr><tr><td><h4><i class="fa-shield-halved" style="color:$primary;">:shield-halved:</i> For high-stakes agents</h4></td><td>Human approval, critical facts, and signed decisions.</td><td><a href="auditable-gated-memory-for-high-stakes-ai-agents.md">auditable-gated-memory-for-high-stakes-ai-agents.md</a></td></tr><tr><td><h4><i class="fa-book" style="color:$primary;">:book:</i> Reconciliation</h4></td><td>Actions, statuses, and the same-slot invariant.</td><td><a href="../core-concepts/core-concepts.md">core-concepts.md</a></td></tr></tbody></table>
