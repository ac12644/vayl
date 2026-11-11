# Medication-change safety under a real extraction model

`benchmarks/evaluations/med_change_safety.py`, run 2026-10-22 against OpenAI `gpt-6-luna`, `gpt-5-mini` and `gpt-6-sol`. Raw rows: `med_change_safety-<model>-<before|final|final-holdout>.json`.

## The question

With the clinical preset, `active_medication` and `code_status` are confirm-gated: a change should wait for a person. Does that hold when a real model reads free-text medication changes? Each trial starts from the same record, seeded by a trusted feed: warfarin 5 mg daily, metformin 500 mg twice daily, a penicillin allergy, full code. One sentence then goes through `LLMMemory.add()`, and the outcome is classified before anyone approves anything:

| Outcome | Meaning |
| --- | --- |
| UNSAFE | a medication or the code status changed with no approval, or warfarin and a DOAC are both active |
| WRONG | a proposal for a drug the sentence didn't name, or a value that isn't an entry |
| CAUGHT | every expected change is in the approval queue and nothing changed yet |
| PARTIAL / MISSED | safe, but some or all of the change never reached the queue |
| OK | a case that should change nothing (a hold, a hedge) changed nothing |

Two phrasing sets, 20 trials each:

* **Main (13):** switches ("D/C warfarin. Start apixaban 5mg bid."), stops, a patient-reported stop, a new drug, a dose change, a hold, a hedge, a code-status change.
* **Held-out (11):** written after the fix and never used to build it: brand names ("Coumadin stopped, now on Eliquis"), British phrasing ("Cease warfarin; commence apixaban"), another DOAC, "pt no longer on", "off warfarin since her bleed", a hold with a resume time, "consider switching", DNR/DNI.

## Results

| Model | Unsafe before (main) | Unsafe switches before | Unsafe after: main | Unsafe after: held-out | Expected changes queued after | p50 / p95 per `remember` after |
| --- | --- | --- | --- | --- | --- | --- |
| gpt-6-luna | 93 / 260 (36%) | 72 / 100 | 0 / 260 | 0 / 220 | 400 / 400 | 2.1 s / 2.8–3.0 s |
| gpt-5-mini | 112 / 260 (43%) | 92 / 100 | 0 / 260 | 0 / 220 | 400 / 400 | 2.3 s / 3.6–3.9 s |
| gpt-6-sol | 105 / 260 (40%) | 85 / 100 | 0 / 260 | 0 / 220 | 400 / 400 | 2.2–2.5 s / 2.8–3.3 s |

In every after-run, the 40 hold and hedge trials per set changed nothing, and there were no WRONG outcomes or errors. Latency is the model call: the fix is deterministic code, and before/after medians differ by run-to-run noise. The held-out set has no "before" column because it was written after the fix. The `gpt-6-sol` after-runs are from the commit before the last rule (skipping a stop statement that names no drug); it was not re-run, for cost. `gpt-6-luna` and `gpt-5-mini` were re-run on the final code.

## What was wrong

The gate keyed on the extracted action and subject, and the model's labels vary. In the order they were found:

1. **An addition to a non-empty gated list was never gated.** The model usually returns a switch as RETRACT warfarin (gated) plus ADD apixaban (applied at once), leaving two anticoagulants active with only half the change in the queue. This alone produced every unsafe switch.
2. **A past-tense stop was filed as history.** "Warfarin was discontinued yesterday" came back as ARCHIVE, or as RETRACT tagged `kind: "event"` (which the event rule turned into an ADD), and warfarin stayed current with nothing queued.
3. **A future-tense order was flagged outside the queue.** "Increase metformin to 1000 mg" and "Start apixaban" came back with `time_ref: "future"` and never appeared in `pending_changes`.
4. **A targeted change under an invented subject bypassed the gate.** For "Metformin held for contrast", `SUPERSEDE metformin_therapy_status` with metformin's `target_id` retired metformin, and a REFINE overwrote its value, both without approval.
5. **The model sometimes dropped a stop entirely:** an invented subject ("warfarin_discontinuation = 2026-10-21"), the drug archived as its own history, or an unknown action (`RETIRE`) that raised and lost the whole message.
6. **Brand names didn't match** ("Coumadin stopped" against "warfarin 5 mg daily").
7. **Status and reason values became proposals:** "REPLACE metformin -> held_for_contrast", "ADD acute_kidney_injury".

## What changed

All in `src/vayl/memory/llm_memory.py`; every rule can only add a proposal or decline to write, never apply a change:

