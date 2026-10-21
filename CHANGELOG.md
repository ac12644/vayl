# Changelog

All notable changes to Vayl are documented here. This project adheres to
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Changed
- **Moved to FastMCP 4 and MCP Python SDK v2** (`fastmcp>=4.1,<5`, `mcp>=2.3,<3`). FastMCP 4 requires
  MCP SDK v2, so the two move together. The server and its tools are unchanged on the wire. Vayl's
  pins used to exclude these majors, so it couldn't be installed next to tools that need them.
- The Python client's HTTP mode, `Vayl(url=…, api_key=…)`, uses SDK v2's `streamable_http_client`
  with an `httpx2` client carrying the API key. SDK v2 removed `streamablehttp_client`, so the old
  code failed with an `ImportError` on SDK v2.
- Pinned GitHub Actions updated to their latest releases, including CodeQL `v3` → `v4`.

### Fixed
- A failed client connection now raises one readable `VaylError` that names the target and the root
  cause, instead of a nested `ExceptionGroup` ("unhandled errors in a TaskGroup").
- The client reads `CallToolResult.is_error` directly. It used `getattr(result, "isError", False)`,
  which would have reported every denied or failed call as a success once the old field name was
  removed.

### Added
- A test that drives `Vayl(url=…)` against a real HTTP server, including a refused key. Nothing
  covered that path, which is how the SDK v2 break went unnoticed.

### Upgrading
- No action for servers or the stdio client.
- If your project pins `mcp<2` or `fastmcp<4` for something else, Vayl 0.8 can't share that
  environment; stay on Vayl 0.7 until you can move.
- Python 3.10 is still supported, but MCP SDK v2's HTTP library (`httpx2`) dropped 3.10 after 2.14,
  so 3.10 installs get that older version. Python 3.10 reaches end of life in October 2026, and a
  later release will require 3.11.

## [0.7.0] — 2026-09-27

Tenant isolation completed across policies, the graph, decisions, receipts, the audit log and
principal management, plus correctness fixes from a docs-against-code audit. **Read Upgrading below:
migration v2 is not additive, so back up first, and graph users rebuild the graph once.**

### Fixed
- **A same-scope COEXIST can't leave two active values.** A model that labelled a change COEXIST but
  kept the same scope left both values active (`state = Redux` and `state = Zustand`). It's now checked
  like any other change; a real COEXIST, on a different scope, still keeps both.
