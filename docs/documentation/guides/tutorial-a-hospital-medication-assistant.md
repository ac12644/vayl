---
description: >-
  Build a clinical assistant that keeps a patient's meds and allergies current,
  never misses a critical fact, needs a clinician to approve a drug change, and
  leaves a signed audit trail.
icon: hospital
---

# Tutorial: a hospital medication assistant

By the end of this tutorial you'll have a clinical assistant that tracks one patient's medications and allergies across a hospital stay, with every change to a medication approved by a person and every step recorded in a signed audit trail. Along the way you'll use reconciliation, declared slots, the critical-fact channel, the approval queue, trusted feeds, safe recall and attestations, and see why each one matters when a wrong answer can hurt someone.

{% hint style="warning" %}
This is a software tutorial, not medical guidance. Vayl presents and safeguards facts; a clinician decides. It is not a certified clinical system.
{% endhint %}

## What we're building

A patient, Dorothy Vance, is admitted. The assistant will:

1. record her home medications and allergies on admission,
2. keep the medication list current as doses change and drugs are stopped,
3. make sure her allergies always reach the answer, even in a long chart,
4. require a clinician to approve any change or stop to a medication,
5. accept an authorized stop order from the pharmacy feed without re-approval,
6. withhold an answer on the path to an action when a fact isn't safe to act on,
7. leave a signed, tamper-evident record of what was known and decided.

Each patient is an isolated [memory space](../core-concepts/memory-spaces.md), so everything is scoped to `user_id="patient_dorothy"`.

## How to read the outputs

The `remember`, `forget`, `pending_changes`, `confirm_change`, `list_memories`, `history`, `check_before_act`, `attest` and `verify_audit` outputs below are the exact strings Vayl returns. Two things depend on your model:

* **Values and subjects.** The model extracts the value from each sentence, so `warfarin 5 mg PO daily` could come back worded slightly differently. The subject is fixed here because the slots are declared.
* **Recall answers.** `recall`, `safe_recall` (when it answers) and the `based on:` line of `record_decision` are written by the model. Those are shown as representative examples.

Memory ids (`#4`) depend on what's already in the space. Read them from `pending_changes()` rather than hard-coding them.

## Step 0: model the clinical fields

By default Vayl lets the extractor name its own slots. For a clinical domain we want deterministic reconciliation, doses stored exactly as written, medications and allergies handled as lists, and medication changes gated behind a person. Vayl ships this as a built-in preset, `preset:clinical`. Its two most important slots:

```json
{
  "slots": [
    {
      "name": "allergy",
      "description": "a substance the patient reacts to, and the reaction (a patient may have several — this is a list)",
      "category": "critical", "verbatim": true, "multi": true, "confirm": false,
      "aliases": ["allergies", "patient_allergy", "drug_allergy", "known_allergies"]
    },
    {
      "name": "active_medication",
      "description": "a medication the patient is currently taking, with dose and frequency",
      "category": "critical", "verbatim": true, "multi": true, "confirm": "auto",
      "aliases": ["current_medication", "medication", "prescribed_medication", "meds"]
    }
  ]
}
```

The preset also declares `code_status` (critical, confirm-gated), `primary_diagnosis` (critical) and `care_team_lead`. Each flag encodes a clinical decision:

| Flag | What it does | Why it matters here |
| --- | --- | --- |
| `verbatim` | store the value exactly as stated | normalizing `warfarin 5 mg PO daily` could lose the dose |
| `category: critical` | tag every fact in the slot | with `VAYL_CRITICAL_CATEGORIES=critical`, meds and allergies bypass ranking |
| `multi: true` | the slot holds a list | a patient has several meds and allergies; a new one is added, not swapped for the others |
| `confirm` | changes are gated. `"auto"`: a clear change applies at once and is marked auto-applied, and only an uncertain one waits for sign-off. `"review"` (or `true`): every change waits | `active_medication` is `"auto"` in the preset, `code_status` is `"review"`, and `allergy` is `false`, because a new allergy must show up immediately |

To customise the slots, copy the preset into your own JSON file and point `VAYL_SLOT_SCHEMA` at its absolute path. A missing or malformed schema stops Vayl at startup, so you never run believing a critical slot is declared when it isn't.

## Step 1: connect

We'll drive Vayl from a script with the Python client that ships with `vayl-mcp` (see [Calling Vayl from code](../getting-started/calling-vayl-from-code.md)). It spawns `vayl-mcp` over stdio and sends the patient's `user_id` on every call to a tool that takes one:

