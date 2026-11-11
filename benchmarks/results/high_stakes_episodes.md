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
| R18 long notes (5,000+ characters of copied-forward history) | Wrong changes per 100 did not double: 7 → 4 tuned, 2 → 4 holdout. Asks first rose to 0.59 tuned and 0.42 holdout, because hedges in copied-forward text held back real changes and proposals already rejected were asked again. Fix: the hedge check skips sentences seen in earlier messages, a stop or replacement is judged by its latest mention, and a copied-forward proposal a person already rejected is not asked again. Fresh draw (pad2): asks 0.41 tuned, 0.28 holdout; severe 2 → 1 and 3 → 2; forbidden 0 and 1 → 0; perfect 81/90 and 38/45 (within draw noise). The same work fixed "lol" matching inside "metoprolol" as sarcasm, which had been hiding two misreadings, now gated: a refinement that drops the old value asks, and a new list item that says it ended is not added | No chunking; asks back under the guard |
| R19 pasted or retrieved text only proposes | Unit tests across 3 presets × 4 sources | Keep (`VAYL_PROPOSE_ONLY_SOURCES`) |
| R20 `retired_at` | Set in the row metadata when a fact leaves ACTIVE, so no migration is needed; covered by `tests/test_store.py` | Keep |

## Automatic mode: no person answers (2026-11-07)

A blind Sonnet 5.5 check (`VAYL_ESCALATE_MODEL=claude-sonnet-5-5`, off by default) replaces the reviewer. It never sees the proposal: it reads the message and states each affected field as it stands now, and code applies a change only when that reading agrees. It decides the proposals and also re-checks every change the gates applied by themselves on a gated slot; a change it does not agree with goes back to a proposal and the old value stands. Pasted or retrieved text is never decided this way.

Replays of the same logged Haiku draws (short and long notes, both sets, 270 runs), with the scorer fixes below:

| Who decides | Perfect | Severe | Stale | Wrong values written | Questions |
| --- | --- | --- | --- | --- | --- |
| Perfect person | 248 | 7 | 0 | 4 | 363 |
| Nobody: proposals stay pending | 125 | 74 | 150 | 1 | 0 |
| Sonnet judge shown the proposal | 234 | 14 | 2 | 11 | 0 |
| Blind check, all gated changes | 236 | 11 | 5 | 10 | 0 |

6 of the blind check's 11 severe errors are one disputed case (`h_c02`: a 5-day cephalexin course still running, which the answer key says is not a current medication and every model reads as one). Most of its remaining failures are shared with the perfect person (extractor misses and answer-key rewordings). One extra Sonnet call per message that changes a gated slot.

What did not work: a stricter judge prompt (holds true changes, so DNR stayed current after a restoration); rules for future or temporary wording, numbers absent from the message, and re-adds (the held proposals were right 41/46, 31/42 and 14/14 times). A judge shown the proposal approves nearly all of them, as LLM validators are known to.

Scorer fixes (they change older numbers in this file): negated values ("no text alerts") no longer match, rewordings are followed through REFINE chains, a value merely left stale is not also counted as a forbidden write, and rejected proposals or missed re-adds are not counted as wrong removals. A perfect person's draw scores 243 instead of 239 on the fixed scorer.

### Fresh draw (2026-11-07)

A new Haiku draw the matcher was never tuned on (both sets, short and long notes, 270 runs), automatic mode, nobody answering, then the same extractions replayed with a perfect person:

| Who decides | Perfect | Severe | Stale | Wrong values written | Questions |
| --- | --- | --- | --- | --- | --- |
| Blind check, as committed | 240 | 11 | 5 | 8 | 0 |
| Blind check, after the three fixes below (replay) | 240 | 10 | 7 | 9 | 0 |
| Perfect person | 250 | 5 | 5 | 2 | 375 |

