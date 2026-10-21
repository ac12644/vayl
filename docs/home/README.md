---
description: Reconciling memory for AI agents — current, auditable, and local.
icon: house
layout:
  width: wide
  title:
    visible: true
  description:
    visible: true
  tableOfContents:
    visible: false
  outline:
    visible: false
  pagination:
    visible: false
  metadata:
    visible: false
  tags:
    visible: true
  actions:
    visible: true
  anchors:
    visible: false
---

# Vayl

<button type="button" class="button primary" data-action="ask" data-icon="gitbook-assistant">Ask a question…</button>

<button type="button" class="button secondary" data-action="ask" data-query="How do I connect Vayl to my MCP client" data-icon="bolt">Get started</button><button type="button" class="button secondary" data-action="ask" data-query="What is reconciling memory" data-icon="book">What is reconciling memory?</button><button type="button" class="button secondary" data-action="ask" data-query="How do I scope memory per user" data-icon="sitemap">Scope per user</button>

<table data-view="cards"><thead><tr><th></th><th></th><th></th><th data-hidden data-card-target data-type="content-ref"></th></tr></thead><tbody><tr><td><h4><i class="fa-rocket-launch" style="color:$primary;">:rocket-launch:</i></h4></td><td><strong>Documentation</strong></td><td>Install Vayl, learn the core concepts, and deploy a team server.</td><td><a href="https://vayl.gitbook.io/vayl-docs/documentation/getting-started/quickstart">https://vayl.gitbook.io/vayl-docs/documentation/getting-started/quickstart</a></td></tr><tr><td><h4><i class="fa-terminal" style="color:$primary;">:terminal:</i></h4></td><td><strong>MCP tools</strong></td><td>Every tool your agent gets — memory, safety, accountability, and compliance.</td><td><a href="https://vayl.gitbook.io/vayl-docs/documentation/reference/mcp-tools">https://vayl.gitbook.io/vayl-docs/documentation/reference/mcp-tools</a></td></tr><tr><td><h4><i class="fa-github" style="color:$primary;">:github:</i></h4></td><td><strong>Open source</strong></td><td>Vayl is Apache-2.0 and built in the open. Read the code and contribute.</td><td><a href="https://github.com/ac12644/vayl">https://github.com/ac12644/vayl</a></td></tr></tbody></table>

{% columns %}
{% column width="50%" %}
## Memory that stays current

Most agent memory accumulates — it saves every fact and later hands your agent a stale one. Vayl **reconciles on write**: a new value supersedes the old, a removal actually retracts, ambiguous inputs are flagged instead of guessed, and the full history stays queryable and auditable.

It speaks the Model Context Protocol, so any MCP client plugs in. Locally it runs over stdio with a single SQLite file; teams run the authenticated `vayl-server`.

{% hint style="info" icon="sparkles" %}
**New to reconciling memory?**

Ask the Assistant how supersede, retract, and the same-slot invariant work.

<button type="button" class="button primary" data-action="ask" data-query="Explain reconciling memory and the same-slot invariant" data-icon="gitbook-assistant">Explain reconciling memory</button>
{% endhint %}

<a href="https://vayl.gitbook.io/vayl-docs/documentation/getting-started/quickstart" class="button primary" data-icon="rocket-launch">Get started</a> <a href="https://github.com/ac12644/vayl" class="button secondary" data-icon="github">GitHub</a>
{% endcolumn %}

{% column width="50%" %}
{% stepper %}
{% step %}
#### Install

```bash
pip install vayl-mcp
```
{% endstep %}

{% step %}
#### Add Vayl to your MCP client

{% code overflow="wrap" %}
```json
{
  "mcpServers": {
    "vayl": {
      "command": "vayl-mcp",
      "env": {
        "OPENAI_API_KEY": "sk-…",
        "VAYL_DB": "/absolute/path/vayl.db"
      }
    }
  }
}
```
{% endcode %}
{% endstep %}

{% step %}
#### Watch it reconcile

```
remember("Patient is on warfarin 5 mg daily.")       → active_medication = warfarin
remember("Warfarin stopped. Started apixaban.")      → held for approval
confirm_change(4)                                    → apixaban active, warfarin in history
recall("What anticoagulant is the patient on?")      → apixaban (worded by your model)
```
{% endstep %}
{% endstepper %}
{% endcolumn %}
{% endcolumns %}

<h2 align="center">Open source, built in the open</h2>

<p align="center">Vayl is Apache-2.0. Read the code, file an issue, or open a pull request.</p>

<table data-card-size="large" data-view="cards"><thead><tr><th></th><th></th><th></th><th data-hidden data-card-target data-type="content-ref"></th></tr></thead><tbody><tr><td><h4><i class="fa-github">:github:</i></h4></td><td><strong>GitHub</strong></td><td>The engine, the MCP server, and the benchmarks — all open source.</td><td><a href="https://github.com/ac12644/vayl">https://github.com/ac12644/vayl</a></td></tr><tr><td><h4><i class="fa-shield-halved">:shield-halved:</i></h4></td><td><strong>Security &#x26; compliance</strong></td><td>Encrypted at rest, tamper-evident audit chain, and GDPR building blocks.</td><td><a href="https://github.com/ac12644/vayl/blob/main/SECURITY.md">https://github.com/ac12644/vayl/blob/main/SECURITY.md</a></td></tr></tbody></table>
