---
description: >-
  Find the exact error message you're seeing, what causes it, how to fix it,
  and how to confirm the fix.
icon: wrench
---

# Troubleshooting

Find your error message on this page. Each entry quotes the text Vayl actually prints, then gives the cause, the fix and a way to confirm it worked.

When the server starts but something behaves wrongly, call the `health` tool first. It checks the database, the embedder, the model and the graph, making one attempt at each, and reports which piece fails:

```
config: LLM_PROVIDER=(unset), model=(default)
license: community
encryption: on · signing: on
db: ok
embedder: FAIL (NewConnectionError)
llm: FAIL (NewConnectionError)
graph: disabled
```

## Startup failures

These stop `vayl-mcp` or `vayl-server` before it serves anything. The message is the last line of the traceback on stderr. In an MCP client, look in the client's MCP server log.

### `LLM_PROVIDER must be one of anthropic, openai, groq`

```
ValueError: LLM_PROVIDER must be one of anthropic, openai, groq, got 'ollama'. For Ollama or any OpenAI-compatible endpoint, use 'openai' with OPENAI_BASE_URL.
```

* **Cause:** `LLM_PROVIDER` has a value other than the three providers. Ollama, vLLM and similar servers are reached through the OpenAI-compatible provider.
* **Solution:** set `LLM_PROVIDER=openai` and `OPENAI_BASE_URL=http://localhost:11434/v1`, or unset `LLM_PROVIDER` to let Vayl infer it. See [Local models with Ollama](../guides/local-models-with-ollama.md).
* **Verification:** the server starts, and `health` prints `config: LLM_PROVIDER=openai, …`.

### `<VARIABLE> must be an integer / a number / on or off`

```
ValueError: VAYL_PORT must be an integer, got 'eighty'
ValueError: VAYL_SLOT_RESOLVE_SIM must be a number, got 'high'
ValueError: VAYL_ENCRYPT must be on or off, got 'maybe'
```

* **Cause:** since 0.6.0, typed settings parse strictly. On/off switches accept only `on` `off` `true` `false` `yes` `no` `1` `0` (any case).
* **Solution:** fix the variable named in the message. The accepted values are in [Configuration](configuration.md).
* **Verification:** the server starts.

### `VAYL_LOG_FORMAT must be 'text' or 'json'` / `VAYL_LOG_LEVEL must be …`

```
ValueError: VAYL_LOG_FORMAT must be 'text' or 'json', got 'xml'
ValueError: VAYL_LOG_LEVEL must be DEBUG, INFO, WARNING, ERROR or CRITICAL, got 'LOUD'
```

* **Cause:** an unsupported `VAYL_LOG_FORMAT` or `VAYL_LOG_LEVEL`.
* **Solution:** use `text` or `json` for the format, and `DEBUG`, `INFO`, `WARNING`, `ERROR` or `CRITICAL` (any case) for the level.
* **Verification:** the server starts and logs in the chosen format on stderr.

### `VAYL_OIDC_ROLE_MAP must be a JSON object`

```
ValueError: VAYL_OIDC_ROLE_MAP must be a JSON object like {"group": "role"}: Expecting property name enclosed in double quotes: line 1 column 2 (char 1)
ValueError: VAYL_OIDC_ROLE_MAP must be a JSON object like {"group": "role"}
```

* **Cause:** with OIDC configured, `VAYL_OIDC_ROLE_MAP` isn't valid JSON (first line, with the parser's detail), or is valid JSON but not an object, such as a list (second line). Before 0.7.0 a malformed map was silently ignored and every SSO user got `VAYL_OIDC_DEFAULT_ROLE`.
* **Solution:** set a JSON object with double-quoted keys, for example `VAYL_OIDC_ROLE_MAP='{"vayl-admins":"admin","eng":"member"}'`. Single-quote the whole value in the shell.
* **Verification:** `vayl-server` starts.

### `unknown slot preset` / schema file not found

