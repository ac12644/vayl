---
description: >-
  Every environment variable Vayl reads, grouped by concern, with exact
  defaults, which values are required, and which are secrets.
icon: sliders
---

# Configuration

Every Vayl setting is an environment variable. There is no config file. Set them in your MCP client's `env` block, your shell, a `.env` file for `docker compose`, or your orchestrator's secret store.

Two markers are used in the tables below:

* **Required when …** means Vayl refuses to start (or the feature stays off) without it under that condition. No variable is required unconditionally: with nothing set, Vayl runs against a local Ollama and a SQLite file in the working directory.
* **Sensitive** means the value is a secret or contains one. Keep it out of source control, shell history and logs.

## How values are parsed

As of 0.6.0, settings parse strictly. A malformed value fails with a message that names the variable, instead of being silently reinterpreted:

| Kind | Accepted values | Error on anything else |
| --- | --- | --- |
| On/off switch | `on` `off` `true` `false` `yes` `no` `1` `0`, any case | `VAYL_ENCRYPT must be on or off, got 'maybe'` |
| Integer | a whole number | `VAYL_PORT must be an integer, got 'eighty'` |
| Number | an integer or decimal | `VAYL_SLOT_RESOLVE_SIM must be a number, got 'high'` |
| Enumerated | the listed values only | for example `VAYL_KMS must be 'file' or 'vault', got 'aws'` |

The on/off switches are `VAYL_AUTH_REQUIRED`, `VAYL_GRAPH`, `VAYL_ENCRYPT`, `VAYL_SIGN`, `VAYL_DEDUP_PREFILTER`, `VAYL_SLOT_RESOLVE`, `OPENAI_JSON` and `VAYL_DECRYPT_CACHE`. An unset or empty variable always means "use the default".

Most variables are read at startup, so a typo stops `vayl-mcp` or `vayl-server` before it serves anything. A few are read on each model call (`OPENAI_MAX_TOKENS`, `OPENAI_TEMP`, `GROQ_TEMP`, `LLM_TIMEOUT`). A bad value there fails that call, which the client sees as `Vayl couldn't complete that (ref …)`. The server-only variables (`VAYL_PORT`, `VAYL_RATE_PER_MIN`, `VAYL_MAX_BODY`, `VAYL_TRUSTED_PROXY_HOPS`) are only read, and only checked, by `vayl-server`.

## Core and storage

| Variable | Default | Description |
| --- | --- | --- |
| `VAYL_DB` | `vayl.db` | SQLite database path. `~` is expanded. Relative paths resolve against the working directory, so use an absolute path. Key files are created beside it (`<VAYL_DB>.key`, `<VAYL_DB>.sign.key`). |
| `VAYL_DATABASE_URL` | unset | Postgres URL, for example `postgresql://user:pass@host/vayl`. When set, it replaces SQLite. Needs `pip install "vayl-mcp[postgres]"`. **Sensitive** (contains the password). |

```bash
VAYL_DB=/data/vayl.db
# or, for several vayl-server processes:
VAYL_DATABASE_URL=postgresql://vayl:CHANGE_ME@db.internal:5432/vayl
```

## Model and embedder

Vayl calls a chat model to extract and reconcile facts and to answer recalls, and an embedder to rank facts. Any OpenAI-compatible endpoint works for both.

### Provider selection

| Variable | Default | Description |
| --- | --- | --- |
| `LLM_PROVIDER` | inferred | `openai` (any OpenAI-compatible endpoint, including Ollama and vLLM), `anthropic` or `groq`. Any other value fails at startup. When unset: `anthropic` if `ANTHROPIC_API_KEY` is set, else `groq` if `GROQ_API_KEY` is set, else `openai`. |
| `VAYL_READ_MODEL` | unset (uses the extraction model) | A different model for answering recalls, on whichever provider is active. A cost lever: answers are short, so a cheaper model is usually enough. |

### OpenAI-compatible path (`LLM_PROVIDER=openai`)

