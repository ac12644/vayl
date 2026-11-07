---
description: >-
  The nine reconcile actions, the four fact statuses, the same-slot invariant,
  events vs. state, declared slots, source policy, and history.
icon: book
---

# Reconciliation

On every write, Vayl decides how each new fact relates to what the space already holds and applies exactly one of nine actions, so a read has a single current value per slot to return. This page covers each action and when it fires, the statuses a fact can hold, and the rules the engine enforces in code.

## Facts

`remember` sends the message to one LLM extraction call, which returns zero or more facts:

| Field | Meaning |
| --- | --- |
| `subject` | What the fact is about, in snake\_case (`state`, `plan`, `active_medication`). The model picks the name unless a [declared slot](#declared-slots-and-presets) fixes it. |
| `value` | The value itself (`Zustand`, `Free`, `Warfarin 5mg`). |
| `scope` | A qualifier inside the space: `global` by default, or `web`, `mobile`, `backend`… |
| `kind` | `state` (holds until replaced) or `event` (happened once). |
| `time_ref` | `current`, `past`, `future` or `unknown`. Drives the valid-time gate. |
| `action` | The model's proposed action. The engine can override it. |
| `confidence` | 0.0–1.0. Below 0.7, a change is flagged instead of applied. |

Vayl also records the `source` you pass to `remember`, the original sentence (`raw`), and any `metadata`. One message can carry several facts. _"We moved to Postgres and dropped Redis"_ produces a supersede and a retract.

## Statuses

Every stored fact has exactly one of four statuses:

| Status | Meaning | On the hot path? |
| --- | --- | --- |
| `ACTIVE` | The current value. What `recall` answers from. | Yes |
| `FLAGGED_CONFLICT` | Held for a person: an ambiguous conflict, a low-confidence change, a future-tense statement, a policy block, or a pending confirm-gate proposal. Never presented as current. | Yes |
| `SUPERSEDED` | A former value, retired by a newer one or by a retraction. | No |
| `HISTORICAL` | A recorded past fact, a retraction tombstone, or a rejected proposal. Never current. | No |

There is no "retracted" status. A retraction marks the fact `SUPERSEDED` and writes a `HISTORICAL` tombstone whose value is `(retracted: <old value>)`.

## The nine actions

The model proposes an action. `_apply` in `memory/llm_memory.py` makes the final decision, and the result of `remember` shows it: `Stored: [<ACTION>] <subject> = <value>`. A `SKIP` is reported on its own line, `Not stored (hypothetical, sarcasm, or nothing to change): <subject> = <value>`.

| Action | Effect | When it fires |
| --- | --- | --- |
| `ADD` | New `ACTIVE` fact. | A new slot with no competing value, a new item on a list slot, or an event. |
| `SUPERSEDE` | Old fact becomes `SUPERSEDED`, new fact `ACTIVE`. | A new value for a slot that already holds a different one. This includes an `ADD` the model mislabelled (see the [same-slot invariant](#the-same-slot-invariant)). |
| `RETRACT` | Old fact becomes `SUPERSEDED`, plus a `HISTORICAL` tombstone. The slot is left empty. | A removal with no replacement: "we dropped Sentry". Also every `forget` call. |
| `REFINE` | Updates the targeted fact's value, keeping the same fact id. The earlier wording stays in history. | Same fact, more detail. |
| `DEDUP` | Nothing stored. | The same value is already active in the same slot and scope, a past fact is already archived, a proposal is already pending, or the [dedup prefilter](#dedup-prefilter) matched. |
| `COEXIST` | New `ACTIVE` fact beside the existing one. | Same subject, different **scope**: `state = Zustand` for `web`, `state = MobX` for `mobile`. |
| `FLAG` | New fact stored as `FLAGGED_CONFLICT`. The current value stands. | See [When a fact is flagged](#when-a-fact-is-flagged). |
| `SKIP` | Nothing stored. | Hypotheticals, questions and sarcasm ("maybe we should try Jotai?"), or a retraction that matches nothing. |
| `ARCHIVE` | New fact stored as `HISTORICAL`. Never active. | The statement describes a former state: "historically we billed monthly", "we used to use MySQL". |

Real results from `remember`:

```
Stored: [ADD] state = Redux
Stored: [SUPERSEDE] state = Zustand
Stored: [ARCHIVE] billing = monthly
Not applied, flagged for review: [FLAG] primary_database = Postgres
Not stored (hypothetical, sarcasm, or nothing to change): state = Jotai
No durable fact found (looked like chatter or a question).
```

The last line is what you get when extraction returns no facts at all, for example for a greeting.

### When a fact is flagged

A fact is stored as `FLAGGED_CONFLICT` when any of these apply:

* **Ambiguity.** The model reports two state values for the same slot with nothing saying which is newer.
* **Low confidence.** A `SUPERSEDE`, `COEXIST`, `REFINE` or `RETRACT` has confidence below 0.7. For a retraction, the target stays active and a flagged copy is added.
* **Future tense.** A `SUPERSEDE`, `COEXIST` or `ADD` whose `time_ref` is `future` ("we will move to Postgres") isn't true yet.
* **Confirmation gate.** A change to a declared `confirm` slot. The proposal waits in `pending_changes` until someone with the `approve` capability calls `confirm_change` or `reject_change`. See [Safety gates and human approval](../guides/safety-gates-and-human-approval.md).
* **Source policy.** A `REVIEW` or `AUTHORITY` [reconcile policy](#source-aware-reconciliation) blocks the overwrite.

When the model gave a reason, it is kept in the fact's metadata for the reviewer. `list_memories` shows flagged facts with a `⚠` marker.

### Rules the engine applies in code

These checks run in `_apply` whatever the model proposed:

1. **Events never replace anything.** A `SUPERSEDE`, `REFINE` or `RETRACT` on an event becomes `ADD`.
2. **Valid-time gate.** A fact with `time_ref = past` is archived as `HISTORICAL` (`ARCHIVE`) and never retires the current value. Retractions are exempt: "Datadog is gone" still retires Datadog.
3. **Retract upgrade.** Definite removal language ("dropped", "no longer", "removed", "discontinued"…), with no hedge or sarcasm marker, turns a mislabelled `ADD`/`SUPERSEDE` into `RETRACT`. This only happens when the extracted value matches the active value in that slot.
4. **Restatement.** The same value in the same slot and scope becomes `DEDUP`.
5. **Confirmation gate**, then the **confidence threshold**, then the **same-slot invariant** with the source policy.

A `RETRACT` with no matching active fact becomes `SKIP`. `forget` reports that as `Nothing matching to retract (that fact isn't currently stored).`

### Dedup prefilter

If the exact message (case-folded, whitespace-collapsed) already produced facts and all of them are still `ACTIVE`, `remember` returns `DEDUP` without calling the model. If any of those facts has since changed, the message is extracted normally. Turn it off with `VAYL_DEDUP_PREFILTER=off`.

## The same-slot invariant

A **slot** is a fact's `(subject, scope)` within one memory space. For single-valued state, Vayl keeps **at most one active value per slot**.

This is enforced deterministically. When a new `ADD`, `SUPERSEDE`, `REFINE` or `COEXIST` lands on a slot that already holds a different active value, the old value is retired even if the model didn't link them. A weak model that labels "we use Postgres now" as `ADD` still can't leave MySQL active beside it:

```
Stored: [ADD] db = MySQL
Stored: [SUPERSEDE] db = Postgres
```

Several active values can share a subject only in these cases:

* **Different scope** (`COEXIST`). Different scopes are different slots.
* **Declared list slots** (`"multi": true`), such as allergies or active medications. A new item joins the list. A `SUPERSEDE` replaces the matching item by identity (the drug or substance name before any dose), and a retract removes only the item it names.
* **Events.** Two races are two races. The exception is a declared single-valued slot that already holds a different value: there, an incoming "event" is treated as a state change and supersedes.

A `COEXIST` is checked like any other change. If the model labels a fact `COEXIST` but gives it the **same** scope as the current value, it's a change the model misnamed, and it supersedes:

```
Stored: [ADD] state = Redux
Stored: [SUPERSEDE] state = Zustand
Stored: [COEXIST] state = MobX
```

The second fact came back from the model as `COEXIST` on the same scope as Redux, so Vayl superseded Redux. The third is a real `COEXIST` with scope `mobile`, so Zustand and MobX both stay active. Before 0.7.0 a same-scope `COEXIST` left both values active.

## Events vs. state

The extractor sets `kind` on every fact:

* **State** holds until something replaces it: "we use Postgres", "the plan is Free". State obeys the same-slot invariant.
* **Events** happened at a point in time: "ran a charity race", "the customer called on Tuesday". Events are never superseded, refined or retracted by reconciliation.

Vayl stores the kind in the fact's metadata (`{"kind": "event"}`). Facts with no kind default to state.

## Declared slots and presets

By default the model names subjects freely. That works for open-ended memory, but two statements about the same thing only reconcile if they land on the same subject. A **slot schema** fixes the names for the fields you care about.

Set `VAYL_SLOT_SCHEMA` to a JSON file path, or to a built-in preset:

```bash
VAYL_SLOT_SCHEMA=preset:coding          # or a path: /etc/vayl/slots.json
```

Built-in presets (in `src/vayl/presets/`): `assistant`, `clinical`, `coding`, `finance`, `sales`, `support`.

Each slot entry supports:

| Field | Effect |
| --- | --- |
| `name` | The canonical subject. |
| `aliases` | Other names that fold onto it. Matching is spelling-tolerant (case, spaces, hyphens), not semantic. |
| `description` | Shown to the extractor. |
| `category` | Stamped on every fact in the slot. Feeds the critical-fact channel (`VAYL_CRITICAL_CATEGORIES`). |
| `multi` | The slot holds a list. New values join it instead of superseding. |
| `confirm` | Replacements and removals become pending proposals that need human approval. |
| `verbatim` | Tells the extractor to copy the value exactly (for doses and versions). This is a prompt instruction, not a code check. |

```json
{"slots": [
  {"name": "allergy", "category": "critical", "multi": true,
   "aliases": ["allergies", "drug_allergy"]},
  {"name": "active_medication", "category": "critical", "multi": true,
   "confirm": true, "verbatim": true}
]}
```

A trusted source listed in `VAYL_TRUSTED_SOURCES` (for example `fhir`) bypasses the confirmation gate. Only a key whose principal name equals that source, or a caller with the `approve` capability, may write with that `source`. Anyone else gets `Access denied`.

## Source-aware reconciliation

Pass `source` to `remember` to record who asserted a fact. In a space several contributors write to, an admin can set how cross-source conflicts resolve with `set_reconcile_policy` (requires `admin`). Anyone with `read` can view it with `get_reconcile_policy`.

| Mode | A new fact from source B contradicts A's active fact |
| --- | --- |
| `RECENCY` (default) | B supersedes A. |
| `AUTHORITY` | B supersedes only if its rank in `authority` is strictly higher than A's. Otherwise B is flagged and A stands. Unlisted sources rank 0. |
| `REVIEW` | Always flagged for a person. |

A source correcting its own earlier fact always supersedes. The policy also applies to a cross-source `REFINE` that changes the value.

```
set_reconcile_policy("AUTHORITY", {"crm": 2, "bot": 1}, user_id="org")
→ Reconciliation policy for this space set to AUTHORITY.  authority ranks: {'crm': 2, 'bot': 1}

remember("…plan is Pro", user_id="org", source="crm")   → Stored: [ADD] plan = Pro
remember("…plan is Free", user_id="org", source="bot")  → Not applied, flagged for review: [FLAG] plan = Free
```

## Provenance

`recall(..., explain=True)` returns the answer followed by the exact facts placed in the model's context:

```
You use Zustand.

Based on these facts:
  • #2 state = Zustand  [conf 0.95, from crm, supersedes #1]
  • #3 db = MySQL  [conf 0.95]
```

The first line is model-written. The fact list is deterministic: id, subject, value, confidence, source when set, and the id it superseded. `record_decision` snapshots the same set.

## History and tombstones

Nothing is deleted by reconciliation. Superseded values, archived past facts, retraction tombstones and rejected proposals stay in the database but off the hot path.

* Ordinary `recall`, `check_before_act` and every write load only `ACTIVE` and `FLAGGED_CONFLICT` rows. A retired value can't be returned as current, because it is never loaded.
* `history(subject)` shows the full timeline for one subject:

  ```
  History for 'monitoring' (oldest → newest):
    1. Sentry  [SUPERSEDED]
    2. (retracted: Sentry)  [HISTORICAL]
  ```
* `recall(..., include_history=True)` adds retired facts to the ranking pool, tagged `(history)`, for questions about the past.
* `list_memories` shows active facts, flagged facts, and the 20 most recent retired rows.
* Only `delete`, `delete_all` and `purge_expired` remove rows permanently. See [Compliance (GDPR)](../mcp-tools/compliance-gdpr.md).

## Next steps

<table data-view="cards"><thead><tr><th></th><th></th><th data-hidden data-card-target data-type="content-ref"></th></tr></thead><tbody><tr><td><h4><i class="fa-sitemap" style="color:$primary;">:sitemap:</i> Memory spaces and tenants</h4></td><td>Where a slot lives: tenant, user, agent and run.</td><td><a href="memory-spaces.md">memory-spaces.md</a></td></tr><tr><td><h4><i class="fa-shield-halved" style="color:$primary;">:shield-halved:</i> Safety gates</h4></td><td>Confirm-gated slots, pending changes and approval.</td><td><a href="../guides/safety-gates-and-human-approval.md">safety-gates-and-human-approval.md</a></td></tr><tr><td><h4><i class="fa-toolbox" style="color:$primary;">:toolbox:</i> Memory tools</h4></td><td>remember, recall, forget, history and the rest.</td><td><a href="../mcp-tools/memory.md">memory.md</a></td></tr></tbody></table>