```python
import os
from vayl import Vayl

PATIENT = "patient_dorothy"

env = {
    **os.environ,
    "OPENAI_API_KEY": "sk-…",                  # chat: gpt-5-mini; embeddings: text-embedding-3-small
    "VAYL_DB": "/abs/path/hospital.db",
    "VAYL_SLOT_SCHEMA": "preset:clinical",
    "VAYL_CRITICAL_CATEGORIES": "critical",
    "VAYL_TRUSTED_SOURCES": "fhir",            # used in Step 7
    "VAYL_CONFIRM_MODE": "review",             # this ward signs off every medication change
}

with Vayl(user_id=PATIENT, env=env) as m:
    # … the steps below go here …
    pass
```

This tutorial runs the approval queue on every medication change (`VAYL_CONFIRM_MODE=review`), so you can see each step of the sign-off. Leave it unset to use the preset's `"auto"` mode, where a clear change such as "D/C warfarin, start apixaban 5 mg BID" applies at once and only an uncertain one is queued; see [Safety gates and human approval](safety-gates-and-human-approval.md#automatic-or-reviewed).

Since 0.6.0, `OPENAI_API_KEY` on its own configures both the chat model and the embedder. For a fully local setup, see [Local models with Ollama](local-models-with-ollama.md).

{% hint style="info" %}
Over stdio, every call runs as a trusted local admin, which has every capability. That keeps the tutorial short. Step 10 shows the keys and roles to use on a shared `vayl-server`, where the assistant can propose changes but only a clinician can approve them.
{% endhint %}

## Step 2: admission, recording home meds and allergies

On admission the patient's home medication list arrives from the pharmacy-reconciled record over the FHIR feed, the trusted source configured in Step 1. Each line becomes a fact. Because the slots are declared, the facts land in `active_medication` and `allergy` and are tagged `critical`:

```python
print(m.remember("Home med: warfarin 5 mg PO daily", source="fhir"))
print(m.remember("Home med: metformin 500 mg PO twice daily", source="fhir"))
print(m.remember("Allergic to penicillin (rash)", source="fhir"))
```

```
Stored: [ADD] active_medication = warfarin 5 mg PO daily
Stored: [ADD] active_medication = metformin 500 mg PO twice daily
Stored: [ADD] allergy = penicillin (rash)
```

Because `active_medication` is `multi`, metformin is added next to warfarin instead of replacing it. `source="fhir"` records where each fact came from, which the audit log keeps, and because `fhir` is trusted the list applies directly.

If the medication history came from conversation instead, from an untrusted source, only the first drug would apply. Each further drug would wait in `pending_changes` as `ADD active_medication: '...'` until a clinician approves it, so a misheard or invented medication can never become current on its own. Allergies are never gated: a new allergy must count at once.

## Step 3: the current picture

`list_memories` shows exactly what's current:

```python
print(m.list_memories())
```

```
• active_medication = warfarin 5 mg PO daily  (#1)
• active_medication = metformin 500 mg PO twice daily  (#2)
• allergy = penicillin (rash)  (#3)
```

`recall` answers in prose from the same active set:

```python
print(m.recall("what medications is the patient on?"))
# e.g. "Warfarin 5 mg PO daily and metformin 500 mg PO twice daily."   (model-generated)
```

## Step 4: a dose change needs approval

Day two, the team wants to increase the warfarin:

```python
print(m.remember("Increase warfarin to 7 mg PO daily", source="dr_smith"))
print(m.pending_changes())
```

```
Stored: [FLAG] active_medication = warfarin 7 mg PO daily
1 change(s) awaiting approval:
  #4 REPLACE active_medication: 'warfarin 5 mg PO daily' -> 'warfarin 7 mg PO daily'
        said: 'Increase warfarin to 7 mg PO daily'

Approve with confirm_change(memory_id), discard with reject_change(memory_id).
```

The dose change was **not applied**. `active_medication` is confirm-gated, so Vayl recorded a proposal and left 5 mg standing. That is the point of the gate: a model reading "increase the warfarin" in a note is not a clinician ordering it. A clinician approves it explicitly:

```python
print(m.confirm_change(memory_id=4, decided_by="verbal order, confirmed with pharmacy"))
print(m.history(subject="active_medication"))
```

```
Approved #4: SUPERSEDE active_medication = warfarin 7 mg PO daily
History for 'active_medication' (oldest → newest):
  1. warfarin 5 mg PO daily  [SUPERSEDED]
  2. metformin 500 mg PO twice daily  [ACTIVE]
  3. warfarin 7 mg PO daily  [ACTIVE]
```

