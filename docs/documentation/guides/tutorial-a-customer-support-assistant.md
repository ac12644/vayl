---
description: >-
  Build a support agent that remembers each customer's current plan,
  preferences and issues, isolated per customer, and answers with what's true
  now.
icon: headset
---

# Tutorial: a customer-support assistant

By the end of this tutorial you'll have a support assistant on a shared `vayl-server` that keeps each customer's plan, preferences and open issues current, isolated from every other customer, and that settles disagreements between the customer and your billing system by rule instead of by accident. There's no life-safety gating here. The features doing the work are per-customer isolation, reconciliation across many conversations, and source-aware conflict resolution.

## How to read the outputs

`remember`, `forget`, `list_memories`, `set_reconcile_policy` and `create_principal` outputs below are the exact strings Vayl returns. The subject (`plan`, `open_issue`) is named by the model, so yours may differ unless you declare slots, for example with `VAYL_SLOT_SCHEMA=preset:support`. Recall answers are written by the model and shown as representative examples.

## Step 1: connect to the team server

Support is a team setting, so the assistant talks to `vayl-server` over HTTP with its own API key (see [Deploying vayl-server](deploying-vayl-server.md)). We use the Python client from [Calling Vayl from code](../getting-started/calling-vayl-from-code.md) and pass the customer id as `user_id` on each call:

```python
from vayl import Vayl

URL = "https://memory.acme.com/mcp"
BOT_KEY = "vayl_sk_…"     # the support bot's key: role "agent"

bot = Vayl(url=URL, api_key=BOT_KEY)
```

Two steps below need an admin key: setting a reconciliation policy (Step 4) and creating keys (Step 6). The bot's `agent` role can read and write memory but not change configuration, and gets `Access denied: 'set_reconcile_policy' requires the 'admin' capability; your role(s) ['agent'] do not grant it.` if it tries. Keep the admin key out of the bot:

```python
admin = Vayl(url=URL, api_key="vayl_sk_…")   # an admin key, used only by your setup code
```

Call `bot.close()` and `admin.close()` when done, or use each as a context manager.

## Step 2: a memory per customer

Each customer is an isolated [memory space](../core-concepts/memory-spaces.md). Facts for `cust_5521` never mix with `cust_7788`, and the same subject (`plan`) is a different slot in each:

```python
print(bot.remember("On the Pro plan; prefers email over phone", user_id="cust_5521"))
print(bot.remember("On the Free plan", user_id="cust_7788"))
```

```
Stored: [ADD] plan = Pro; [ADD] contact_preference = email
Stored: [ADD] plan = Free
```

```python
print(bot.recall("what plan are they on?", user_id="cust_5521"))   # e.g. "Pro."   (model-generated)
print(bot.recall("what plan are they on?", user_id="cust_7788"))   # e.g. "Free."  (model-generated)
```

## Step 3: keep it current

Weeks later the customer upgrades. Vayl doesn't append a second plan; it supersedes the old one:

```python
print(bot.remember("Upgraded to the Enterprise plan", user_id="cust_5521"))
print(bot.list_memories(user_id="cust_5521"))
```

```
Stored: [SUPERSEDE] plan = Enterprise
• contact_preference = email  (#2)
• plan = Enterprise  (#3)

— history (superseded / retracted / archived) —
  ◦ plan = Pro  [SUPERSEDED]
```

Recall now answers from the one current plan. An append-only memory asked the same question after a few changes may still surface "Pro" from an old ticket; here "Pro" is in history, and a normal recall never uses it.

## Step 4: when sources disagree

The customer says one thing and your billing system knows another. You want the authoritative source to win and the disagreement kept visible for a person, not silently resolved either way. Set a reconciliation policy for the space. This needs the admin key:

```python
print(admin.set_reconcile_policy(mode="AUTHORITY",
                                 authority={"billing_system": 10, "customer": 1},
                                 user_id="cust_9120"))
```

```
Reconciliation policy for this space set to AUTHORITY.  authority ranks: {'billing_system': 10, 'customer': 1}
```

Now tag each write with its `source`. Billing syncs the plan, then the customer contradicts it in chat:

```python
print(bot.remember("Plan: Enterprise", user_id="cust_9120", source="billing_system"))
print(bot.remember("I think I'm still on Pro", user_id="cust_9120", source="customer"))
print(bot.list_memories(user_id="cust_9120"))
```