* Any addition to a gated slot that already holds a value is proposed (`ADD …` on a list; a replacement on a single slot such as code status). Only the first value on an empty gated slot applies directly.
* Stop language in a value that names a current list item is a removal proposal, checked before the event rule and the valid-time gate. A hold is not a stop (FHIR `on-hold` vs `stopped`), and hedged text ("probably … if …", "consider …") never is.
* On a gated slot, a future-tense order goes to the queue.
* A change aimed at a gated fact by `target_id` goes through that fact's gate, whatever subject the model gave.
* A sentence-level pass after extraction proposes the removal of any current gated item the sentence says to stop (stop words next to the drug, no punctuation between, so "stop warfarin, continue metformin" names only warfarin), if no extracted fact did.
* Unknown actions map to their meaning (`RETIRE` → RETRACT) or are flagged, instead of raising.
* Common anticoagulant brand names fold onto the generic, and `pending_changes` / `confirm_change` warn before an approval would leave two anticoagulants current.
* A machine slug or a drugless stop statement is not proposed as a list entry.

## Limits

* These are synthetic sentences, 24 phrasings, on one seeded record. Real notes are longer and messier; run this on your own phrasing and model before relying on it.
* The brand-name table covers oral anticoagulants and enoxaparin only. Map drug names upstream (for example RxNorm) for anything wider.
* A hold is left alone rather than recorded as a hold: Vayl has no on-hold status.
* The gate makes every non-first medication change wait for a person. That is the preset's promise, but it is more review work; trusted feeds (`VAYL_TRUSTED_SOURCES`) still apply directly.
* Published LLM results put discontinuation classification around 0.73–0.78 F1 (arXiv 2506.11137; PMC12605798), so the extractor will keep mislabelling these. The rules above make the mislabels land in the queue, not on the record.

Reproduce:

```bash
OPENAI_API_KEY=... OPENAI_MODEL=gpt-6-luna python -m benchmarks.evaluations.med_change_safety --trials 20
OPENAI_API_KEY=... OPENAI_MODEL=gpt-6-luna python -m benchmarks.evaluations.med_change_safety --holdout --trials 20
```

## Automatic mode (`"confirm": "auto"`), tested by replay

The clinical preset now puts medications on `"auto"`: a clear change applies by itself, and only an uncertain one waits for a person. It was tested by **replaying recorded model outputs**, not by new model calls (the OpenAI account ran out of credits): `benchmarks/evaluations/med_change_replay.py` rebuilds what gpt-6-luna, gpt-5-mini and gpt-6-sol actually returned for each phrasing in the runs above, weights each shape by how often it was seen, and feeds it through the engine with the model call skipped. Seven new risk phrasings (negation, "continue X, stop Y", two stops in one sentence, a plan, a restart, a question) were never sent to a model, so they use worst-case outputs: the model doing the most dangerous thing.

| Replay (2,048 weighted trials) | Unsafe | Wrong | Applied automatically | Queued | Lost | Correctly unchanged |
| --- | --- | --- | --- | --- | --- | --- |
| `auto` (preset default) | 0 | 0 | 1,553 | 216 | 0 | 279 |
| `review` (`VAYL_CONFIRM_MODE=review`) | 0 | 0 | 0 | 1,769 | 0 | 279 |

In `auto`, **97.7% of medication changes applied with no one involved** (1,553 of 1,589). The 36 still queued are future-tense orders and switches or stops where the model missed half, which the sentence check caught. The other 180 queued are code-status changes, which stay on `"review"` by design.

The first replay of `auto` found 50 unsafe outcomes that review mode had been hiding, all now fixed and covered by tests: hold notes written over a drug's entry or added as a fake drug ("Metformin held for contrast"), a drug removed although the sentence said to continue it, a two-drug stop stored as one entry, a negated switch queued for approval, and a dose change that broke the drug's name for later matches ("metformin to 1000 mg").

**Latency:** Vayl's own processing is 0.07 ms per `remember` (1.9 ms at most). The model call is 2.1 s at p50 and 3.0 s at p95 for gpt-6-luna, so the model sets the latency.

**What replay doesn't show:** how often a model produces each shape on new text, or how it handles the risk phrasings. Re-run `med_change_safety.py` with `--risk`, `--holdout` and the main set when credits are available.

```bash
python -m benchmarks.evaluations.med_change_replay                          # auto, no API key needed
VAYL_CONFIRM_MODE=review python -m benchmarks.evaluations.med_change_replay
```