| Variable | Default | Description |
| --- | --- | --- |
| `OPENAI_API_KEY` | unset | API key. **Sensitive.** Also used for embeddings when `EMBED_API_KEY` is unset. |
| `OPENAI_BASE_URL` | `https://api.openai.com/v1` if `OPENAI_API_KEY` is set, else `http://localhost:11434/v1` | Chat endpoint. Point it at Ollama, vLLM, Azure OpenAI, an EU-region endpoint or any OpenAI-compatible server. |
| `OPENAI_MODEL` | `qwen2.5:3b` when the base URL contains `localhost` or `127.0.0.1`, else `gpt-5-mini` | Extraction model (and answer model unless `VAYL_READ_MODEL` is set). |
| `OPENAI_MAX_TOKENS` | `400` for extraction, `300` for answers; `2000` for `gpt-5` and later and `o<digit>*` models | Output token cap. Sent as `max_completion_tokens` to reasoning models and `max_tokens` to everything else. |
| `OPENAI_TEMP` | `0.0` | Temperature. Not sent to reasoning models, which only allow their default. |
| `OPENAI_REASONING_EFFORT` | `minimal` (`none` from gpt-6) | `reasoning_effort` for `gpt-5` and later and `o<digit>*` models only. |
| `OPENAI_JSON` | `on` | Request JSON mode (`response_format: json_object`) for extraction. Set `off` for a model or server that rejects it. |
| `OPENAI_SYSTEM_PREFIX` | empty | Text prepended to every system prompt, for example `/no_think` for a reasoning model you want to answer directly. Also applies to answers on the Anthropic and Groq paths. |

When no key and no base URL are set, the key sent to the local endpoint is the placeholder `ollama`. A non-local base URL with no key sends `none`.

### Anthropic and Groq

| Variable | Default | Description |
| --- | --- | --- |
| `ANTHROPIC_API_KEY` | unset | Anthropic key. Selects `anthropic` when `LLM_PROVIDER` is unset. **Sensitive.** |
| `ANTHROPIC_MODEL` | `claude-haiku-4-5-20251001` | Anthropic model for extraction and answers. |
| `GROQ_API_KEY` | unset | Groq key. Selects `groq` when `LLM_PROVIDER` is unset and no Anthropic key is set. **Sensitive.** |
| `GROQ_MODEL` | `llama-3.3-70b-versatile` | Groq model for extraction and answers. |
| `GROQ_TEMP` | `0.0` | Temperature for Groq extraction. |

{% hint style="warning" %}
Anthropic and Groq serve chat only. Embeddings always go through the OpenAI-compatible embedder below, so with only `ANTHROPIC_API_KEY` or `GROQ_API_KEY` set, Vayl embeds against a local Ollama at `http://localhost:11434/v1`. Run Ollama with `nomic-embed-text`, or set `EMBED_BASE_URL` / `EMBED_API_KEY` / `EMBED_MODEL`.
{% endhint %}

### Embedder

| Variable | Default | Description |
| --- | --- | --- |
| `EMBED_BASE_URL` | see below | Embeddings endpoint (OpenAI-compatible `/embeddings`). |
| `EMBED_API_KEY` | `OPENAI_API_KEY`, else the placeholder `ollama` | Embeddings key. **Sensitive.** |
| `EMBED_MODEL` | `text-embedding-3-small` when the endpoint host is `api.openai.com`, else `nomic-embed-text` | Embedding model. |

The embeddings endpoint resolves in this order (0.6.0):

1. `EMBED_BASE_URL`, if set.
2. `OPENAI_BASE_URL`, if set.
3. `https://api.openai.com/v1` if `OPENAI_API_KEY` is set.
4. `http://localhost:11434/v1` otherwise.

So `OPENAI_API_KEY` alone gives you OpenAI chat and OpenAI embeddings, and nothing at all gives you Ollama for both. See [Local models with Ollama](../guides/local-models-with-ollama.md) for the offline setup and for mixing providers.

{% hint style="warning" %}
Changing the embedding model later changes the vector size (for example 768 for `nomic-embed-text`, 1536 for `text-embedding-3-small`). Existing facts are not re-embedded automatically. Vayl detects the mixed sizes and ranks those recalls by keyword instead of failing.
{% endhint %}

### Transport

| Variable | Default | Description |
| --- | --- | --- |
| `LLM_TIMEOUT` | `60` | Seconds per request for embeddings and for extraction on the OpenAI-compatible and Groq paths. Answers, and Anthropic extraction, use a fixed 30-second timeout. |
| `VAYL_HTTP_POOL` | `8` | Keep-alive connections kept open to the model endpoint. Only used when `urllib3` is installed (`pip install "vayl-mcp[pooled]"`); without it every call opens a new connection. |

