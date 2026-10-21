---
description: >-
  Agent memory returns stale facts because most systems append instead of
  reconcile. The root cause, the evidence, and how to fix it.
---

# Why your agent's memory returns stale facts

**Agent memory returns stale facts because most memory systems _append_ instead of _reconcile_.** When a value changes, the old one stays searchable, and retrieval can return it as current. The fix is memory that retires the old value when the new one is written.

## The root cause

Additive and vector memory treat every statement as a new item. Change a customer's plan four times and you hold four `plan = …` memories, all retrievable. At read time the model sees several plausible "current" values and has to guess. The more often a fact changes, the more stale candidates compete.

Removal is worse. "We dropped Sentry" has no new value to rank above the old one, so an appending store often keeps returning Sentry.

## What reconciling means

Vayl decides what each new statement does to existing memory at write time:

* A new value for the same fact **supersedes** the old one. The old value moves to history.
* A removal **retracts** the fact. It leaves the active set and is kept in history as a tombstone.
* An ambiguous change is **flagged** for review instead of guessed.

A normal `recall` reads only the active set, so a retired value cannot come back as current however the answering model behaves. History is opt-in (`include_history=True` or the `history` tool). The full list of actions is in [Reconciliation](../core-concepts/core-concepts.md).

## Why prompting the model is not enough

Reconciling by prompt depends on the model noticing the contradiction every time, across a long context. Vayl enforces it in the store: for a single-valued fact there is at most one active value per `(subject, scope)`, so two contradictory values cannot both be current. The model still has to extract the fact and name its slot consistently; [declared slots](../core-concepts/core-concepts.md) make that part deterministic for the fields you care about.

## What the benchmarks show

Two small, author-run benchmarks, both with `gpt-4o-mini`. The metric is **silently wrong**: the system returns a stale or retracted value as current.

| Benchmark | Vayl | Mem0 | Graphiti |
| --- | --- | --- | --- |
| [Reconciliation comparison](https://github.com/ac12644/vayl/blob/main/benchmarks/results/compare_systems.md) (10 scenarios) | 0/10 silently wrong, 10/10 correct | 1/10 silently wrong, 2/10 missed | 0/10 silently wrong, 6/10 missed |
| [Retraction battery](https://github.com/ac12644/vayl/blob/main/benchmarks/results/retraction_battery.md) (12 retractions + 2 controls) | 0/14 silently wrong, 12/12 retractions correct | 1/14 silently wrong, 11/12 | 3/14 silently wrong, 10/12 |

{% hint style="warning" %}
These are single runs on small suites written by the Vayl author. Treat them as a reproducible demonstration, not a general accuracy claim. The scripts and per-case outputs are in `benchmarks/` in the repo.
{% endhint %}

## The three shapes of agent memory

| Shape | Strength | Weakness under change |
| --- | --- | --- |
| Additive / vector | Simple, broad recall | Old values stay retrievable; removal is hard to express |
| Temporal knowledge graph | Reconciles and answers multi-hop questions | Needs a graph database server; in the comparison above it missed more updates |
| Reconciling (Vayl) | Supersede, retract, and flag on write; history kept | Built for changing facts, not for Q&A over a static document corpus |

Vayl runs on a single SQLite file by default, with Postgres for a shared team server and an optional Neo4j projection for relational questions.

## How to fix it

Point your MCP client at Vayl. `remember` extracts and reconciles facts, `recall` answers from the active set or says "I don't know", and `history` shows what changed. Keep your existing RAG for static documents; Vayl holds the facts that change.

## Next steps

<table data-view="cards"><thead><tr><th></th><th></th><th data-hidden data-card-target data-type="content-ref"></th></tr></thead><tbody><tr><td><h4><i class="fa-bolt" style="color:$primary;">:bolt:</i> Quickstart</h4></td><td>Point an MCP client at Vayl and store your first fact.</td><td><a href="../getting-started/quickstart.md">quickstart.md</a></td></tr><tr><td><h4><i class="fa-diagram-project" style="color:$primary;">:diagram-project:</i> How Vayl works</h4></td><td>The write and read paths end to end.</td><td><a href="../how-vayl-works.md">how-vayl-works.md</a></td></tr><tr><td><h4><i class="fa-database" style="color:$primary;">:database:</i> Memory tools</h4></td><td><code>remember</code>, <code>recall</code>, <code>history</code>, and the rest.</td><td><a href="../mcp-tools/memory.md">memory.md</a></td></tr></tbody></table>
