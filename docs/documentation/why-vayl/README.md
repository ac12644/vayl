---
description: >-
  Where Vayl fits, why appending memory goes stale, and who needs reconciling,
  auditable memory most.
icon: lightbulb
---

# Why Vayl

**Vayl is the reconciling memory layer for AI agents: when a fact changes, the old value is retired on write, so your agent is not handed a value that used to be true.** Most agent memory appends every statement and leaves retrieval to pick among old and new values. The full argument, with the benchmark behind it, is in [Why your agent's memory returns stale facts](why-your-agents-memory-returns-stale-facts.md).

These pages cover the root cause, the guarantees Vayl makes (and where they stop), and what high-stakes agents need on top.

<table data-view="cards"><thead><tr><th></th><th></th><th data-hidden data-card-target data-type="content-ref"></th></tr></thead><tbody><tr><td><h4><i class="fa-clock-rotate-left" style="color:$primary;">:clock-rotate-left:</i> Why memory goes stale</h4></td><td>The root cause, append vs. reconcile, and what the benchmarks show.</td><td><a href="why-your-agents-memory-returns-stale-facts.md">why-your-agents-memory-returns-stale-facts.md</a></td></tr><tr><td><h4><i class="fa-circle-check" style="color:$primary;">:circle-check:</i> Memory that is never silently wrong</h4></td><td>The guarantees: one active value per fact, real removal, provable history.</td><td><a href="memory-for-ai-agents-that-cant-be-wrong.md">memory-for-ai-agents-that-cant-be-wrong.md</a></td></tr><tr><td><h4><i class="fa-shield-halved" style="color:$primary;">:shield-halved:</i> For high-stakes agents</h4></td><td>Human approval, critical facts, signed decisions, and erasure.</td><td><a href="auditable-gated-memory-for-high-stakes-ai-agents.md">auditable-gated-memory-for-high-stakes-ai-agents.md</a></td></tr></tbody></table>

## Next steps

<table data-view="cards"><thead><tr><th></th><th></th><th data-hidden data-card-target data-type="content-ref"></th></tr></thead><tbody><tr><td><h4><i class="fa-bolt" style="color:$primary;">:bolt:</i> Quickstart</h4></td><td>Install Vayl and store your first reconciled fact.</td><td><a href="../getting-started/quickstart.md">quickstart.md</a></td></tr><tr><td><h4><i class="fa-book" style="color:$primary;">:book:</i> Reconciliation</h4></td><td>The actions and statuses behind every write.</td><td><a href="../core-concepts/core-concepts.md">core-concepts.md</a></td></tr></tbody></table>