Model and embedder calls retry on HTTP 429, 500, 502, 503 and on connection errors, with exponential backoff (up to 10 attempts). The `health` tool makes one attempt only, so it reports an unreachable endpoint immediately.

{% hint style="info" %}
With no model variables set, Vayl uses a local Ollama and nothing leaves the machine. Which endpoint you configure, not Vayl, decides your data-residency posture.
{% endhint %}

## Write path and reconciliation

| Variable | Default | Description |
| --- | --- | --- |
| `VAYL_RECONCILE_CONTEXT` | `40` | How many active facts the extractor sees per write. A space at or below this size sends all of them; a larger space sends the most relevant. Reconciliation still checks the full active set. |
| `VAYL_RECALL_CONTEXT` | `40` | How many facts the answer model sees per recall. At or below this size, every fact is passed and no query embedding is made. |
| `VAYL_EXTRACT_RETRIES` | `2` | Extra attempts when the model returns unparseable JSON. Negative values are treated as `0`. |
| `VAYL_DEDUP_PREFILTER` | `on` | Skip the extractor for a verbatim restatement (after case and whitespace folding) of facts that are all still active. |
| `VAYL_SLOT_RESOLVE` | `off` | Fold near-duplicate subject names that carry the same value (for example a subject repeated with a date appended) into one slot. Only turns an ADD into a DEDUP; never supersedes. Off by default because it changes stored subject names. |
| `VAYL_SLOT_RESOLVE_SIM` | `0.6` | Similarity threshold for `VAYL_SLOT_RESOLVE`. |
| `VAYL_SLOT_SCHEMA` | unset | Path to a declared-slot schema JSON, or a built-in preset: `preset:assistant`, `preset:clinical`, `preset:coding`, `preset:finance`, `preset:sales`, `preset:support`. A missing file, malformed JSON or unknown preset fails at startup. |
| `VAYL_CRITICAL_CATEGORIES` | unset | Comma-separated fact categories that bypass ranking and always reach recall and `safe_recall` (case-insensitive). The bundled `clinical` and `finance` presets tag their critical slots `critical`. |
| `VAYL_CRITICAL_BUDGET` | `200` | Maximum number of critical facts per read. Above it the read fails (`CriticalOverflow`) instead of silently dropping some. |
| `VAYL_TRUSTED_SOURCES` | unset | Comma-separated `source` values whose changes skip the confirmation gate, for authorized feeds such as `fhir,hl7` (case-insensitive). Since 0.6.0, only a key whose principal name equals the source, or a caller with the `approve` capability, may write with that source. |
| `VAYL_CONFIRM_MODE` | unset | `review` or `auto`, for every confirm-gated slot, overriding each slot's own `confirm`. `review` queues every change for approval; `auto` applies clear changes and queues uncertain ones. Unset uses the schema (the clinical preset: medications `auto`, code status `review`). Any other value fails at startup. |

The prefilter only skips when re-extraction is provably a no-op. If any fact from that utterance is no longer active, it falls through to the extractor, so a restatement that should supersede is never missed. See [Safety gates and human approval](../guides/safety-gates-and-human-approval.md) for slot schemas, critical categories and trusted sources in context.

## Security and keys

| Variable | Default | Description |
| --- | --- | --- |
| `VAYL_ENCRYPT` | `on` | Encrypt memory at rest. `off` stores plaintext. |
| `VAYL_SIGN` | `on` | Sign the audit chain, receipts and attestations with Ed25519. Independent of `VAYL_ENCRYPT`. |
| `VAYL_KEY` | unset | Passphrase. When set, the encryption and signing keys are derived from it and never written to disk, and `VAYL_KMS` is not used. **Sensitive.** |
| `VAYL_KDF` | `argon2id` | KDF for `VAYL_KEY` on a fresh deployment: `argon2id` or `scrypt`. Recorded in a marker file on first use and fixed from then on. A deployment whose salt predates the marker stays on scrypt. |
| `VAYL_KMS` | `file` | Key custody when `VAYL_KEY` is unset: `file` (auto-generated key files beside the database, mode 0600) or `vault` (HashiCorp Vault Transit). Any other value fails at startup. |
| `VAULT_ADDR` | `http://127.0.0.1:8200` | Vault address. |
| `VAULT_TOKEN` | unset | Vault token. **Required when** `VAYL_KMS=vault`. **Sensitive.** |
| `VAYL_VAULT_TRANSIT_MOUNT` | `transit` | Transit secrets engine mount path. |
| `VAYL_VAULT_TRANSIT_KEY` | `vayl` | Transit key name. |

