---
description: >-
  Give your agent memory that stays true: it keeps what's current, retracts
  what changed, and keeps the full history queryable.
icon: hand-wave
---

# Welcome to Vayl

**Vayl is the reconciling memory layer for AI agents.** When a fact changes, Vayl retires the old value instead of storing both, so your agent gets back what is true now. A removal is a real retraction, ambiguous input is flagged instead of guessed, and the full history stays queryable and auditable. Vayl is an [MCP](https://modelcontextprotocol.io) server, so any MCP client can use it.

<button type="button" class="button primary" data-action="ask" data-icon="gitbook-assistant">Ask a question…</button>

<button type="button" class="button secondary" data-action="ask" data-query="How do I connect Vayl to my MCP client" data-icon="bolt">Connect an MCP client</button><button type="button" class="button secondary" data-action="ask" data-query="What is reconciling memory" data-icon="book">What is reconciling memory?</button><button type="button" class="button secondary" data-action="ask" data-query="How do I scope memory per user" data-icon="sitemap">Scope memory per user</button>

***

## See it in one example

Vayl turns a conversation into facts and keeps only what is true now, while preserving the history. Here a care assistant records a fictional patient's anticoagulant, then a switch to another one. Medications are confirm-required in the clinical preset, so the switch waits for a clinician.

```
remember("Patient is on warfarin 5 mg daily for atrial fibrillation.")
  Stored: [ADD] active_medication = warfarin 5 mg daily; [ADD] primary_diagnosis = atrial fibrillation
remember("Allergic to penicillin, causes hives.")
  Stored: [ADD] allergy = penicillin, causes hives
remember("Warfarin stopped today. Started apixaban 5 mg twice daily.")
  Stored: [FLAG] active_medication = apixaban 5 mg twice daily; …

confirm_change(4, decided_by="Dr. Okafor, verified with pharmacy")
  Approved #4: SUPERSEDE active_medication = apixaban 5 mg twice daily

recall("What anticoagulant is the patient on?")
  The patient is on apixaban 5 mg twice daily.
recall("What anticoagulant was the patient on before?", include_history=True)
  The patient was previously on warfarin 5 mg daily.
recall("Can we give amoxicillin?")
  No—amoxicillin is a penicillin, and the patient has a penicillin allergy that causes hives.
```

{% hint style="info" %}
**Captured from a real run** with `gpt-6-luna`, `VAYL_SLOT_SCHEMA=preset:clinical` and `VAYL_CRITICAL_CATEGORIES=critical`. The `Stored:` and `Approved` lines are Vayl's own output. The `recall` answers are written by the model, so their wording varies. The allergy reaches the last answer because critical facts go into every answer. The whole flow is in [Tutorial: a hospital medication assistant](guides/tutorial-a-hospital-medication-assistant.md).
{% endhint %}

Why this matters, and how it differs from memory that only appends: [Why Vayl](why-vayl/README.md).

{% hint style="info" icon="sparkles" %}
**New to reconciling memory?** Read [Reconciliation](core-concepts/core-concepts.md) for _supersede_, _retract_, _flag_, and the same-slot invariant, the ideas that make "what's true now" unambiguous.
{% endhint %}

## Where to start

<table data-card-size="large" data-view="cards"><thead><tr><th></th><th></th><th></th><th data-hidden data-card-target data-type="content-ref"></th></tr></thead><tbody><tr><td><h4><i class="fa-bolt" style="color:$primary;">:bolt:</i></h4></td><td><h4>Quickstart</h4></td><td>Install Vayl, connect your MCP client, and store your first fact.</td><td><a href="getting-started/quickstart.md">quickstart.md</a></td></tr><tr><td><h4><i class="fa-book" style="color:$primary;">:book:</i></h4></td><td><h4>Reconciliation</h4></td><td>Supersede, retract, flag, the same-slot invariant, and history.</td><td><a href="core-concepts/core-concepts.md">core-concepts.md</a></td></tr><tr><td><h4><i class="fa-graduation-cap" style="color:$primary;">:graduation-cap:</i></h4></td><td><h4>Guides</h4></td><td>Deploy a team server, add safety gates, run on local models.</td><td><a href="guides/guides.md">guides.md</a></td></tr><tr><td><h4><i class="fa-book-open" style="color:$primary;">:book-open:</i></h4></td><td><h4>Reference</h4></td><td>Configuration, CLI, HTTP endpoints, and troubleshooting.</td><td><a href="reference/reference.md">reference.md</a></td></tr></tbody></table>

## Next steps

<table data-view="cards"><thead><tr><th></th><th></th><th data-hidden data-card-target data-type="content-ref"></th></tr></thead><tbody><tr><td><h4><i class="fa-lightbulb" style="color:$primary;">:lightbulb:</i> Why Vayl</h4></td><td>Why appending memory goes stale, and who needs reconciling memory most.</td><td><a href="why-vayl/README.md">README.md</a></td></tr><tr><td><h4><i class="fa-diagram-project" style="color:$primary;">:diagram-project:</i> How Vayl works</h4></td><td>Architecture and the data flow of a write and a read.</td><td><a href="how-vayl-works.md">how-vayl-works.md</a></td></tr><tr><td><h4><i class="fa-plug" style="color:$primary;">:plug:</i> Integrations</h4></td><td>Claude Code, Cursor, and agent frameworks.</td><td><a href="integrations/README.md">README.md</a></td></tr></tbody></table>
