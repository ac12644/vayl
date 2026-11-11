---
description: Block actions on unsafe memory, queue gated changes for a person, and set how shared spaces resolve conflicts.
icon: shield-halved
---

# Safety and gating

These tools stop an agent from acting on memory that is disputed, uncertain, stale, or just changed, and hold changes to sensitive slots until a person approves them. `check_before_act` and `safe_recall` return a deterministic verdict with reasons; `pending_changes`, `confirm_change` and `reject_change` run the human-approval queue. For the end-to-end workflow, see the [Safety gates & human approval guide](../guides/safety-gates-and-human-approval.md).

All seven tools take the memory-space arguments `user_id="default"`, `agent_id=""`, `run_id=""`. Outputs below were captured by running the tools offline.

## The safety policy

`check_before_act` and `safe_recall` share one policy, set per call:

| Argument | Default | Blocks when |
| --- | --- | --- |
| `min_confidence` | `0.7` | A fact's confidence is below this. |
| `require_active` | `True` | A fact is retired (superseded or historical). |
| `block_on_flagged` | `True` | A fact is flagged: an unresolved conflict, or a change awaiting approval. |
| `max_staleness_days` | `0` (off) | A fact was last set more than this many days ago. |
| `block_on_recent_change_days` | `0` (off) | A fact that **superseded** an earlier value was set less than this many days ago. A first-time value never trips it. |

Any value of `0` or below turns the two day windows off. Both tools only evaluate active and flagged facts, so `require_active` is a backstop: a retired value never reaches the check in the first place.

## check\_before\_act

```python
check_before_act(subject: str, user_id: str = "default", agent_id: str = "", run_id: str = "",
                 min_confidence: float = 0.7, require_active: bool = True,
                 block_on_flagged: bool = True, max_staleness_days: float = 0,
                 block_on_recent_change_days: float = 0) -> str
```

**Capability** `read` · **Annotations** read-only, no LLM call

Evaluates every active and flagged fact for `subject` against the policy. Call it before any irreversible action (a payment, an email, a config change). The verdict is deterministic.

```json
{"name": "check_before_act", "arguments": {"subject": "state", "user_id": "proj_7"}}
```

```
✅ SAFE to act on 'state'.
  current: state = Zustand
```

A blocked verdict lists one bullet per failing check. These are the real reason strings:

| Cause | Reason line |
| --- | --- |
| No active fact for the subject | `• no active fact for this subject — nothing safe to act on` |
| Flagged (disputed, or awaiting approval) | `• unresolved conflict (FLAGGED) — the value is disputed` |
| Low confidence | `• confidence 0.5 < required 0.7` |
| `max_staleness_days=90` | `• stale: last set 120d ago > 90d limit` |
| `block_on_recent_change_days=1` | `• recently changed 0.0d ago — may be unsettled` |

For example:

```
⛔ BLOCKED — do NOT act on 'api_timeout':
  • confidence 0.5 < required 0.7
```

While a change to a confirm-required slot is pending, the proposal is a flagged fact on that subject, so `check_before_act` blocks until someone decides. Each reason is listed once, however many proposals are pending.

## safe\_recall

```python
safe_recall(question: str, user_id: str = "default", agent_id: str = "", run_id: str = "",
            min_confidence: float = 0.7, require_active: bool = True,
            block_on_flagged: bool = True, max_staleness_days: float = 0,
            block_on_recent_change_days: float = 0, critical_categories: str = "") -> str
```

**Capability** `read` · **Annotations** read-only, open-world (calls the LLM and embedder)

Like `recall`, but for an answer the agent will act on. It runs the same retrieval as `recall`, checks **every active fact placed in the model's context** against the policy, and also blocks if any of those subjects has a flagged fact. If every check passes it returns the model's answer (free text); otherwise it withholds the answer:

```
⛔ WITHHELD — not safe to act on this answer:
  • confidence 0.5 < required 0.7
```

With no current fact behind the answer:

```
⛔ WITHHELD — not safe to act on this answer:
  • no current fact supports this — nothing safe to act on
```

A flagged fact on a subject in context adds `• unresolved conflict on '<subject>' — the value is disputed`.

{% hint style="warning" %}
**Small spaces are checked whole.** When a space holds `VAYL_RECALL_CONTEXT` (default 40) facts or fewer, every active fact goes into the context, so one low-confidence fact anywhere in the space withholds every answer. Keep unrelated facts in separate [memory spaces](../core-concepts/memory-spaces.md), or use `check_before_act` on the specific subject.
{% endhint %}

