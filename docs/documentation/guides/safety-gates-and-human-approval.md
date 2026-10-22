---
description: >-
  Block actions on unsafe facts, require a person to approve risky changes,
  make critical facts impossible to miss, and pin down domain fields.
icon: gear
---

# Safety gates & human approval

Reconciliation keeps memory current. For agents where a wrong action is costly (clinical, financial, legal), Vayl adds five guardrails on top: an action gate, a human approval queue, trusted sources for authorized feeds, a critical-fact channel, and declared slots that tie them together.

The examples use a clinical space, `user_id="patient_42"`, with the built-in `preset:clinical` schema. Outputs are real tool output; subject names under your own schema will be the ones you declare.

## Gate an action on the facts behind it

Before your agent acts on a fact, ask whether it's safe to:

```python
check_before_act(subject="active_medication", user_id="patient_42")
```

```
✅ SAFE to act on 'active_medication'.
  current: active_medication = metformin 500 mg PO twice daily; active_medication = warfarin 7 mg PO daily
```

When a check fails, the result is `BLOCKED` with one line per reason:

```
⛔ BLOCKED — do NOT act on 'active_medication':
  • recently changed 0.0d ago — may be unsettled
```

The policy is set per call:

| Parameter | Default | Blocks when |
| --- | --- | --- |
| `block_on_flagged` | `True` | the value is disputed (`FLAGGED_CONFLICT`) |
| `min_confidence` | `0.7` | the fact's confidence is below this |
| `require_active` | `True` | the fact is no longer current (superseded or historical) |
| `max_staleness_days` | `0` (off) | the fact was last set more than this many days ago |
| `block_on_recent_change_days` | `0` (off) | the fact replaced an earlier value less than this many days ago |

Staleness and recent-change blocking are off unless you pass a value above 0. For a high-stakes action, set them explicitly, for example `max_staleness_days=90, block_on_recent_change_days=1`.

`safe_recall` combines recall with the same gate. It answers only if every current fact behind the answer passes the policy; otherwise it withholds the answer and says why:

```python
safe_recall(question="what is the current medication plan?", user_id="patient_42",
            block_on_recent_change_days=1)
```

```
⛔ WITHHELD — not safe to act on this answer:
  • recently changed 0.0d ago — may be unsettled
```

When it does answer, the answer is written by the model, like any recall. Use `safe_recall` instead of `recall` on the path to an irreversible action.

## Require a person to approve risky changes

Some writes are themselves the hazard: "we should probably stop the warfarin" in a note is not an order to stop it. A slot declared with `confirm: true` doesn't apply a replacement, a removal, or a new item added to a slot that already holds one (a second medication). It records a **proposal**, leaves the current value standing, and waits for a person:

```python
remember(text="Increase warfarin to 7 mg PO daily", user_id="patient_42")
# Stored: [FLAG] active_medication = warfarin 7 mg PO daily

pending_changes(user_id="patient_42")
```

```
1 change(s) awaiting approval:
  #4 REPLACE active_medication: 'warfarin 5 mg PO daily' -> 'warfarin 7 mg PO daily'
        said: 'Increase warfarin to 7 mg PO daily'

Approve with confirm_change(memory_id), discard with reject_change(memory_id).
```

Approve or discard it:

```python
confirm_change(memory_id=4, user_id="patient_42", decided_by="phoned pharmacy")
# Approved #4: SUPERSEDE active_medication = warfarin 7 mg PO daily

reject_change(memory_id=4, user_id="patient_42")
# Discarded #4. The current value is unchanged.
```

### Who can approve

`confirm_change` and `reject_change` need the **`approve`** capability, which only the `admin` and `member` roles have (0.6.0). An agent key can propose a change but can't approve its own proposal, or quietly discard the queue:

```
Access denied: 'confirm_change' requires the 'approve' capability; your role(s) ['agent'] do not grant it.
```

The approver recorded in the audit log is the authenticated caller, as `name [principal_id]`. `decided_by` is only an optional note stored beside it, so nobody can record a decision in someone else's name. Over local stdio the caller is the local admin (`local [local]`); give each approver their own `member` key on `vayl-server`.

### How proposals behave

* A proposal is **flagged, not active**, so recall never presents it as current.
* Only the first value on an empty gated slot applies directly; there is nothing to lose yet. After that, a new list item is proposed as `ADD <subject>: '<value>'`.
* Stop language that names a current list item ("warfarin stopped today", "D/C warfarin") is proposed as a removal, even when the model files it as history or as a new entry. A hold ("hold metformin tonight") is not a stop, and hedged text ("probably stop it if …") never becomes one.
* A future-tense order on a gated slot ("increase metformin to 1000 mg", "start apixaban") goes to the queue too.
* A change the model aims at a gated fact goes through that fact's gate, even if the model names a different subject for it.
* Before approving a new anticoagulant, the reviewer is warned if another one would stay current. `pending_changes` and `confirm_change` both show `⚠ … two anticoagulants`. It's a warning, not a block: an overlap can be intended. Common brand names (Coumadin, Eliquis, Xarelto, Pradaxa, Savaysa, Lovenox) are matched to their generic names. For anything wider, map drug names upstream, for example to RxNorm, before they reach Vayl.
* Proposing the same change twice queues it once.
* A proposal can't be confirmed once the value it would have replaced has changed. It was made against state that no longer holds; `confirm_change` says so and the change must be proposed again.
* A rejected proposal is kept as history: that someone proposed it is worth auditing.