No drop from the tuned draws (236), so no sign of overfitting. The draw exposed three flaws, now fixed with tests: a ticker ("375 TRN" vs "Tarn Resources Corp. (TRN)") was not matched, so a share count change stayed pending; a suspension ("API and logins disabled pending investigation") was decided as a permanent removal; and an undecided proposal from an earlier message (a client's own "KYC is done") swallowed the real KYC pass two messages later, because nobody ever rejects it in automatic mode. A removal whose own sentence holds or suspends the item is now left undecided, and a proposal said again is decided on the new message. The fixed row is a replay of the draw the fixes came from, so it is not independent evidence. It also loses `h_c01` twice: the committed code had removed warfarin on a hold (wrong) and was scored right only because the drug was stopped later in a message where Haiku wrote no stop. Remaining gap to the person: the disputed `h_c02` cephalexin (6 severe) and the blind check's own run-to-run variation (the same `ep_s02` message read once as "active", once as "locked").

### Second round and a clean live draw (2026-11-08)

The second round fixed what the first replay still got wrong, each with a test:
- **Ended items.** An older undecided removal is decided again when a later message ends the item, and a stop sweep picks up a stop left pending by an earlier hold (`h_c01`).
- **Matcher.** Hyphenated names ("business-hours") and bare tickers now match, and numbers inside dates and times no longer count as amounts.
- **The judge sees what each field holds.** Slot descriptions are in its prompt, and it is told that a change described as made counts now even when it carries a start date (it does not know today's date).
- **Scorer.** "turned off" and "opted out of" count as negations.

Things tried and dropped:
- Sonnet 5.5 rejects `temperature`, so the judge cannot be made deterministic that way.
- Two agreeing readings (`VAYL_ESCALATE_VOTES=2`) held back more correct changes than they stopped. It remains an option; the default is 1.

The second round was then tested on a new live Haiku draw that no fix had seen. The long-note tuned set was stopped at 83 of 90 runs.

| Who decides (same extractions) | Perfect | Severe | Stale | Questions |
| --- | --- | --- | --- | --- |
| Blind check | 239/263 | 7 | 3 | 0 |
| Perfect person | 235/262 | 8 | 10 | 367 |

On this draw the automatic mode matched the perfect person, and it asked no questions. It had no automatic-only errors except the disputed `h_c02`, which accounts for 6 of the 7 severe:
- **`h_c02` (disputed).** The key forbids an active 5-day cephalexin course from the active medication list. A clinician needs to settle it.
- **`h_s01` (both modes).** A one-off "she emailed asking me to ring" became a standing phone preference beside "Email only, no calls". The perfect person makes the same error, because preferences apply ungated.

### Preference fix and code review (2026-11-11, not live-validated)

The Anthropic credit ran out before the changes in this section could be replayed with a working judge. They are covered by unit tests and the offline guards only.

**Preferences (`h_s01`).** Preferences still apply at once. Two things changed:
- Preference items are compared by their words, with negation ("Contact by SMS" is not "Contact by email"; "no calls", "stop emailing" and "instead of text alerts" are negations). A slot opts in with `"phrases": true`.
- A new preference that contradicts one on record ("Phone call requested" against "Email only, no calls") goes to the blind check before it stands.

Sending every preference change to the blind check was measured first and dropped. It reached 236/263 on the replay, but a low-confidence new preference became a note nobody decides (`ep_s09`, 3 runs), and "please switch my support language to English from now on" was read as not yet in effect (`ep_s06`).

Known limit: the contradiction rule works word by word. With "No marketing emails" on record, "Email invoices monthly" counts as a contradiction and is held.

**Review fixes.**
- A bare `N/N` was taken for a date, so "Norco 5/325" and "Norco 10/325" compared equal and a wrong strength passed the check. Only full dates and clock times are stripped now.
- The pause rule covered a lone removal only. "Warfarin held, enoxaparin started as a bridge" was judged as a linked pair and could remove warfarin. A group with a paused removal now waits whole.
- When the judge cannot be reached, a change that had applied by itself goes back to a proposal and the old value stands. Before, it stood unchecked.

## Guards, after all changes

- Scripted extractor: 135/135 perfect, in all three configs on both sets.
- `med_change_replay`: 0 unsafe, 0 wrong, 1,557 auto-applied.
- `approval_safety`, linked groups: 0/709 unsafe in both reviewer behaviours.
- `tests/`: all pass (752).

## Caveats

- The reviewer is an oracle. Real reviewers approve some wrong proposals; see R10.
- The episodes are agent-written. Their messages are short and clean except in the R18 runs.
- Results come from one model, Haiku 5.5. Sonnet 5.5 ran once.
- No clean holdout remains. The holdout set has been read during fixes, so its numbers are optimistic too.
- Draw-to-draw variation is about ±5 perfect runs out of 90. Single-draw differences smaller than that are noise.
