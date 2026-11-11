---
description: Store facts, ask what is true now, correct or retract them, and read their history.
icon: database
---

# Memory

These eight tools store facts from plain language and answer from the current, reconciled set. `remember` extracts facts with the LLM and reconciles each one against what is already stored; `recall` answers only from active facts; `history` shows every value a subject has held.

All eight take the memory-space arguments `user_id="default"`, `agent_id=""`, `run_id=""` (see [Memory spaces & tenants](../core-concepts/memory-spaces.md)). Example outputs below were captured by running the tools offline with the extraction and answering models stubbed. Subject names such as `state` are chosen by the extraction model, so yours may differ.

## remember

```python
remember(text: str, user_id: str = "default", agent_id: str = "", run_id: str = "",
         metadata: dict | None = None, source: str = "") -> str
```

**Capability** `write` · **Annotations** write, not destructive, open-world (calls the LLM and embedder)

Extracts one or more facts from `text` and reconciles each against the active facts in the space. A new value for a subject supersedes the old one instead of being appended; a removal retracts; several facts in one message are all captured.

| Argument | Description |
| --- | --- |
| `text` | The statement. May contain several facts. |
| `metadata` | Merged into the metadata of every fact stored by this call, e.g. `{"category": "allergy"}`. The `category` key decides whether a fact counts as critical for `critical_categories`. |
| `source` | Who or what asserted the fact (an agent id, a person, a connector). Stored as provenance and used by [source-aware reconciliation](safety-and-gating.md#set_reconcile_policy). |

The return lists one `[ACTION] subject = value` entry per extracted fact. The action is one of the nine reconciliation actions:

| Action | Meaning | Real return |
| --- | --- | --- |
| `ADD` | New subject, no conflict. | `Stored: [ADD] state = Redux` |
| `SUPERSEDE` | Replaces the active value; the old one becomes `SUPERSEDED`. | `Stored: [SUPERSEDE] state = Zustand` |
| `DEDUP` | Already known; nothing new written. | `Stored: [DEDUP] state = Zustand` |
| `REFINE` | More detail on the same fact; the earlier wording stays in history. | `Stored: [REFINE] api_style = REST over HTTP/2 with JSON bodies` |
| `COEXIST` | Same subject, different **scope**; both stay active. | `Stored: [ADD] deploy_day = Tuesday; [COEXIST] deploy_day = Friday` |
| `FLAG` | Surfaced, not applied: an ambiguous or low-confidence change, a future-tense statement, a cross-source conflict under `AUTHORITY`/`REVIEW`, or a change to a confirm-required slot. | `Not applied, flagged for review: [FLAG] hosting = Fly.io` |
| `ARCHIVE` | A past-tense statement; recorded as history, never active. | `Stored: [ARCHIVE] hosting = Heroku` |
| `RETRACT` | A removal with no replacement. | `Stored: [RETRACT] primary_database = Postgres` |
| `SKIP` | Not a durable fact (hypothetical, sarcasm). Nothing is stored, and it's reported on its own line. | `Not stored (hypothetical, sarcasm, or nothing to change): language = Rust` |

When the text holds no durable fact at all:

```
No durable fact found (looked like chatter or a question).
```

Several facts in one call:

```json
{"name": "remember", "arguments": {"text": "Alice leads Platform and Bob leads Mobile.", "user_id": "proj_7", "metadata": {"team": "eng"}}}
```

```
Stored: [ADD] platform_lead = Alice; [ADD] mobile_lead = Bob
```

{% hint style="warning" %}
**Trusted sources.** A `source` listed in `VAYL_TRUSTED_SOURCES` skips the confirmation gate, so claiming one is itself an approval. Since 0.6.0 it is allowed only for a key whose principal name equals that source, or a caller with `approve`. Anyone else gets this (and the attempt is audited):

```
Access denied: 'fhir' is a trusted source (VAYL_TRUSTED_SOURCES), so its changes skip the confirmation gate. Only a key named 'fhir', or one with the 'approve' capability, may write as it.
```
{% endhint %}

## recall

```python
recall(question: str, user_id: str = "default", agent_id: str = "", run_id: str = "",
       explain: bool = False, include_history: bool = False,
       critical_categories: str = "") -> str
```

**Capability** `read` · **Annotations** read-only, open-world (calls the LLM and embedder)

Answers a question from the **active** facts. Superseded and retracted facts are not loaded at all by default, so a stale value cannot come back as current. The answer is free text written by the answering model; it says it doesn't know rather than guessing when the facts don't support an answer.

| Argument | Description |
| --- | --- |
| `question` | Natural-language question, including multi-hop questions over current facts. |
| `explain` | Append the exact facts placed in the model's context, with confidence, source, and what each superseded. |
| `include_history` | Also load retired facts, tagged `(history)`. Use only for questions about the past ("what did we use before?"). |
| `critical_categories` | Comma-separated categories that always reach the context, whatever their rank. Empty means use `VAYL_CRITICAL_CATEGORIES`, never "none". |

**How facts reach the model.** When the space holds `VAYL_RECALL_CONTEXT` (default 40) facts or fewer, every active fact is passed to the model. Above that, hybrid semantic + lexical retrieval picks the top 40, and facts in critical categories are added on top without using ranked slots.

```json
{"name": "recall", "arguments": {"question": "what state library do we use?", "user_id": "proj_7", "explain": true}}
```

Representative output (the first line is model-generated; the fact list is deterministic):

```
Zustand.

Based on these facts:
  • #2 state = Zustand  [conf 0.95, supersedes #1]
  • #7 deploy_day = Tuesday  [conf 0.95]
  • #8 deploy_day = Friday  [conf 0.95]
  • #9 platform_lead = Alice  [conf 0.95]
  • #10 mobile_lead = Bob  [conf 0.95]
  • #11 api_timeout = 30s  [conf 0.5]
```

Every active fact appears in the list because this space is under 40 facts. A fact written with a `source` shows `from <source>` inside the brackets. If no facts were used, the answer is followed by `(no stored facts were used)`.

## recall\_related

```python
recall_related(question: str, user_id: str = "default", agent_id: str = "", run_id: str = "") -> str
```

**Capability** `read` · **Annotations** read-only, open-world

Answers relational, multi-hop questions ("who owns the company Bob works for?") over the Neo4j entity graph when `VAYL_GRAPH` is on. Without the graph it falls back to the same path as `recall`. The return is model-generated free text, for example `Alice leads Platform.`

## forget

```python
forget(text: str, user_id: str = "default", agent_id: str = "", run_id: str = "") -> str
```

**Capability** `write` · **Annotations** write, not destructive, open-world

Retracts a fact: a removal with no replacement ("we dropped Sentry"). The fact's status becomes `SUPERSEDED` and a `HISTORICAL` tombstone `(retracted: <value>)` is written, so it never comes back as current but stays in `history` for audit. There is no separate "retracted" status.

```
Retracted (retained in history for audit): monitoring = Sentry
```

When nothing current matches:

```
Nothing matching to retract (that fact isn't currently stored).
```

On a slot declared with `"confirm": true`, a removal is proposed, not applied. The value stays current until someone approves it:

```
Proposed for removal, awaiting approval (the value stays current until someone approves it with confirm_change; see pending_changes): code_status = full code
```

Asking again while that proposal is pending doesn't queue a second one:

```
Already awaiting approval (see pending_changes): code_status
```

`forget` retains history. To erase data for privacy, use [`delete`](compliance-gdpr.md#delete).

## history

```python
history(subject: str, user_id: str = "default", agent_id: str = "", run_id: str = "") -> str
```

**Capability** `read` · **Annotations** read-only

Every value a subject has held, oldest to newest, with status. `subject` is the key shown by `list_memories`.

```
History for 'state' (oldest → newest):
  1. Redux  [SUPERSEDED]
  2. Zustand  [ACTIVE]
```

After a retraction:

```
History for 'monitoring' (oldest → newest):
  1. Sentry  [SUPERSEDED]
  2. (retracted: Sentry)  [HISTORICAL]
```

An unknown subject returns `No history for 'nope'.`

## list\_memories

```python
list_memories(user_id: str = "default", agent_id: str = "", run_id: str = "") -> str
```

**Capability** `read` · **Annotations** read-only

Lists active facts with their `#id`, then flagged facts, then up to 20 of the most recent retired rows (superseded, retracted, archived).

```
• state = Zustand  (#2)
• deploy_day = Tuesday  (#7)
• deploy_day = Friday  (#8)
• platform_lead = Alice  (#9)
• mobile_lead = Bob  (#10)
• api_timeout = 30s  (#11)
⚠ hosting = Fly.io  (#6, flagged — needs confirmation)

— history (superseded / retracted / archived) —
  ◦ monitoring = (retracted: Sentry)  [HISTORICAL]
  ◦ monitoring = Sentry  [SUPERSEDED]
  ◦ hosting = Heroku  [HISTORICAL]
  ◦ primary_database = (retracted: Postgres)  [HISTORICAL]
  ◦ primary_database = Postgres  [SUPERSEDED]
  ◦ state = Redux  [SUPERSEDED]
```

`hosting = Heroku` was stored with `ARCHIVE` (past tense), so it went straight to history. An empty space returns `(no memories yet)`.

## get\_memory

```python
get_memory(memory_id: int, user_id: str = "default", agent_id: str = "", run_id: str = "") -> str
```

**Capability** `read` · **Annotations** read-only

One memory by its `#id`, active or retired: value, status, scope, confidence, what it supersedes, and metadata.

```
#9  platform_lead = Alice
  status=ACTIVE  scope=global  confidence=0.95  metadata={'team': 'eng'}
```

An unknown id returns `No memory with id #9999.`

## update\_memory

```python
update_memory(memory_id: int, new_value: str, user_id: str = "default",
              agent_id: str = "", run_id: str = "") -> str
```

**Capability** `write` · **Annotations** write, not destructive, open-world

Corrects a memory by id without the LLM. The old value is retired to history and the new value becomes active under a new id, so the change shows up in `history`.

```
Updated #9: 'Alice' → 'Carol' (now #14; old kept in history).
```

An unknown id returns `No memory with id #9999.`

## Next steps

<table data-view="cards"><thead><tr><th></th><th></th><th data-hidden data-card-target data-type="content-ref"></th></tr></thead><tbody><tr><td><h4><i class="fa-compass" style="color:$primary;">:compass:</i> Your first memory</h4></td><td>These tools in a hands-on walkthrough: store, correct, retract, recall.</td><td><a href="../getting-started/your-first-memory.md">your-first-memory.md</a></td></tr><tr><td><h4><i class="fa-code-merge" style="color:$primary;">:code-merge:</i> Reconciliation</h4></td><td>The nine actions and four statuses behind every <code>remember</code>.</td><td><a href="../core-concepts/core-concepts.md">core-concepts.md</a></td></tr><tr><td><h4><i class="fa-shield-halved" style="color:$primary;">:shield-halved:</i> Safety and gating</h4></td><td>Gate irreversible actions before your agent acts on a memory.</td><td><a href="safety-and-gating.md">safety-and-gating.md</a></td></tr></tbody></table>