```
Stored: [ADD] plan = Enterprise
Not applied, flagged for review: [FLAG] plan = Pro
• plan = Enterprise  (#1)
⚠ plan = Pro  (#2, flagged — needs confirmation)
```

`billing_system` outranks `customer`, so the current value stays what billing says. The customer's claim isn't thrown away: it's flagged, so an agent can follow up on the confusion. `recall(..., explain=True)` shows which source the answer rests on:

```python
print(bot.recall("what plan are they on?", user_id="cust_9120", explain=True))
```

```
Enterprise.

Based on these facts:
  • #1 plan = Enterprise  [conf 0.95, from billing_system]
```

The first line is the model's answer; the `Based on these facts:` block is Vayl's provenance, and its confidence comes from the model's extraction.

{% hint style="info" %}
Set the policy before the first write, and tag every write with a `source`. Ranks apply to the source of the fact already stored: a fact written with no source has rank 0, so any ranked source overrides it. A source correcting its own earlier fact always supersedes.
{% endhint %}

The three policies, chosen per space:

| Mode | Cross-source conflict resolves by |
| --- | --- |
| `RECENCY` (default) | the newer assertion wins |
| `AUTHORITY` | the higher-ranked source wins; a lower-ranked contradiction is flagged |
| `REVIEW` | every cross-source conflict is flagged for a person |

`get_reconcile_policy(user_id=…)` shows the current setting.

## Step 5: resolve an issue

When an issue is resolved, retract it. It stops being current but stays in history for audit:

```python
print(bot.remember("Open issue: billing double-charge", user_id="cust_5521"))
# … later …
print(bot.forget("The billing issue is resolved", user_id="cust_5521"))
```

```
Stored: [ADD] open_issue = billing double-charge
Retracted (retained in history for audit): open_issue = billing double-charge
```

To erase a customer's data rather than retire it (a GDPR request), use `delete` or `delete_all`; see [Compliance & GDPR](../mcp-tools/compliance-gdpr.md).

## Step 6: isolate customer-facing keys

The internal bot above serves every customer. If you expose a per-customer widget or portal, give that integration a key scoped to just that customer, so a bug or a leaked token can't read another customer's memory. Creating keys needs the admin key:

```python
print(admin.create_principal(name="portal-cust_5521", role="agent", scopes="cust_5521"))
```

```
Created principal prin_9a45cd8ac82e 'portal-cust_5521' (role: agent, kind: agent, tenant: default).
  API key (shown once — save it now):
  vayl_sk_…
```

A scoped key that passes any other `user_id` gets `Access denied: 'recall' targets a memory space outside your assigned scope.`, and the denial is audited. For separate organizations on one deployment, also give each its own `tenant`. See [Authentication & access](../core-concepts/authentication-and-access.md).

## What you leaned on

| Requirement | Vayl feature |
| --- | --- |
| each customer's memory isolated | [memory spaces](../core-concepts/memory-spaces.md) (`user_id`) |
| always the current plan and preference | reconciliation (supersede on write) |
| customer vs. system disagreements | `set_reconcile_policy` (AUTHORITY / REVIEW) + `source` |
| why the agent believes X | `recall(explain=True)` provenance |
| safe multi-customer exposure | scoped API keys |

## Next steps

<table data-view="cards"><thead><tr><th></th><th></th><th data-hidden data-card-target data-type="content-ref"></th></tr></thead><tbody><tr><td><h4><i class="fa-hospital" style="color:$primary;">:hospital:</i> Tutorial: a hospital medication assistant</h4></td><td>The higher-stakes example: critical facts and approval gates.</td><td><a href="tutorial-a-hospital-medication-assistant.md">tutorial-a-hospital-medication-assistant.md</a></td></tr><tr><td><h4><i class="fa-sitemap" style="color:$primary;">:sitemap:</i> Memory spaces</h4></td><td>The per-customer isolation model in depth.</td><td><a href="../core-concepts/memory-spaces.md">memory-spaces.md</a></td></tr><tr><td><h4><i class="fa-lock" style="color:$primary;">:lock:</i> Authentication &#x26; access</h4></td><td>Roles, scopes and tenants for a shared deployment.</td><td><a href="../core-concepts/authentication-and-access.md">authentication-and-access.md</a></td></tr></tbody></table>