- `forget` on a confirm-required slot reports the queued removal ("Proposed for removal, awaiting
  approval…" or "Already awaiting approval…") instead of "Nothing matching to retract".
- `remember` no longer lists SKIP results (hypotheticals, sarcasm) under "Stored:".
- `check_before_act` and `safe_recall` list each reason once.
- `pending_changes` shows a proposed removal as `REMOVE subject: 'value'` instead of `'value' -> 'value'`.
- `delete` that erases nothing issues no signed receipt; the request is still in the audit log.
- The MCP `serverInfo.version` is Vayl's version, not FastMCP's.
- An invalid `VAYL_LOG_LEVEL` or a malformed `VAYL_OIDC_ROLE_MAP` fails at startup naming the variable.
  A malformed role map used to be ignored, giving every SSO user the default role.


### Security
- **Reconcile policies are isolated per tenant.** The policy table was keyed by
  `(user_id, agent_id, run_id)` without the tenant, so one tenant's `set_reconcile_policy` replaced
  another tenant's policy for the same `user_id`. Schema migration **v2** rebuilds the table with the
  tenant in the key and keeps existing policies.
- **Graph edges are isolated per tenant.** The Neo4j namespace had no tenant, so two tenants with the
  same `user_id` shared `recall_related` results, and erasing one tenant's user also removed the other
  tenant's edges. The namespace now starts with the tenant. `reproject_graph()` used to wipe the whole
  graph and replay only the current tenant; it now clears only that tenant's edges.
- **Decisions, receipts and audit entries are isolated per tenant.** These tables had no tenant, so a
  caller in one tenant could read another tenant's decision with `explain_decision`, verify its
  receipts with `verify_receipt`, and list its entries with `audit_log`. Schema migration **v3** adds
  a `tenant_id` column to each; every write is stamped with the caller's tenant and every lookup is
  filtered by it. The audit hash chain doesn't cover the new column, so existing chains still verify.
- **Retention purges stay inside the tenant.** `purge_expired(include_decisions / include_receipts)`
  deleted every tenant's rows; it now deletes only the caller's tenant's. The audit log is one chain
  for the whole deployment, so `include_audit` still purges it for every tenant, and now only the
  deployment operator may use it.
- **A tenant admin can no longer manage another tenant's principals.** An admin of one tenant could
  create a principal (for example, an admin key) in any other tenant, list every tenant's principals,
  and revoke any of them. A tenant admin now manages only its own tenant. The **deployment operator**
  (an admin of the `default` tenant, which includes the local stdio admin) still manages every tenant.
  `create_principal` defaults `tenant` to the caller's own.
- **Deployment-wide numbers are for the operator only.** `stats` counts every tenant's activity, so
  callers outside the `default` tenant are now denied it. `license_status` shows seats used only to
  the operator, since seats are counted across tenants.

### Added
- `vayl-migrate reproject-graph` rebuilds the graph for every tenant from the store.

### Upgrading
- **Back up the database first.** Migration v2 isn't additive: 0.6 can't write policies to the rebuilt
  table, so 0.6 refuses to start on a v2 database (the schema version check). To roll back, restore
  the backup.
- **If the graph is enabled, run `vayl-migrate reproject-graph` once after upgrading.** Edges written
  before this release use the old namespace. Until they're rebuilt, `recall_related` doesn't find them
  and erasure doesn't remove them.
- Migration v3 is additive. It adds a `tenant_id` column to `decisions`, `receipts` and `audit`, and
  existing rows are stamped `default`, which is where a single-tenant deployment's rows belong. In a
  multi-tenant deployment, rows written before the upgrade stay in `default`, so they're visible only
  to the `default` tenant.
- Single-tenant and stdio deployments behave as before.

## [0.6.0] — 2026-09-26

Closes the approval-gate gaps, fixes `vayl-server`'s HTTP transport, and corrects configuration
defaults found in a full docs-against-code audit. **Read Upgrading below**: approvals now need the
`approve` capability, and embeddings follow `OPENAI_API_KEY`.

### Security
- **Only a person can approve a gated change.** `confirm_change` and `reject_change` used to need only
  `write`, which the `agent` role has, so an agent could approve its own proposal to a confirm-required
  slot. They now need a new `approve` capability, which only the `admin` and `member` roles have. The
  approver recorded in the fact and the audit log is the authenticated caller; `decided_by` is kept
  only as a note beside it, so a caller can no longer name someone else as the approver.
- **Trusted sources are bound to a key.** A write whose `source` is listed in `VAYL_TRUSTED_SOURCES`
  skips the confirmation gate, and `source` is chosen by the caller, so any key could skip the gate by
  labelling its write `fhir`. `remember` now accepts a trusted source only from a key with that name
  (for example, an integration key created as `create_principal("fhir", role="agent")`) or a key with
  `approve`. Other callers are denied, and the denial is audited.

### Changed
- **Embeddings follow your OpenAI key.** With `OPENAI_API_KEY` set and neither `EMBED_BASE_URL` nor
  `OPENAI_BASE_URL`, chat went to OpenAI but embeddings still went to a local Ollama at
  `localhost:11434` (`nomic-embed-text`), and the OpenAI key was sent there. Without Ollama running,
  every embed call stalled on retries and recall fell back to keyword ranking. That setup, which is
  the README's MCP config and the docker-compose default, now embeds with OpenAI
  `text-embedding-3-small`. Setups that set `EMBED_BASE_URL` or `OPENAI_BASE_URL` are unchanged.
- **Every on/off setting is parsed strictly.** `VAYL_AUTH_REQUIRED`, `VAYL_GRAPH`, `VAYL_ENCRYPT`,
  `VAYL_SIGN`, `VAYL_DEDUP_PREFILTER`, `VAYL_SLOT_RESOLVE` and `OPENAI_JSON` accept
  `on/off/true/false/yes/no/1/0` in any case, and anything else fails at startup naming the variable.
  Before, `VAYL_AUTH_REQUIRED=on` and `VAYL_GRAPH=on` silently meant off, and a typo in
  `VAYL_ENCRYPT` or `VAYL_SIGN` silently meant on.

### Added
- `vayl-mcp` and `vayl-server` answer `--help` and `--version`. `vayl-mcp --help` used to start the
  stdio server and create `vayl.db` in the working directory.

### Fixed
- The Docker admin bootstrap command in README.md, DEPLOY.md and `docker-compose.yml` always returned
  "authentication required", because the image sets `VAYL_AUTH_REQUIRED=1`. It now passes
  `-e VAYL_AUTH_REQUIRED=0` for that one-off container.
- **`vayl-server` returned empty responses to every MCP call.** Requests got `200` with an empty body,
  and the server logged `ASGI callable returned without completing response`. `LimitsMiddleware` reads
  the request body to enforce the size cap, then replays it. After the replay it answered every
  further `receive()` with `http.disconnect`, so each streamed (SSE) response took the client for
  gone and cancelled itself. It now passes later calls to the real `receive()`. A new test runs a real
  MCP `tools/call` through the full HTTP stack. stdio (`vayl-mcp`) was not affected.
### Upgrading
- If you set only `OPENAI_API_KEY` and ran a local Ollama for embeddings, set
  `EMBED_BASE_URL=http://localhost:11434/v1` to keep using it. Otherwise, new facts get OpenAI vectors,
  and semantic ranking falls back to keyword ranking in any space that mixes the two sizes.
- An on/off setting with a value other than the accepted spellings now stops startup with a message
  naming it.
- Agent keys that called `confirm_change` or `reject_change` are now denied. Approve changes with a
  `member` or `admin` key.
- An integration that writes as a trusted source must use a key whose name matches the source.
- Local stdio use is unaffected: it runs as the local admin.

## [0.5.1] — 2026-09-26

A hardening patch: atomic schema migrations on SQLite and an opt-out for the in-memory decrypt caches.
No action needed to upgrade.

### Added
- `VAYL_DECRYPT_CACHE=off` turns off the decrypted-field and decoded-embedding caches, so plaintext
  stays in process memory only while a call runs. It costs speed: at 1,000 facts, `remember` goes from
  23 to 122 ms and `recall` from 59 to 273 ms. An unknown value fails at startup.

### Fixed
- **Schema migrations are atomic on SQLite.** Python's `sqlite3` module committed each schema change
  on its own, so a migration interrupted halfway left a partial schema. Pending migrations and their
  ledger rows now commit as one transaction under `BEGIN IMMEDIATE`, which also locks out other
  processes sharing the file until the migration finishes. Postgres already worked this way.
- `VAYL_VECTOR_CACHE=0` disables the vector cache instead of failing on the first recall.

## [0.5.0] — 2026-09-26

Faster tool calls on large memories, versioned schema migrations, and request-level logging for the
HTTP server. No breaking API changes; see **Upgrading from 0.4** below.

### Performance
- **Faster tool calls on large memories.** Every tool call reloads the memory space. Before this change
  it decrypted every row again and re-parsed every embedding. Short encrypted fields now go through a
  bounded ciphertext→plaintext cache. Decoded embeddings are cached by the SHA-256 of their stored text
  and carry a precomputed norm, and recall ranks with `math.sumprod` on Python 3.12+. Every hard delete
  (`delete`, `delete_all`, retention expiry) clears both caches, so erased data doesn't stay in
  process memory. At 1,000 facts, with encryption on and model calls stubbed out, median `remember`
  drops from 122 ms to 23 ms and `recall` from 444 ms to 59 ms.

### Added
- **Request IDs on the HTTP server.** Every request gets an ID, echoed in the `X-Request-ID` response
  header. A plain incoming `X-Request-ID` (up to 64 characters of `A-Z a-z 0-9 . _ : -`) is reused so
  proxy and Vayl logs line up. The ID appears on every log line written while serving that request, so
  an error `ref` shown to a client can be traced to its request. Each request also writes one access
  line at `INFO` with method, path, status and duration, and no client IP, query string or body. Health,
  readiness and metrics probes log at `DEBUG`.
- **Versioned schema migrations.** The schema is now one ordered list of migrations
  (`vayl/storage/migrations.py`), recorded in a new `schema_migrations` table and applied on startup
  under a lock (a cross-process advisory lock on Postgres). A Vayl older than the database's schema
  refuses to start instead of writing rows it doesn't understand. `vayl-migrate status` and
  `vayl-migrate up` show and apply migrations. Existing databases, including ones from releases before
  0.4, are brought to the baseline (v1) automatically, with no data changes.
- `VAYL_LOG_FORMAT=json` writes one JSON object per log line (default `text`). An unknown value fails
  at startup.
- `VAYL_VECTOR_CACHE` (default 8192): how many decoded embeddings to keep, about 6 KB each at 1536
  dimensions. A space with more embedded facts than this re-decrypts the overflow on every recall.

### Changed
- Text-format log lines now include the request ID in brackets after the logger name
  (`… INFO vayl.api.server [3f9a…]: …`), shown as `[-]` on stdio. Update any log parser that matches
  the old format, or switch to `VAYL_LOG_FORMAT=json`.

### Security
- CI now fails on a dependency with a known vulnerability (`pip-audit`) instead of warning, and GitHub
  secret scanning with push protection is on for the repository.

### Upgrading from 0.4
- No action needed. On first start, 0.5 creates `schema_migrations` and records the existing schema as
  v1. It changes no data.
- Rolling back to 0.4.0 is safe: 0.4.0 ignores the new table and reads the database as before.
- If you run several server processes on one Postgres, you can run `vayl-migrate up` once before
  rolling out. If you don't, the first process to start applies the migrations under an advisory lock.

## [0.4.0] — 2026-09-26

Framework adapters, three new presets, per-tenant isolation, and a hardening pass. Several changes are
**breaking** for misconfigured or partially-configured setups; each now fails at startup with a message
naming the setting. See **Upgrading from 0.3** below.

### Added
- **Agent-framework adapters.** `vayl.integrations.langgraph`, `.openai_agents` and `.crewai` expose the
  same memory tools (`remember`, `recall`, `history`, `forget`, `list_memories`) with the caller's scope
  bound server-side. Install with the `langgraph`, `openai-agents` or `crewai` extra. (Vercel AI SDK and
  Mastra adapters ship in the TypeScript client, `@vayl.dev/client`.)
- **Presets `coding`, `assistant` and `sales`** (`VAYL_SLOT_SCHEMA=preset:coding`, …), alongside
  `clinical`, `finance` and `support`.
- **Tenant partitioning.** A principal's `tenant` now filters every store query, so two organizations on
  one deployment never see each other's memory, even under the same `user_id`.
- `examples/coding_assistant/`: a runnable example of an agent that remembers project decisions.
- `VAYL_LOG_LEVEL` (default `WARNING`); logs go to stderr.

### Changed
- **Invalid configuration fails at startup:** unknown `LLM_PROVIDER` (it silently routed extraction to
  Anthropic), unknown `VAYL_KMS` (it silently fell back to a key file), `VAYL_KMS=vault` without
  `VAULT_TOKEN`, and malformed numeric settings.
- `health()` makes one attempt per dependency instead of backing off for minutes when one is down.

### Security
- **Erasure fails closed.** If the Neo4j graph purge fails, `delete` / `delete_all` erase nothing and
  issue **no** signed receipt, instead of a receipt for a partial erasure.
- **`NEO4J_PASSWORD` is required** when `VAYL_GRAPH=1` (it fell back to a hard-coded default). The
  shipped `docker-compose.yml` takes it from `.env` and binds Neo4j to localhost.
- **Exception text no longer leaks.** `stats()` shows error text to admins only (any role could read
  errors carrying another tenant's memory), and `ERROR` logs carry a ref, type and location, not the
  text; full detail is at `DEBUG`.
- `cryptography>=50` (PYSEC-2026-3552); `pyjwt>=2.13.0` for the `sso` extra (CVE-2022-29217).

### Fixed
- A declared single-valued slot now supersedes the old value even when a weak extractor mislabels the
  change as an event; previously both values stayed active.
- Mismatched embedding dimensions (e.g. after changing `EMBED_MODEL`) no longer produce a meaningless
  ranking; recall falls back to lexical ranking and logs it.
- `VAYL_EXTRACT_RETRIES` below 0 no longer skips extraction and crashes.

### Performance
- `load()` stays fast as a memory space's history grows: 3.2 ms instead of 93 ms with 200,000 retired
  facts in one space (SQLite, 500 active facts).

### Removed
- **`vayl.clinical`** (FHIR ingestion and discharge medication reconciliation). It was not wired into any
  MCP tool or entry point. The **`preset:clinical`** slot schema is unchanged.

### Upgrading from 0.3
1. **Back up your database.** The first start applies schema changes automatically (a `tenant_id`
   column; hot-path indexes replace `idx_space`).
2. If you set `VAYL_GRAPH=1`, set `NEO4J_PASSWORD`.
3. `LLM_PROVIDER` must be `openai`, `anthropic` or `groq`. For Ollama or vLLM, use `openai` with
   `OPENAI_BASE_URL`.
4. `VAYL_KMS` must be `file` or `vault`; `vault` requires `VAULT_TOKEN`.
5. Code importing `vayl.clinical` must vendor it. Code importing private helpers from
   `vayl.memory.llm_memory` (e.g. `_embed`, `_http_json`) should import them from
   `vayl.memory.llm_client` or `vayl.memory.retrieval`.

## [0.3.0] — 2026-07-29

Framework migration: Vayl's MCP server now runs on the maintained standalone **FastMCP** framework
instead of the bundled `mcp.server.fastmcp` (which MCP SDK 2.0 removed).

### Changed
- **Migrated to standalone FastMCP** (`fastmcp>=3,<4`). Unblocks the previous `mcp<2` cap and enables
  **one-command client install** — `fastmcp install {claude-desktop, claude-code, cursor, gemini-cli,
  mcp-json}` — plus FastMCP's auth/middleware/provider system.
- **Scope-aware SDKs.** FastMCP validates tool arguments strictly and rejects unknown ones. The
  Python and TypeScript clients now learn each tool's parameters on connect and send
  `user_id`/`agent_id`/`run_id` only to tools that accept them — so a client-wide default scope no
  longer breaks scope-less tools (`health`, `stats`, `verify_audit`, `export_public_key`, …).

## [0.2.0] — 2026-07-28

Onboarding and write-path release: try Vayl in 30 seconds, call it from Python or TypeScript, and
use a domain schema without authoring JSON.

### Added
- **`vayl-demo`** — a zero-setup, ~30-second demonstration of reconciling memory. Runs the real
  engine on a scripted conversation (no keys or network needed); `--live` uses a reachable LLM.
- **Python client `vayl.Vayl`** — a synchronous client wrapping the MCP tools so you call methods
  (`m.remember(...)`, `m.recall(...)`, `m.call(tool, ...)`) instead of `tools/call` JSON. Works over
  stdio (spawns `vayl-mcp`) or authenticated streamable-HTTP.
- **TypeScript client** (`clients/typescript`, npm `vayl`) — the same surface for TS/JS agents:
  `await Vayl.connect({...})`, `m.remember/recall/call`, stdio or HTTP.
- **Built-in slot-schema presets** — `VAYL_SLOT_SCHEMA=preset:clinical` (also `finance`, `support`)
  loads a bundled declared-slot schema, no JSON authoring required.

### Changed
- **Pre-LLM dedup** — a verbatim restatement of facts that are all still active now skips the
  extractor entirely (0 LLM calls) instead of spending one and reconciling to a no-op. Provably no
  staleness regression; disable with `VAYL_DEDUP_PREFILTER=off`.

### Infrastructure
- OpenSSF Scorecard, CodeQL (SAST), and Dependabot workflows; least-privilege and SHA-pinned CI;
  a hardened security policy with private vulnerability reporting.

## [0.1.0] — 2026-07-27

Initial public release: the reconciling-memory engine and MCP server. A new value supersedes the
old, removals retract, ambiguous input is flagged, and history stays queryable — over stdio
(`vayl-mcp`) or an authenticated team server (`vayl-server`). SQLite by default, optional Postgres;
encryption at rest, an Ed25519-signed tamper-evident audit chain, RBAC, and GDPR tools.

[0.7.0]: https://github.com/ac12644/vayl/releases/tag/v0.7.0
[0.6.0]: https://github.com/ac12644/vayl/releases/tag/v0.6.0
[0.5.1]: https://github.com/ac12644/vayl/releases/tag/v0.5.1
[0.5.0]: https://github.com/ac12644/vayl/releases/tag/v0.5.0
[0.4.0]: https://github.com/ac12644/vayl/releases/tag/v0.4.0
[0.3.0]: https://github.com/ac12644/vayl/releases/tag/v0.3.0
[0.2.0]: https://github.com/ac12644/vayl/releases/tag/v0.2.0
[0.1.0]: https://github.com/ac12644/vayl/releases/tag/v0.1.0