The change landed on warfarin only. Within a `multi` list, Vayl matches a change to the item it names (the drug), so metformin is untouched. The audit log records who approved it as the authenticated caller, with `decided_by` as a note beside it.

## Step 5: a newly discovered allergy

Mid-stay the patient reacts to a sulfa drug. `allergy` is `multi` and not confirm-gated, so it applies at once and is added to the list:

```python
print(m.remember("New allergy: sulfamethoxazole (hives)", source="dr_jones"))
```

```
Stored: [ADD] allergy = sulfamethoxazole (hives)
```

Recall passes the model every fact while the space is small, and only the best-ranked facts once it grows past `VAYL_RECALL_CONTEXT` (40 by default). A fact outside that set is absent from the answer, and for an allergy that's a safety failure. Because allergies are tagged `critical` and `VAYL_CRITICAL_CATEGORIES=critical` is set, they bypass ranking and are always passed to the model:

```python
print(m.recall("give me a one-line chart summary"))
# e.g. "On warfarin 7 mg PO daily and metformin 500 mg PO twice daily; allergic to
#       penicillin (rash) and sulfamethoxazole (hives)."   (model-generated)
```

The guarantee is that both allergies are in the facts the model answers from, however long the chart gets. How the model words the summary is up to the model.

## Step 6: a stop in a note is only a proposal

A resident writes "stop the metformin" in a note. A stop is a removal, and removals on `active_medication` are gated too:

```python
print(m.forget("Stop the metformin"))
print(m.pending_changes())
```

```
Proposed for removal, awaiting approval (the value stays current until someone approves it with confirm_change; see pending_changes): active_medication = metformin 500 mg PO twice daily
1 change(s) awaiting approval:
  #6 REMOVE active_medication: 'metformin 500 mg PO twice daily'
        said: 'Stop the metformin'

Approve with confirm_change(memory_id), discard with reject_change(memory_id).
```

Running the same `forget` again doesn't queue a second proposal; it replies `Already awaiting approval (see pending_changes): active_medication`.

Metformin stays active until someone decides. The team decides against stopping it on the strength of a note:

```python
print(m.reject_change(memory_id=6))
```

```
Discarded #6. The current value is unchanged.
```

The rejected proposal is kept in history: that someone proposed stopping a drug is itself worth auditing.

## Step 7: an authorized stop order from the pharmacy feed

The next day the prescriber discontinues metformin in the EHR, and the FHIR feed delivers a `MedicationRequest` with `status=stopped`. That is an authorized order: the sign-off happened upstream, so queuing it again would be friction. `fhir` is in `VAYL_TRUSTED_SOURCES`, so a write with that source skips the queue:

```python
print(m.remember("MedicationRequest 7781: metformin 500 mg PO twice daily, status=stopped",
                 source="fhir"))
print(m.list_memories())
```

```
Stored: [RETRACT] active_medication = metformin 500 mg PO twice daily
• allergy = penicillin (rash)  (#3)
• active_medication = warfarin 7 mg PO daily  (#4)
• allergy = sulfamethoxazole (hives)  (#5)

— history (superseded / retracted / archived) —
  ◦ active_medication = (retracted: metformin 500 mg PO twice daily)  [HISTORICAL]
  ◦ active_medication = metformin 500 mg PO twice daily  [HISTORICAL]
  ◦ active_medication = metformin 500 mg PO twice daily  [SUPERSEDED]
  ◦ active_medication = warfarin 5 mg PO daily  [SUPERSEDED]
```

Because `source` is a string the caller chooses, claiming `fhir` is itself an approval. On a shared server, only a key named `fhir` or a caller with the `approve` capability may use it (Step 10).

## Step 8: don't act on an unsafe fact

Before the assistant drafts a discharge prescription, gate the read. `check_before_act` gives a verdict on one subject:

```python
print(m.check_before_act(subject="active_medication"))
```

```
✅ SAFE to act on 'active_medication'.
  current: active_medication = warfarin 7 mg PO daily
```

`safe_recall` answers only if every fact behind the answer passes the policy. By default it blocks disputed facts, facts below 0.7 confidence and retired facts. Staleness and recent-change checks are off unless you set them, so for a discharge script turn on both:

```python
print(m.safe_recall(question="what is the current medication plan?",
                    max_staleness_days=1, block_on_recent_change_days=1))
```

The warfarin dose changed today, so the answer is withheld:

```
⛔ WITHHELD — not safe to act on this answer:
  • recently changed 0.0d ago — may be unsettled
```

Once every fact passes, `safe_recall` returns the model's answer, like `recall`. A disputed (flagged) value, low confidence or a just-changed fact blocks the action instead of letting it proceed on shaky ground.

