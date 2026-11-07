# High-stakes case histories: wrong changes, severe changes and questions asked

`benchmarks/evaluations/high_stakes_episodes.py`, runs 2026-11-05 to 2026-11-07. Each episode is a short case history (clinical, finance or support) fed to Vayl one message at a time. At the end the record is scored against the expected state. A simulated reviewer answers every question Vayl asks, and it approves only correct proposals.

**Not for public quoting.** The episodes are written by an agent, not by a clinician or advisor, and the reviewer never makes a mistake. Treat these figures as engineering signal only.

## Metrics

- **Perfect**: the episode ends with the record fully correct.
- **Wrong changes per 100**: stale values left current plus forbidden values written, per 100 episode runs.
- **Severe**: a wrong removal, or a forbidden value in a critical or medication slot.
- **Asks per message**: questions put to the reviewer, per message. The product guard is 0.50 at most.

Each run covers every episode 3 times: 90 runs on the tuned set (30 episodes) and 45 on the holdout set (15 episodes). The intervals in brackets are 95% episode-clustered bootstrap intervals for the change in perfect runs against p0 (`benchmarks/evaluations/compare.py`).

## Shipped presets, by stage (Haiku 5.5 unless noted)

| Stage | Tuned perfect | Tuned wrong/100 | Tuned severe | Tuned asks/msg | Holdout perfect | Holdout wrong/100 | Holdout severe | Holdout asks/msg |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| p0, baseline after scorer fixes | 42/90 | 53 | 19 | 0.36 | 22/45 | 56 | 6 | 0.23 |
| pB, after defect fixes R2–R9 | 66/90 | 13 | 4 | 0.68 | 38/45 | 9 | 1 | 0.50 |
| vCoff, R14 tiering, verdict pass off | 68/90 | 26 | 12 | 0.45 | 37/45 | 13 | 1 | 0.35 |
| vC2, same draw, verdict pass on (R12) | 78/90 | 7 | 5 | 0.38 | 42/45 | 2 | 2 | 0.30 |
| Sonnet 5.5, pB code (R11) | 78/90 | 1 | 0 | 0.51 | 43/45 | 4 | 0 | 0.41 |

vCoff and vC2 replay one logged Haiku draw through the code, so their difference comes from the verdict pass alone. Every other row is a fresh model draw, and fresh draws of the same code move by about 5 perfect runs.

### Reference: no gates and approve-everything (pB code)

| Config | Tuned perfect | Tuned wrong/100 | Tuned severe | Tuned asks/msg | Holdout perfect | Holdout severe |
| --- | --- | --- | --- | --- | --- | --- |
| off, no gates | 62/90 | 31 | 16 | 0 | 31/45 | 9 |
| review, a person approves every change | 78/90 | 14 | 7 | 1.48 | 39/45 | 1 |

`review` asks four times as often as the shipped presets and still ends with more severe errors. With a fallible reviewer (R10), once 20% of wrong proposals get approved, `review` has more severe errors than the shipped presets.

## Research bets

| Bet | Result | Decision |
| --- | --- | --- |
| R10 fallible reviewer | `review` loses its lead once the reviewer approves 20–45% of wrong proposals | Keep approval narrow |
| R11 Sonnet 5.5 | Holds the same perfect count as vC2 with 0 severe, but asks 0.51 per message | Model size is a lever for severe errors; cost decision open |
| R12 verdict pass | Severe 12 → 5, wrong changes per 100 26 → 7 on the same draw | Keep; `VAYL_VERDICT` on by default since 2026-11-07 (one extra call per message, only when a slot schema is declared) |
| R13 closed-slot router | 1 lost fact in 270 runs came from free-named rows; no other person's fact landed in an owner slot | Skipped |
| R14 quote-checked tiering | A hedged stop or replacement applies when the verdict pass agrees (never a new list item). Part of the vCoff → vC2 change, where asks went 0.45 → 0.38 | Keep |
| R15 slim prompt (`VAYL_PROMPT=slim`) | Two tuned draws: severe 5 → 1 and 1, forbidden 3 → 0, perfect 78 → 82 and 83. Rejected proposals 31 → 58 and 54, asks 0.38 → 0.48 and 0.47. Holdout unchanged (42/45, severe 2, asks 0.30) | Opt-in only: fewer severe errors, but more needless questions |
| R16 disagreement routing | AUROC 0.54–0.62 against a bar of 0.75 | Dropped |
| R17 fact's own sentence in context | Shipped | Keep |
| R18 long notes (5,000+ characters of copied-forward history) | Wrong changes per 100 did not double: 7 → 4 tuned, 2 → 4 holdout. Asks rose 0.38 → 0.59 tuned (above the 0.50 guard) and 0.30 → 0.42 holdout. The tuned run lost 5 of 90 runs (`ep_s09`, `ep_s10`) when the session ended | No chunking; asks on long notes are the open problem |
| R19 pasted or retrieved text only proposes | Unit tests across 3 presets × 4 sources | Keep (`VAYL_PROPOSE_ONLY_SOURCES`) |
| R20 `retired_at` | Set in the row metadata when a fact leaves ACTIVE, so no migration is needed; covered by `tests/test_store.py` | Keep |

## Guards, after all changes

- Scripted extractor: 135/135 perfect, in all three configs on both sets.
- `med_change_replay`: 0 unsafe, 0 wrong, 1,557 auto-applied.
- `approval_safety`, linked groups: 0/709 unsafe in both reviewer behaviours.
- `tests/`: all pass.

## Caveats

- The reviewer is an oracle. Real reviewers approve some wrong proposals; see R10.
- The episodes are agent-written. Their messages are short and clean except in the R18 runs.
- Results come from one model, Haiku 5.5. Sonnet 5.5 ran once.
- No clean holdout remains. The holdout set has been read during fixes, so its numbers are optimistic too.
- Draw-to-draw variation is about ±5 perfect runs out of 90. Single-draw differences smaller than that are noise.