`forget` on a confirm-gated slot queues a removal proposal and says so; `pending_changes()` lists it as `REMOVE <subject>: '<value>'`:

```
Proposed for removal, awaiting approval (the value stays current until someone approves it with confirm_change; see pending_changes): code_status = full code
```

## Let authorized feeds skip the queue

A structured feed, such as a FHIR `MedicationRequest` with `status=stopped`, is already an authorized order: the sign-off happened upstream. Re-queuing it for approval is friction, not safety. List those feeds as trusted sources:

```bash
VAYL_TRUSTED_SOURCES=fhir,hl7
```

A `remember(..., source="fhir")` then applies directly, even on a `confirm` slot. Because `source` is a string the caller chooses, claiming a trusted source is itself an approval. Since 0.6.0 it's allowed only for a key whose principal name equals the source, or a caller with `approve`:

```python
create_principal("fhir", role="agent", scopes="patient_42")   # the feed's own key (admin only)
```

Any other key gets, and the audit log records:

```
Access denied: 'fhir' is a trusted source (VAYL_TRUSTED_SOURCES), so its changes skip the confirmation gate. Only a key named 'fhir', or one with the 'approve' capability, may write as it.
```

## Make critical facts impossible to miss

Ordinary recall passes the model every fact when a space is small (up to `VAYL_RECALL_CONTEXT`, 40 by default) and the top-ranked ones when it's larger. For most memory a low-ranked fact is a quality issue. For an allergy it's a safety issue: it isn't ranked low, it's **absent**, and the gates above can't judge a fact they were never given.

Mark categories critical so their facts skip ranking and always reach `recall` and `safe_recall`:

```bash
VAYL_CRITICAL_CATEGORIES=critical
```

A fact gets a category from its declared slot (below), or from the caller's metadata:

```python
remember(text="Patient is allergic to penicillin", user_id="patient_42",
         metadata={"category": "critical"})
recall(question="summarise the chart", user_id="patient_42",
       critical_categories="critical,allergy")   # per-call list
```

An empty `critical_categories` means "use `VAYL_CRITICAL_CATEGORIES`", never "none", so a caller can't opt out of what the operator marked critical.

If the critical set exceeds `VAYL_CRITICAL_BUDGET` (default 200), the read fails with `CriticalOverflow` instead of dropping part of it. Dropping part of an always-include set would recreate the miss it exists to prevent. The client sees `Vayl couldn't complete that (ref …)`; see [Troubleshooting](../reference/troubleshooting.md).

## Declare your domain's slots

By default the extractor names its own slots, which suits open-ended memory. For a domain with known fields, declare them so facts land in fixed slots and reconcile deterministically. Point `VAYL_SLOT_SCHEMA` at a JSON file, or at a built-in preset: `preset:assistant`, `preset:clinical`, `preset:coding`, `preset:finance`, `preset:sales`, `preset:support`.

```json
{"slots": [
  {"name": "allergy", "category": "critical", "verbatim": true, "multi": true, "confirm": false,
   "description": "a substance the patient reacts to, and the reaction",
   "aliases": ["allergies", "patient_allergy", "drug_allergy", "known_allergies"]},
  {"name": "active_medication", "category": "critical", "verbatim": true, "multi": true, "confirm": true,
   "description": "a medication the patient is currently taking, with dose and frequency",
   "aliases": ["current_medication", "medication", "prescribed_medication", "meds"]}
]}
```

That is an excerpt of `preset:clinical`, which also declares `code_status` (critical, confirm-gated), `primary_diagnosis` and `care_team_lead`.

| Property | Default | Effect |
| --- | --- | --- |
| `name` | required | The canonical subject. Slots without a name are ignored. |
| `description` | `""` | Tells the extractor what belongs in the slot. |
| `aliases` | `[]` | Other spellings that fold onto `name`. Matching ignores case and separators only (`Patient Allergy` = `patient_allergy`); it is never semantic. |
| `category` | `""` | Tags every fact in the slot. Feeds the critical-fact channel. |
| `verbatim` | `false` | Store the value exactly as stated. Normalizing is lossy: fine for a colour, unacceptable for a dose. |
| `multi` | `false` | The slot holds a list. A new distinct value is added alongside the others instead of replacing them, and a change or removal applies to the matching item (the drug, not the whole list). |
| `confirm` | `false` | Route replacements and removals through the approval queue above. |

A missing file, malformed JSON or unknown preset stops Vayl at startup rather than falling back to no schema. With no schema set, behaviour is unchanged.

## Next steps

<table data-view="cards"><thead><tr><th></th><th></th><th data-hidden data-card-target data-type="content-ref"></th></tr></thead><tbody><tr><td><h4><i class="fa-hospital" style="color:$primary;">:hospital:</i> Tutorial: a hospital medication assistant</h4></td><td>Every gate on this page, used end to end.</td><td><a href="tutorial-a-hospital-medication-assistant.md">tutorial-a-hospital-medication-assistant.md</a></td></tr><tr><td><h4><i class="fa-scale-balanced" style="color:$primary;">:scale-balanced:</i> Accountability</h4></td><td>Prove what was known and decided: decisions, attestations, the audit chain.</td><td><a href="../mcp-tools/accountability.md">accountability.md</a></td></tr><tr><td><h4><i class="fa-book" style="color:$primary;">:book:</i> Reconciliation</h4></td><td>Flagging, state vs. event, and history.</td><td><a href="../core-concepts/core-concepts.md">core-concepts.md</a></td></tr></tbody></table>