## Step 9: accountability

When the clinician acts, record why, bound to the exact facts consulted:

```python
print(m.record_decision(
    action_summary="Discharge on warfarin 7 mg PO daily; metformin stopped per MedicationRequest 7781",
    question="what medications is the patient on?"))
```

```
Decision #1 recorded, bound to 3 belief(s).
  based on: <model-generated answer>
  receipt: 20e8e63c3b7e5eb1…  (signed; verify with explain_decision #1)
```

The snapshot is signed and immutable, so months later `explain_decision(decision_id=1)` shows what was believed at the time, even after the meds change again. At discharge, issue a signed attestation of the medication list and verify the whole trail:

```python
print(m.attest(subject="active_medication"))
print(m.verify_audit())
print(m.export_public_key())
```

```
Attestation #1: 'active_medication' = warfarin 7 mg PO daily  (as of 2026-09-26T12:31:22+00:00).
  signed & anchored to audit head a6565e6591108d21… — verify with verify_receipt #1.
✓ Audit chain INTACT — 12 entries verified.
Ed25519 public key (hex):
fe74364fa144ea…
```

`verify_audit` and `export_public_key` take no `user_id`; the client leaves it out for tools that don't accept one. A third party can check the attestation with the public key alone, with no database and no secret.

## Step 10: keys and roles on a shared server

On `vayl-server`, every caller has its own key and role, and the gates above become enforced boundaries. A setup for this ward:

| Principal | Role | Scopes | Can |
| --- | --- | --- | --- |
| `ward-assistant` | `agent` | `patient_dorothy` | remember, recall, propose changes; **not** approve |
| `dr-smith` | `member` | (all) | everything above, plus `approve`: `confirm_change` / `reject_change` |
| `fhir` | `agent` | `patient_dorothy` | write with `source="fhir"`, because the principal name matches the trusted source |

Create them over local stdio (the local admin doesn't count as a seat), and keep each key in its own secret store:

```python
with Vayl(env=env) as admin:
    print(admin.create_principal(name="ward-assistant", role="agent", scopes=PATIENT))
    print(admin.create_principal(name="dr-smith", role="member"))
    print(admin.create_principal(name="fhir", role="agent", scopes=PATIENT))
```

With these keys:

* the assistant calling `confirm_change` gets `Access denied: 'confirm_change' requires the 'approve' capability; your role(s) ['agent'] do not grant it.`
* the assistant calling `remember(..., source="fhir")` gets `Access denied: 'fhir' is a trusted source (VAYL_TRUSTED_SOURCES), so its changes skip the confirmation gate. Only a key named 'fhir', or one with the 'approve' capability, may write as it.`
* Dr Smith's approval is recorded as `dr-smith [prin_…]`, whatever `decided_by` says.

The Community edition allows 3 active principals, which this setup uses exactly. See [Deploying vayl-server](deploying-vayl-server.md) and [Authentication & access](../core-concepts/authentication-and-access.md).

## What you leaned on

| Requirement | Vayl feature |
| --- | --- |
| meds stay current as they change | reconciliation + `multi` per-drug lists |
| allergies always reach the answer | the critical-fact channel (`category: critical`) |
| a person approves changes and stops | confirm-gated slots + `pending_changes` / `confirm_change` / `reject_change` |
| authorized orders apply without re-approval | `VAYL_TRUSTED_SOURCES` + a key named like the source |
| don't act on shaky data | `safe_recall` / `check_before_act` |
| prove what was known and decided | `record_decision`, `attest`, the signed audit chain |

Almost all of it is configuration plus the standard tools. The guarantees live in the engine, not in prompt wording.

## Next steps

<table data-view="cards"><thead><tr><th></th><th></th><th data-hidden data-card-target data-type="content-ref"></th></tr></thead><tbody><tr><td><h4><i class="fa-gear" style="color:$primary;">:gear:</i> Safety gates &#x26; human approval</h4></td><td>The gating mechanisms used here, in depth.</td><td><a href="safety-gates-and-human-approval.md">safety-gates-and-human-approval.md</a></td></tr><tr><td><h4><i class="fa-book" style="color:$primary;">:book:</i> Reconciliation</h4></td><td>Why a superseded value can't come back as current.</td><td><a href="../core-concepts/core-concepts.md">core-concepts.md</a></td></tr><tr><td><h4><i class="fa-toolbox" style="color:$primary;">:toolbox:</i> MCP tools</h4></td><td>Every tool used above, with its arguments.</td><td><a href="../mcp-tools/README.md">README.md</a></td></tr></tbody></table>
