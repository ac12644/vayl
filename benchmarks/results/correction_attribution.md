# Correction attribution: telling a mistaken auto-apply from a real later change

`benchmarks/evaluations/correction_attribution.py`, run 2026-10-22. No model calls. Each trial auto-applies one medication change (a dose change, a start or a stop) and then a follow-up write arrives. The follow-up is labelled by hand as a **correction** (the auto-apply was wrong) or a **change** (the world moved on). There are 11 scenarios over 5 drugs, so 55 trials.

The baseline is the signal used in the revert-rate literature and in the closest prior work: any later write that touches an auto-applied write counts as an error of it.

| Scenario | Truth | Vayl flags | Naive flags |
| --- | --- | --- | --- |
| reversed the next day | correction | 5/5 | 5/5 |
| corrected by a note, new value | correction | 5/5 | 5/5 |
| "still on" the old dose | correction | 5/5 | 5/5 |
| clinician edits the entry | correction | 5/5 | 5/5 |
| auto-started drug removed the same day | correction | 5/5 | 5/5 |
| auto-stop undone by the feed | correction | 5/5 | 5/5 |
| titrated up a week later | change | 0/5 | 5/5 |
| titrated up two days later | change | 0/5 | 5/5 |
| stopped a month later | change | 0/5 | 5/5 |
| restarted a month later | change | 0/5 | 5/5 |
| dose reduced back after a side effect | change | **5/5 (wrong)** | 5/5 |

| | Precision | Recall | False corrections |
| --- | --- | --- | --- |
| Vayl (reversal within window, correction language, edit) | 0.86 | 1.00 | 5 |
| Naive (any later touch) | 0.55 | 1.00 | 25 |

## Limits

* The scenarios and their labels were written by hand for this test, not drawn from real records. Treat them as a mechanism check, not an accuracy estimate.
* A deliberate clinical reversal inside the window ("reduce back to 5 mg after the bleed") looks exactly like a correction and is counted as one. This hard case is left in on purpose. A reason given in the text ("after", "due to") could separate it, but that rule has not been built or tested.
* Evidence is kept per memory space. A deployment with one space per patient needs it pooled across spaces before `"confirm": "earned"` is useful.

## Sensitivity to the time window

False corrections / missed corrections over the same 55 trials:

| Window (days) | 0.5 | 1 | 3 | 7 (default) | 14 | 30 | 60 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| False / missed | 0 / 10 | 0 / 10 | 0 / 0 | 5 / 0 | 5 / 0 | 5 / 0 | 15 / 0 |

The perfect score at 3 days is an artefact: the hard case (a deliberate reversal 3 days later) falls just outside that window, and the scenario timings were chosen by hand. The result is highly sensitive to the window and to scenario timing, which is why this test cannot stand as accuracy evidence. That needs real records, or a larger set labelled independently.