```
ValueError: unknown slot preset 'dental'. Available presets: assistant, clinical, coding, finance, sales, support
FileNotFoundError: [Errno 2] No such file or directory: '/etc/vayl/slots.json'
```

* **Cause:** `VAYL_SLOT_SCHEMA` names a preset that doesn't exist or a file that isn't there. A malformed JSON file fails the same way. Vayl refuses to start rather than run without the schema you asked for.
* **Solution:** use one of the listed presets (`preset:clinical`), or an absolute path to a readable, valid JSON file.
* **Verification:** the server starts. Store a fact that fits a declared slot and check that `list_memories` shows it under the declared name.

### `VAYL_KMS must be 'file' or 'vault'`

```
ValueError: VAYL_KMS must be 'file' or 'vault', got 'aws'
```

* **Cause:** an unsupported key-custody provider. Vayl rejects it instead of quietly falling back to a key file.
* **Solution:** set `VAYL_KMS=file` (the default) or `VAYL_KMS=vault`.
* **Verification:** the server starts.

### `VAYL_KMS=vault requires VAULT_TOKEN`

```
ValueError: VAYL_KMS=vault requires VAULT_TOKEN
```

* **Cause:** Vault custody is selected but no token is set.
* **Solution:** set `VAULT_TOKEN` (and `VAULT_ADDR` if Vault isn't at `http://127.0.0.1:8200`).
* **Verification:** see the next entry.

### Vault unreachable at startup

```
urllib.error.URLError: <urlopen error [Errno 61] Connection refused>
```

* **Cause:** with `VAYL_KMS=vault`, Vayl unwraps its data key through Vault Transit at startup. If Vault can't be reached it fails closed rather than run without its key. An HTTP error from Vault (403 for a bad token or policy, 400 for a missing key) fails the same way.
* **Solution:** check `VAULT_ADDR`, network access and the token's policy. The Transit engine and key must exist (defaults: mount `transit`, key `vayl`): `vault secrets enable transit` and `vault write -f transit/keys/vayl`.
* **Verification:** `curl $VAULT_ADDR/v1/sys/health` answers from the Vayl host, the server starts, and `health` prints `encryption: on · signing: on`.

### `VAYL_GRAPH is set but NEO4J_PASSWORD is not`

```
RuntimeError: VAYL_GRAPH is set but NEO4J_PASSWORD is not. Set the Neo4j password, or unset VAYL_GRAPH to run without the graph.
```

* **Cause:** the graph is enabled with no password. Vayl won't fall back to a well-known default.
* **Solution:** set `NEO4J_PASSWORD` (and `NEO4J_URI` / `NEO4J_USER` if not the defaults), or unset `VAYL_GRAPH`.
* **Verification:** the server starts and `health` prints `graph: ok`.

### Encryption or signing unavailable

```
RuntimeError: VAYL_ENCRYPT is on but at-rest encryption is unavailable (…). Install the 'cryptography' package, or set VAYL_ENCRYPT=off to explicitly run without encryption.
RuntimeError: Ed25519 signing is unavailable (…). Install the 'cryptography' package, or set VAYL_SIGN=off to explicitly run without signed audit/receipts.
```

* **Cause:** encryption and signing are on by default and fail closed. The `cryptography` package is missing or broken, or the key material can't be loaded.
* **Solution:** `pip install cryptography` (it is a core dependency, so reinstalling `vayl-mcp` also works) and check that the key files beside `VAYL_DB` are readable by the Vayl user. Turn either feature off only as a deliberate decision.
* **Verification:** `health` prints `encryption: on · signing: on`.

### `database schema is at version N, but this Vayl only knows up to M`

```
database schema is at version 3, but this Vayl only knows up to 2. It was upgraded by a newer Vayl: run that version, or restore the backup taken before the upgrade.
```

* **Cause:** a newer Vayl migrated this database, and an older Vayl was started against it. It refuses to write to a schema it doesn't understand.
* **Solution:** run the newer release, or restore the backup taken before the upgrade.
* **Verification:** `vayl-migrate status` ends with `schema: vN (this Vayl: vN)` and lists nothing as `PENDING`.

## Model and embedder

### Embedder or model unreachable, or every call is slow

* **Cause:** `health` shows `embedder: FAIL (…)` or `llm: FAIL (…)`, typically `NewConnectionError` or `URLError`. The most common case is no local Ollama at `localhost:11434`, which is where Vayl sends both chat and embeddings when no model variables are set. Before 0.6.0 this also happened with only `OPENAI_API_KEY` set. Regular calls retry with backoff for up to about two minutes before giving up, so an unreachable endpoint looks like a hang. When only the embedder is down, writes still succeed and recall falls back to keyword ranking, with the warning `embedding unavailable (…); recall degrades to lexical ranking` in the log.
* **Solution:** start Ollama and pull the models (`ollama pull qwen2.5:3b`, `ollama pull nomic-embed-text`), or point Vayl at a reachable endpoint with `OPENAI_BASE_URL` / `OPENAI_API_KEY`, and `EMBED_BASE_URL` for embeddings. With `ANTHROPIC_API_KEY` or `GROQ_API_KEY`, embeddings still need an OpenAI-compatible embedder. See [Local models with Ollama](../guides/local-models-with-ollama.md).
* **Verification:** `health` prints `embedder: ok` and `llm: ok`.

### `Vayl config error: missing 'ANTHROPIC_API_KEY'`

```
Vayl config error: missing 'ANTHROPIC_API_KEY'. Set OPENAI_API_KEY (or ANTHROPIC_API_KEY / GROQ_API_KEY) and LLM_PROVIDER in the server's env, then restart.
```

* **Cause:** `LLM_PROVIDER` names a provider whose key isn't set (here `anthropic`; the same happens for `groq`).
* **Solution:** set the key for that provider, or change `LLM_PROVIDER`.
* **Verification:** `health` prints `llm: ok`.

### Recall got worse after changing `EMBED_MODEL`

* **Cause:** the new model produces vectors of a different size, and existing facts keep their old embeddings. Vayl detects the mismatch and logs `semantic ranking unavailable (ValueError); recall uses lexical ranking`, so those recalls rank by keyword only. Re-embedding is not automatic.
* **Solution:** keep the original embedding model for an existing database, or start a new database with the new model.
* **Verification:** the warning stops appearing in the log at `WARNING` level.

## The server over HTTP

### `401` from `/mcp`

```json
{"error":"unauthorized: send Authorization: Bearer vayl_sk_..."}
```

* **Cause:** the request has no `Authorization: Bearer …` header, or the key is unknown or revoked, or the JWT failed OIDC verification.
* **Solution:** send a valid key. On a fresh database, bootstrap the first admin (see the next entry). To check a key, list principals with an admin key (`list_principals`). A revoked key can't be restored: create a new principal.
* **Verification:** a `tools/call` for `verify_audit` returns `✓ Audit chain INTACT — …`.

### `Access denied: authentication required`

```
Access denied: authentication required. Provide a valid API key (Authorization: Bearer vayl_sk_…).
```

* **Cause:** `VAYL_AUTH_REQUIRED` is on and no principal is bound. The usual case is the Docker bootstrap: the image sets `VAYL_AUTH_REQUIRED=1`, so a one-off `python -c "…create_principal(…)"` in the container is denied.
* **Solution:** turn it off for the bootstrap command only:

  ```bash
  docker compose run --rm -e VAYL_AUTH_REQUIRED=0 vayl python -c \
    "from vayl.api import mcp_server as s; print(s.create_principal('admin', role='admin'))"
  ```

  Outside Docker, run `vayl-mcp` without `VAYL_AUTH_REQUIRED` and call `create_principal("you", role="admin")`.
* **Verification:** the command prints `Created principal … API key (shown once — save it now):` followed by the key.

### `413` or `429`

```json
{"error": "request body too large"}
{"error": "rate limit exceeded"}
```

* **Cause:** `413` means the body is over `VAYL_MAX_BODY` (1 MiB by default). `429` means the client IP sent more than `VAYL_RATE_PER_MIN` requests (120 by default) in the last minute. Two common surprises: the limit is per process, and behind a proxy without `VAYL_TRUSTED_PROXY_HOPS` every request appears to come from the proxy's IP, so all clients share one bucket.
* **Solution:** behind a proxy, set `VAYL_TRUSTED_PROXY_HOPS` to the number of proxies in front of Vayl. Raise `VAYL_RATE_PER_MIN`, or set it to `0` to disable it and rate-limit at the proxy instead.
* **Verification:** the `X-Request-ID` of a rejected request appears in the access log (at `INFO`) with status `429` or `413`; after the change, it doesn't.

### `421 Misdirected Request` or `403 Forbidden Origin` behind a proxy

* **Cause:** DNS-rebinding protection is always on. By default the server only answers `Host` values of `127.0.0.1`, `localhost` or `[::1]` (any port), and only accepts browser `Origin`s on those hosts. A request through a proxy carries your public host name.
* **Solution:** set `VAYL_ALLOWED_HOSTS` to your public host (for example `memory.acme.com`), and `VAYL_ALLOWED_ORIGINS` for browser clients.
* **Verification:** an authenticated request through the proxy returns `200`.

### `/readyz` returns `503`

```json
{"status": "not-ready"}
```

* **Cause:** the server can't run a query against its database (Postgres down, wrong `VAYL_DATABASE_URL`, file permissions on SQLite). The probe is unauthenticated, so it gives no detail.
* **Solution:** check the database and the server log.
* **Verification:** `curl localhost:8080/readyz` returns `{"status":"ready"}`.

### `/metrics` returns `401 unauthorized`

* **Cause:** `VAYL_METRICS_TOKEN` is set, so `/metrics` needs `Authorization: Bearer <that token>`. It's a separate token, not an API key.
* **Solution:** configure your scraper with the metrics token as a bearer token.
* **Verification:** `curl -H "Authorization: Bearer $VAYL_METRICS_TOKEN" localhost:8080/metrics` prints `vayl_…` counters.

## Access denied from a tool

All denials are recorded in the audit log as `access_denied`.

### `requires the '<capability>' capability`

```
Access denied: 'confirm_change' requires the 'approve' capability; your role(s) ['agent'] do not grant it.
```

* **Cause:** the key's role doesn't grant the tool's capability. Common cases: `confirm_change` and `reject_change` need `approve` (admin and member only, since 0.6.0); `set_reconcile_policy`, `create_principal`, `delete_all` and `purge_expired` need `admin`; `delete` needs `delete`.
* **Solution:** make the call with a key whose role has the capability. The role table is in [Authentication and access](../core-concepts/authentication-and-access.md).
* **Verification:** the same call with the right key returns its normal result, for example `Approved #4: SUPERSEDE …`.

### `targets a memory space outside your assigned scope`

```
Access denied: 'recall' targets a memory space outside your assigned scope.
```

* **Cause:** the key was created with `scopes`, and the call passed a `user_id` not in that list. The message deliberately doesn't echo the requested id.
* **Solution:** use a `user_id` within the key's scopes, or issue a key with the right scopes. Admin keys are never scope-restricted.
* **Verification:** the call with an in-scope `user_id` succeeds.

### Writing as a trusted source

```
Access denied: 'fhir' is a trusted source (VAYL_TRUSTED_SOURCES), so its changes skip the confirmation gate. Only a key named 'fhir', or one with the 'approve' capability, may write as it.
```

* **Cause:** `remember(source="fhir")` where `fhir` is listed in `VAYL_TRUSTED_SOURCES`, from a key that isn't named `fhir` and lacks `approve` (0.6.0).
* **Solution:** give the feed its own principal named exactly like the source (`create_principal("fhir", role="agent")`), or drop the `source` so the change goes through the approval queue.
* **Verification:** the write returns `Stored: …` rather than a denial.

### Tenant admin denials (multi-tenant deployments)

```
Access denied: you can only create principals in your own tenant.
Access denied: stats are deployment-wide; ask the deployment operator.
Access denied: the audit log is shared by every tenant, so only the deployment operator may purge it (include_audit).
```

* **Cause:** since 0.7.0, only the deployment operator (an admin of the `default` tenant, including the local stdio admin) reaches across tenants. The first line is `create_principal` with a `tenant` other than the caller's; the second is `stats` from any caller outside `default`; the third is `purge_expired(include_audit=True)` from anyone but the operator.
* **Solution:** leave `tenant` empty to create a principal in your own tenant, and ask the operator for deployment-wide numbers or an audit purge. `include_decisions` and `include_receipts` work for a tenant admin and purge only its tenant. See [The deployment operator](../core-concepts/authentication-and-access.md#the-deployment-operator).
* **Verification:** the call without the cross-tenant part succeeds, for example `create_principal("acme-viewer", role="viewer")` returns `Created principal … tenant: acme`.

### A principal, decision or receipt from another tenant isn't found

* **Cause:** a tenant admin's `revoke_principal` on another tenant's id returns `No active principal <id> (unknown or already revoked).`, and `explain_decision` or `verify_receipt` on a row recorded in another tenant returns `No decision #N in this scope.` or `No receipt #N.` Since 0.7.0 these are confined to the caller's tenant. Rows written before the upgrade are in `default`.
* **Solution:** make the call with a key in the tenant that owns the row, or as the deployment operator for principals.
* **Verification:** `list_principals` from that key lists the principal.

### `Seat limit reached`

```
Seat limit reached: 3 active principal(s) allowed on the community edition. Revoke an unused principal, or install a license with more seats (see mint_license.py / VAYL_LICENSE).
```

* **Cause:** `create_principal` would exceed the license's seat count. Community allows 3 active principals. The local stdio admin doesn't count.
* **Solution:** `revoke_principal` an unused one (a revoked principal stops counting), or install a license with more seats.
* **Verification:** as the deployment operator, `license_status` shows `principals in use:` below the cap. Seats are counted across every tenant.

## Tool results that look wrong

### `Vayl couldn't complete that (ref …)`

```
Vayl couldn't complete that (ref cc052fb0). Retry if it was a transient blip; otherwise the full detail is in the server logs under that reference.
```

* **Cause:** the tool raised an exception. The client gets only a reference, because exception text can contain memory content.
* **Solution:** search the server's stderr log for the reference. The `ERROR` line names the exception type and code location:

  ```
  2026-09-26 17:58:18,640 ERROR vayl.api.mcp_server [3f9a1c2e4b7d8a01]: [vayl cc052fb0] recall: CriticalOverflow at llm_memory.py:934 in query
  ```

  On `vayl-server`, the bracketed value before it is the request ID (`-` on stdio). Search for that ID to see everything the request did, including the access line. For the exception text, reproduce with `VAYL_LOG_LEVEL=DEBUG`, and treat that log as sensitive. An admin can also read the recent error text with `stats`.
* **Verification:** a retry returns a normal result, or the log names the cause to fix. `CriticalOverflow` is covered below.

### `the model provider rejected the request (HTTP …)`

```
Vayl couldn't complete that (ref 6d1652a3): the model provider rejected the request (HTTP 400). Run the `health` tool to see the provider's reason.
```

* **Cause:** the LLM or embedding API refused Vayl's request: a wrong key (`401`), a model the key can't use (`404`), a parameter the model doesn't accept (`400`), or a quota limit (`429`). The tool result shows only the status, because the provider's message can quote the request.
* **Solution:** run `health`. Its probe sends no memory content, so it prints the provider's reason:

  ```
  llm: FAIL (HTTP 400: Unsupported value: 'reasoning_effort' does not support 'minimal' with this model. Supported values are: 'none', 'low', 'medium', 'high', and 'xhigh'.)
  ```

  Fix the key, the model name or the setting it names, then restart the server.
* **Verification:** `health` reports `llm: ok` and `embedder: ok`.

### `CriticalOverflow` in the log

* **Cause:** a recall found more facts in critical categories than `VAYL_CRITICAL_BUDGET` (200 by default). Vayl fails the read instead of silently dropping some of them.
* **Solution:** raise `VAYL_CRITICAL_BUDGET`, or narrow `VAYL_CRITICAL_CATEGORIES`.
* **Verification:** the same recall returns an answer.

### `recall` says it doesn't know, but the fact was stored

* **Wrong space.** Recall must use the same `user_id` / `agent_id` / `run_id` the fact was stored under. `list_memories` for that space shows what's there.
* **It's history.** A superseded or retracted fact isn't used by a normal recall, by design. Use `include_history=True` for questions about the past, or `history(subject)`.
* **Retrieval miss in a large space.** For a fact that must always reach the answer (an allergy), put its category in `VAYL_CRITICAL_CATEGORIES` so it bypasses ranking.

Recall answers are written by the model, so wording varies between runs and models.

### A change wasn't applied

* **Cause:** the slot is declared with `confirm: true`, so a replacement, a removal, or a new item on a slot that already holds a value is recorded as a proposal and the current value stands. `remember` reports it as `Stored: [FLAG] …`.
* **Solution:** list proposals with `pending_changes()`, then `confirm_change(memory_id)` or `reject_change(memory_id)` with a key that has `approve`. See [Safety gates and human approval](../guides/safety-gates-and-human-approval.md).
* **Verification:** after approval, `list_memories` shows the new value.

`forget` on a confirm-gated slot is gated the same way. It replies `Proposed for removal, awaiting approval …` (or `Already awaiting approval …` if that removal is already queued), and `pending_changes()` lists it as `REMOVE <subject>: '<value>'`.

### Is the audit trail intact?

* `verify_audit()` recomputes every hash and signature. It returns `✓ Audit chain INTACT — N entries verified.` or `⚠ Audit chain BROKEN at seq N: <reason>.`
* `verify_receipt(receipt_id)` checks an erasure receipt or attestation: `✓ VALID — signature verified, payload intact` or `⚠ INVALID — …`.
* `export_public_key()` returns the Ed25519 public key, so a third party can verify signatures offline without the database.

A broken chain means rows were edited, reordered or deleted outside Vayl. Restore from backup and investigate access to the database. See [Accountability](../mcp-tools/accountability.md).

## Still stuck?

Open an issue at [github.com/ac12644/vayl](https://github.com/ac12644/vayl/issues) with the `health` output and the relevant log lines (at `WARNING` or `INFO`, not `DEBUG`).

## Next steps

<table data-view="cards"><thead><tr><th></th><th></th><th data-hidden data-card-target data-type="content-ref"></th></tr></thead><tbody><tr><td><h4><i class="fa-sliders" style="color:$primary;">:sliders:</i> Configuration</h4></td><td>Every environment variable, with defaults. Most fixes start here.</td><td><a href="configuration.md">configuration.md</a></td></tr><tr><td><h4><i class="fa-globe" style="color:$primary;">:globe:</i> Deploying vayl-server</h4></td><td>Auth, TLS, proxy and host settings for the team server.</td><td><a href="../guides/deploying-vayl-server.md">deploying-vayl-server.md</a></td></tr><tr><td><h4><i class="fa-lock" style="color:$primary;">:lock:</i> Authentication &#x26; access</h4></td><td>Roles, capabilities and scopes behind every denial.</td><td><a href="../core-concepts/authentication-and-access.md">authentication-and-access.md</a></td></tr></tbody></table>