`critical_categories` works as in [`recall`](memory.md#recall), and matters more here: the gate can only judge facts that reached the context.

## pending\_changes

```python
pending_changes(user_id: str = "default", agent_id: str = "", run_id: str = "") -> str
```

**Capability** `read` · **Annotations** read-only

Lists proposed changes to **confirm-required** slots (slots declared with `"confirm": true` in the `VAYL_SLOT_SCHEMA` file). Such a change is stored as `[FLAG]` and not applied; the current value stands until a person decides.

```
2 change(s) awaiting approval:
  #2 REPLACE active_medication: 'warfarin 5mg daily' -> 'apixaban 5mg twice daily'
        said: 'Switch her to apixaban 5mg twice daily.'
  #3 REMOVE active_medication: 'warfarin 5mg daily'
        said: 'Maybe stop the warfarin.'

Approve with confirm_change(memory_id), discard with reject_change(memory_id).
```

A new item proposed for a list slot that already has entries shows as `ADD active_medication: 'apixaban 5mg bid'`; `confirm_change` adds it next to the existing items.

A `REMOVE` shows the value that would be removed; there is no new value. An empty queue returns `No changes awaiting approval.`

## confirm\_change

```python
confirm_change(memory_id: int, user_id: str = "default", agent_id: str = "", run_id: str = "",
               decided_by: str = "") -> str
```

**Capability** `approve` (admin and member roles; agent keys lack it, so an agent cannot approve its own proposal) · **Annotations** write, not destructive

Applies a pending change. The approver recorded in the audit log is the authenticated caller as `name [id]`; `decided_by` is only an optional note beside it.

```
Approved #2: SUPERSEDE active_medication = apixaban 5mg twice daily
```

Approving a removal returns, for example, `Approved #2: RETRACT active_medication = warfarin 5mg daily`, and `history` then shows `(retracted: warfarin 5mg daily)  [HISTORICAL]`. The audit entry reads `#2 RETRACT active_medication by <name> [<principal id>]`. If the id isn't pending, or the value it would replace has changed since the proposal was made:

```
#2 is not awaiting approval. It may have been decided already, or the value it would have replaced has since changed — in which case the proposal is stale and should be re-made against the current value.
```

An agent key gets:

```
Access denied: 'confirm_change' requires the 'approve' capability; your role(s) ['agent'] do not grant it.
```

## reject\_change

```python
reject_change(memory_id: int, user_id: str = "default", agent_id: str = "", run_id: str = "",
              decided_by: str = "") -> str
```

**Capability** `approve` · **Annotations** write, not destructive

Discards a pending change. The current value stands; the proposal is kept in `history` as `HISTORICAL`, because that someone proposed it is worth auditing. `approve` is required here too: an agent quietly discarding proposals would empty the queue as surely as approving them.

```
Discarded #3. The current value is unchanged.
```

If the id isn't pending: `#2 is not awaiting approval.`

## set\_reconcile\_policy

```python
set_reconcile_policy(mode: str = "RECENCY", authority: dict | None = None,
                     user_id: str = "default", agent_id: str = "", run_id: str = "") -> str
```

**Capability** `admin` · **Annotations** write, not destructive

Sets how a **shared** space (one that several agents or people write to, each `remember` carrying a `source`) resolves conflicts between different sources:

| `mode` | Behaviour |
| --- | --- |
| `RECENCY` | Newer assertion wins. The default; contributors are equally trusted. |
| `AUTHORITY` | A higher-ranked source wins. A lower-ranked source contradicting it is flagged, not applied. Pass `authority` as `{source: rank}`, higher is more authoritative. |
| `REVIEW` | Every cross-source conflict is flagged for a person. |

A source correcting its own earlier fact always supersedes, whatever the mode.

```json
{"name": "set_reconcile_policy", "arguments": {"mode": "AUTHORITY", "authority": {"fhir": 10, "agent": 1}, "user_id": "org:acme"}}
```

```
Reconciliation policy for this space set to AUTHORITY.  authority ranks: {'fhir': 10, 'agent': 1}
```

An unknown mode returns `Unknown mode 'VOTE'. Use RECENCY, AUTHORITY, or REVIEW.`

## get\_reconcile\_policy

```python
get_reconcile_policy(user_id: str = "default", agent_id: str = "", run_id: str = "") -> str
```

**Capability** `read` · **Annotations** read-only

```
Policy: AUTHORITY
  authority ranks: {'fhir': 10, 'agent': 1}
```

A space with no policy set returns `Policy: RECENCY (default — newer assertion wins).`

## Trusted sources

`VAYL_TRUSTED_SOURCES` (for example `fhir,hl7`) names sources whose changes are already authorized upstream, so they skip the confirmation gate. Because `source` is a free string, writing as a trusted source is bound to keys: only a principal whose name equals the source (create an integration key named `fhir`), or a caller with `approve`, may use it. Anyone else gets `Access denied` and the attempt is audited. See [`remember`](memory.md#remember).

## Next steps

<table data-view="cards"><thead><tr><th></th><th></th><th data-hidden data-card-target data-type="content-ref"></th></tr></thead><tbody><tr><td><h4><i class="fa-gear" style="color:$primary;">:gear:</i> Safety gates &#x26; human approval</h4></td><td>The full workflow for gating writes and requiring approval.</td><td><a href="../guides/safety-gates-and-human-approval.md">safety-gates-and-human-approval.md</a></td></tr><tr><td><h4><i class="fa-file-signature" style="color:$primary;">:file-signature:</i> Accountability</h4></td><td>Record what the agent decided, on a signed audit chain.</td><td><a href="accountability.md">accountability.md</a></td></tr><tr><td><h4><i class="fa-database" style="color:$primary;">:database:</i> Memory</h4></td><td>The store-and-recall tools these gates protect.</td><td><a href="memory.md">memory.md</a></td></tr></tbody></table>