Encryption and signing fail closed. If either is on (the default) and its key material or the `cryptography` package is unavailable, Vayl refuses to start rather than run unprotected. With `VAYL_KMS=vault`, an unreachable Vault also stops startup.

## Server (`vayl-server`)

| Variable | Default | Description |
| --- | --- | --- |
| `VAYL_HOST` | `127.0.0.1` | Bind address. |
| `VAYL_PORT` | `8080` | Port. |
| `VAYL_AUTH_REQUIRED` | `off` for `vayl-mcp` | When on, every tool call needs an authenticated principal. `vayl-server` always forces it on. Leave it off for local stdio, where the caller is a trusted local admin. |
| `VAYL_ALLOWED_HOSTS` | `127.0.0.1:*`, `localhost:*`, `[::1]:*` | Comma-separated `Host` header values the server answers to (DNS-rebinding protection, always on). Set your public host when behind a proxy. |
| `VAYL_ALLOWED_ORIGINS` | `http://localhost:*`, `http://127.0.0.1:*` | Comma-separated `Origin` values allowed for browser clients. |
| `VAYL_TRUSTED_PROXY_HOPS` | `0` | Number of trusted proxies in front of Vayl. Above 0, the client IP for rate limiting is read from `X-Forwarded-For`. At 0, that header is ignored. |
| `VAYL_RATE_PER_MIN` | `120` | Requests per minute per client IP, per process. `0` disables the limit. |
| `VAYL_MAX_BODY` | `1048576` (1 MiB) | Maximum request body in bytes, per request. |
| `VAYL_METRICS_TOKEN` | unset (`/metrics` open) | Bearer token required on `/metrics`. **Sensitive.** |

The rate limit is kept in memory by each process. With N `vayl-server` processes the effective ceiling is N × `VAYL_RATE_PER_MIN`. For one global limit, rate-limit at your proxy or ingress.

## OIDC single sign-on

OIDC lets people sign in with an ID token from your identity provider instead of an API key. It is enabled only when the first three variables are all set and the license grants the `sso` feature. API keys keep working either way. Install the extra with `pip install "vayl-mcp[sso]"`.

| Variable | Default | Description |
| --- | --- | --- |
| `VAYL_OIDC_ISSUER` | unset | Expected `iss` claim. **Required** to enable SSO. |
| `VAYL_OIDC_AUDIENCE` | unset | Expected `aud` claim. **Required** to enable SSO. |
| `VAYL_OIDC_JWKS_URL` | unset | The IdP's JWKS endpoint. **Required** to enable SSO. |
| `VAYL_OIDC_ROLE_CLAIM` | `groups` | Claim holding the user's groups. |
| `VAYL_OIDC_ROLE_MAP` | `{}` | JSON object mapping IdP groups to Vayl roles, for example `{"vayl-admins":"admin","eng":"member"}`. Anything other than a JSON object fails at startup: `VAYL_OIDC_ROLE_MAP must be a JSON object like {"group": "role"}`. |
| `VAYL_OIDC_DEFAULT_ROLE` | `viewer` | Role for a user with no mapped group. An unknown role name falls back to `viewer`. |
| `VAYL_OIDC_SCOPE_CLAIM` | unset | Claim holding the `user_id`s this user may touch (a list or comma-separated string). Unset means SSO users are unrestricted. Set it in a multi-tenant deployment. |

Tokens must be RS256-signed and carry `exp`, `iss` and `aud`.

## Licensing

| Variable | Default | Description |
| --- | --- | --- |
| `VAYL_LICENSE` | unset (Community) | The license string (`vayl_lic_…`) or a path to a file containing it. A missing, invalid or expired license falls back to Community (3 active principals); `license_status` says why. **Sensitive.** |
| `VAYL_VENDOR_PUBKEY` | built-in key, if any | Vendor Ed25519 public key (hex) used to verify `VAYL_LICENSE`. |

## Graph (optional)

| Variable | Default | Description |
| --- | --- | --- |
| `VAYL_GRAPH` | `off` | Mirror facts into Neo4j for relational recall (`recall_related`). Needs `pip install "vayl-mcp[graph]"`. |
| `NEO4J_URI` | `bolt://localhost:7687` | Neo4j Bolt URI. |
| `NEO4J_USER` | `neo4j` | Neo4j user. |
| `NEO4J_PASSWORD` | unset | Neo4j password. **Required when** `VAYL_GRAPH` is on; Vayl refuses to start without it. **Sensitive.** |

If Neo4j is configured but unreachable, Vayl keeps running on the slot store and logs a warning. `health` reports `graph: FAIL (…)`.

## Logging

| Variable | Default | Description |
| --- | --- | --- |
| `VAYL_LOG_LEVEL` | `WARNING` | `DEBUG`, `INFO`, `WARNING`, `ERROR` or `CRITICAL`, any case. Logs go to stderr. Anything else fails at startup naming the variable. |
| `VAYL_LOG_FORMAT` | `text` | `text`, or `json` for one JSON object per line. Any other value fails at startup. |

`ERROR` lines carry a reference, the exception type and the code location, never the exception text, which can contain memory content. That detail is logged only at `DEBUG`, so treat `DEBUG` logs as sensitive.

On `vayl-server`, every line also carries the request ID (see [Deploying vayl-server](../guides/deploying-vayl-server.md), under Logging).

## Performance

| Variable | Default | Description |
| --- | --- | --- |
| `VAYL_VECTOR_CACHE` | `8192` | Decoded embeddings kept in memory for recall ranking (about 6 KB each at 1536 dimensions). `0` turns it off. |
| `VAYL_DECRYPT_CACHE` | `on` | Cache decrypted data between calls. `off` keeps plaintext in memory only while a call runs, and also turns off the vector cache. |
| `VAYL_QUERY_CACHE` | `512` | Recent question embeddings kept in memory, keyed by exact text. |

Every recall ranks every fact in the space. Keep `VAYL_VECTOR_CACHE` above the number of embedded facts in your largest space: facts beyond it are decrypted again on every recall. Both caches are cleared on every hard delete (`delete`, `delete_all`, retention expiry), so erased data doesn't stay in memory.

{% hint style="info" %}
`VAYL_DECRYPT_CACHE=off` is for deployments that want the least possible plaintext in process memory. It costs speed: at 1,000 facts, `remember` goes from 23 to 122 ms and `recall` from 59 to 273 ms. Encryption at rest doesn't protect against someone who can read the running process's memory in either mode.
{% endhint %}

## Python client

Read by the `vayl` Python client (`from vayl import Vayl`), not by the server.

| Variable | Default | Description |
| --- | --- | --- |
| `VAYL_CLIENT_CONNECT_TIMEOUT` | `30` | Seconds to wait for the MCP session to open. |
| `VAYL_CLIENT_CALL_TIMEOUT` | `120` | Seconds to wait for one tool call. |

`vayl-demo` also honours the standard `NO_COLOR` variable.

## Next steps

<table data-view="cards"><thead><tr><th></th><th></th><th data-hidden data-card-target data-type="content-ref"></th></tr></thead><tbody><tr><td><h4><i class="fa-wrench" style="color:$primary;">:wrench:</i> Troubleshooting</h4></td><td>The exact error each bad setting produces, and the fix.</td><td><a href="troubleshooting.md">troubleshooting.md</a></td></tr><tr><td><h4><i class="fa-globe" style="color:$primary;">:globe:</i> Deploying vayl-server</h4></td><td>Where the server and security variables come together.</td><td><a href="../guides/deploying-vayl-server.md">deploying-vayl-server.md</a></td></tr><tr><td><h4><i class="fa-microchip" style="color:$primary;">:microchip:</i> Local models with Ollama</h4></td><td>Run fully offline, or mix a cloud model with local embeddings.</td><td><a href="../guides/local-models-with-ollama.md">local-models-with-ollama.md</a></td></tr></tbody></table>
