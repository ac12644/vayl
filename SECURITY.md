# Vayl — Security & Threat Model

This document is for a security reviewer evaluating Vayl for **on-premise / self-hosted** use
(your team runs Vayl inside your own environment). It states what Vayl secures, what your
environment secures, the honest residual risks, and how to harden a deployment.

> Vayl is early-stage software. It is **secure by default for local, single-tenant use**, but it is **not** independently audited or penetration-tested, and it makes **no "completely secure" claim**. Before a production deployment, run your own security review and a professional pen test.

## Deployment model

Vayl runs entirely inside your environment:

- an **MCP server** (`vayl-mcp`, stdio) for a single local user,
- a **team server** (`vayl-server`, MCP over streamable-HTTP) — **every request requires a Bearer
  credential** (API key or, with a license, an OIDC token); terminate TLS at your reverse proxy.

Memory persists in **SQLite** (default) or **Postgres** (`VAYL_DATABASE_URL`). No component phones
home; there is **no telemetry**. The only outbound network call is to the **LLM/embedding endpoint
you configure** (see Data flow) — plus **Vault**, if you opt into KMS key custody.

## Secure-by-default (out of the box, zero setup)

| Control              | Default                                                                                          |
| -------------------- | ------------------------------------------------------------------------------------------------ |
| Network binding      | `vayl-mcp` is stdio (no socket); `vayl-server` binds **`127.0.0.1`** unless you set `VAYL_HOST`   |
| Server auth          | **On** — `vayl-server` requires a Bearer credential (API key / OIDC) on every request            |
| Telemetry            | **None** — nothing leaves the machine                                                            |
| LLM backend          | **Local Ollama by default** → no data egress unless you configure a cloud LLM                    |
| CSRF / DNS-rebinding | Cross-origin and non-local-`Host` requests rejected                                              |
| Input limits         | Request bodies capped (1 MiB); `Content-Type: application/json` enforced on writes               |
| SQL injection        | Not possible — all queries are parameterized                                                     |
| Path traversal       | Not possible — fixed routes, single served asset                                                 |
| Encryption at rest   | **On, and FAIL-CLOSED** — if encryption is on but unavailable, Vayl refuses to start rather than silently run plaintext (`VAYL_ENCRYPT=off` is the only way to run unencrypted). See scope below. |
| Signed audit chain   | **On, fail-closed** (`VAYL_SIGN=off` to disable) — hash-chained + Ed25519-signed; `verify_audit` pinpoints any edit/reorder/truncation |
| Auth + RBAC (server) | **Required** on `vayl-server` — API-key principals, five roles (admin/member/agent/viewer/auditor), every tool checks its capability fail-closed |
| Dependencies         | Minimal (`mcp`, `cryptography`; optional `neo4j`, `psycopg`, `pyjwt`, `uvicorn`)                 |

## Encryption at rest — exact scope

App-level encryption (Fernet = AES-128-CBC + HMAC) is **on by default** and **fail-closed**: if it's
on but key material or the `cryptography` package is unavailable, Vayl raises at startup instead of
silently writing plaintext. Running unencrypted requires an explicit `VAYL_ENCRYPT=off`.

- **Encrypted:** everything that can carry personal data — statement `subject`, `value`, `slot`,
  `raw`, `metadata`, `embedding`; the audit-log `detail`; **decision snapshots and summaries**;
  **receipt payloads**; **tool-error messages** (`metric_errors` — exception text can echo memory
  content); and **principal names** (team-member personal data).
- **Not encrypted (structural):** `user_id` / `agent_id` / `run_id` (identifiers, used in queries),
  `scope`, `status`, `confidence`, ids, timestamps, and `subject_hmac` — a keyed HMAC blind index
  (irreversible, but reveals which rows share a subject).
- **Outside the boundary:** the optional **Neo4j graph projection** stores entity/relation names in
  Neo4j, not in the encrypted SQLite columns. Erasure purges the edges, but at-rest protection there
  is Neo4j's/your disk's job — enable Neo4j's own encryption or rely on full-disk encryption.
- **Key custody:** `VAYL_KEY` passphrase → key derived with **Argon2id** (OWASP's preferred KDF —
  memory-hard, from the `cryptography` dep, no extra package; parameters pinned in a `<salt>.kdf`
  marker, deployments predating it keep scrypt so existing data stays readable) and kept **off
  disk**; `VAYL_KMS=vault` →
  HashiCorp Vault Transit envelope encryption (master key never leaves Vault; only a wrapped blob on
  disk; **fail-closed** if Vault is unreachable); otherwise an auto-generated `<db>.key` (`0600`) —
  protects a copied DB file, **not** a stolen machine. Pair with OS full-disk encryption.
- **In memory:** a running process holds decrypted data while it serves a call. By default it also
  caches decrypted short fields and decoded embeddings between calls, which makes calls on large memories
  several times faster. Every hard delete (`delete`, `delete_all`, retention expiry via `purge_expired`) clears both caches; `forget` is a retraction that keeps history, so it doesn't.
  `VAYL_DECRYPT_CACHE=off` turns them off, so plaintext stays in memory only while a call runs, at the
  cost of speed (at 1,000 facts: `remember` 23 → 122 ms, `recall` 59 → 273 ms). Encryption at rest
  doesn't protect against someone who can read the process's memory.

Verify: store a value, then `strings vayl.db | grep <value>` returns nothing (ciphertext is `gAAAAA…`).

## Shared responsibility (on-prem)

| Control                                     | Owner          | Notes                                                                                                                         |
| ------------------------------------------- | -------------- | ----------------------------------------------------------------------------------------------------------------------------- |
| Encryption at rest                          | **Shared**     | Vayl encrypts content + audit detail (on by default, see scope above). You provide OS full-disk encryption (FileVault/LUKS/BitLocker) for complete coverage / disk-theft protection. |
| Network firewall / not exposing the port    | **You**        | Vayl defaults to localhost; do not bind `0.0.0.0` without your own auth/proxy.                                                |
| OS/file access controls                     | **You**        | Who can read `vayl.db` and the `.token` file.                                                                                 |
| LLM data residency                          | **You**        | Choosing a cloud LLM sends memory text to that provider (see Data flow).                                                      |
| SSO / enterprise auth (if exposed to users) | **You**        | Front Vayl with your reverse proxy / identity provider.                                                                       |
| No vulnerabilities in the component         | **Vayl**       | See secure-by-default above.                                                                                                  |
| Security-review documentation               | **Vayl**       | This file.                                                                                                                    |

## Dependency & supply-chain posture

Fewer third-party packages = less to audit and patch. Vayl's own code is **stdlib-only** apart from two
essential libraries, and everything else is an **opt-in extra** — a given deployment installs only what
it uses.

| Package | Why | Reducible? |
|---|---|---|
| `cryptography` | at-rest encryption (Fernet) + Ed25519 signing | **No — and must not be.** Rolling your own crypto introduces vulnerabilities; this is the audited, constant-time standard. |
| `mcp` | the Model Context Protocol SDK — the server itself | No — it *is* the product's interface. |
| `uvicorn`, `starlette` *(`[server]`)* | ASGI server + framework for `vayl-server` | No — reinventing an ASGI server/framework is a large, worse-reliability effort. Only installed for the team server. |
| `psycopg` *(`[postgres]`)* | Postgres wire-protocol driver | No — only installed if you use Postgres. |
| `pyjwt` *(`[sso]`)* | OIDC ID-token (JWT) verification | In principle yes (on `cryptography`), but **not worth it**: JWT/JWKS verification is security-sensitive parsing where a battle-tested library beats DIY; only installed if you use SSO. |
| `neo4j` *(`[graph]`)* | Bolt driver | No — only installed if you enable the graph. |

Everything else Vayl does is **stdlib**: `sqlite3`, `hashlib`, `hmac`, `secrets`, `base64`, `json`,
`urllib` (the Vault client is stdlib, not `requests`), `contextvars`, `threading`. The rate-limiter is
stdlib. No `eval`/`exec`/`pickle`/`yaml.load`/`subprocess`/`shell` anywhere in the product code.

- **Minimal install** (`pip install .`) is just `mcp` + `cryptography`.
- **CVEs:** `pip-audit` runs in CI on every push and pull request, and a known vulnerability fails the
  build.
- **Secrets:** GitHub secret scanning with push protection is on for this repository, so a push that
  contains a recognised credential is rejected.
- **Recommended for production:** pin exact versions (a lockfile / hash-pinned constraints) and gate
  releases on `pip-audit`.
- **The one place to resist "build your own":** cryptography and JWT/JWKS verification. Everywhere else,
  Vayl already avoids third-party dependencies.

## Data flow (where personal data goes)

1. `remember(text)` → the text is sent to the **configured LLM** for extraction, and to the
   **embedder** for a vector. → **If you use a cloud LLM, memory text leaves the machine to that
   provider** (a third-party sub-processor; you are responsible for a DPA + transfer safeguards).
2. Reconciled facts + embeddings are stored in **local SQLite**.
3. `recall`/`list`/`history` read from local SQLite; `recall` may make one LLM call for the answer.

**For strict data residency, use the default local LLM (Ollama)** — then no memory data leaves the host.

## Data subject rights (GDPR building blocks)

- **Erasure** (Art. 17): `delete(subject)` / `delete_all()` — hard delete, history included (also
  purges the optional graph), **plus redaction of the erased values from decision snapshots**
  (decisions are re-signed so verification still passes; the redaction itself is audited). A signed
  erasure receipt is issued. This is real removal, not a soft retract.
- **Access & portability** (Art. 15/20): `export_memory` — statements (active + history) **plus the
  subject's decision snapshots, audit entries, and receipts** (a DSAR-complete export).
- **Rectification** (Art. 16): `update_memory` (audit-preserving).
- **Retention** (Art. 5(1)(e)): `purge_expired`, with flags extending the window to the append-only
  tables (audit / decisions / receipts); the audit chain stays verifiable across purges via a
  **signed retention anchor**.
- **Team members** (Art. 17 applies to staff too): principal names are encrypted at rest, and
  `revoke_principal(erase=True)` hard-deletes the record rather than retaining it disabled.

Vayl provides the *technical* building blocks. **You remain the data controller**; using Vayl does
not by itself make your processing compliant.

## Hardening checklist

- [ ] Keep `vayl-server` on `127.0.0.1` (default) or behind your own authenticated, TLS-terminating
      reverse proxy — never expose it raw with `VAYL_HOST=0.0.0.0` and no proxy.
- [ ] Enable OS full-disk encryption; restrict file permissions on `vayl.db` and its key files.
- [ ] For data residency, use the **local LLM** default; if using a cloud LLM, ensure a DPA + SCCs.
- [ ] Leave encryption + signing on (defaults, fail-closed); for production key custody use
      `VAYL_KMS=vault` or a `VAYL_KEY` passphrase.
- [ ] For `vayl-server`: TLS at your reverse proxy, least-privilege roles for agents
      (`agent`/`member`, not `admin`), rotate keys via `revoke_principal` + `create_principal`.
- [ ] Pin dependencies and run `pip-audit` in your pipeline.
- [ ] Run a professional penetration test before production.

## Configuration reference (security-relevant)

| Env                               | Effect                                         | Secure default                   |
| --------------------------------- | ---------------------------------------------- | -------------------------------- |
| `VAYL_ENCRYPT`                    | At-rest encryption (**fail-closed** when on)   | on                               |
| `VAYL_SIGN`                       | Ed25519-signed audit/receipts (fail-closed)    | on                               |
| `VAYL_KEY`                        | Passphrase-derived key, kept off disk          | unset (auto key file)            |
| `VAYL_KDF`                        | Passphrase KDF for a fresh deployment (`scrypt` to opt out) | `argon2id`           |
| `VAYL_KMS` + `VAULT_ADDR/TOKEN`   | Vault Transit key custody (fail-closed)        | `file`                           |
| `VAYL_DECRYPT_CACHE`              | Keep decrypted data cached between calls       | on (`off` for minimal residency) |
| `VAYL_AUTH_REQUIRED`              | Deny tools without a bound principal           | set by `vayl-server`             |
| `VAYL_HOST` / `VAYL_PORT`         | `vayl-server` bind address / port              | `127.0.0.1` / `8080`             |
| `OPENAI_BASE_URL` / provider keys | LLM backend                                    | unset → local Ollama (no egress) |

## Residual risks (honest)

- **Key custody with the default file provider.** The auto-generated key sits next to the database —
  this protects against *copying the DB file* but **not** theft of the whole machine. Use `VAYL_KEY`
  (off-disk) or `VAYL_KMS=vault` (master key in Vault, fail-closed), plus OS full-disk encryption.
- **The signing key is more dangerous to lose than the encryption key.** With the default file
  provider the Ed25519 seed (`<db>.sign.key`) also sits beside the data. Machine theft lets an
  attacker not just *read* but **forge** — re-sign tampered audit rows, mint fake erasure receipts and
  attestations that verify against the exported public key. If receipts/attestations are used as
  evidence, source the signing seed from Vault/an HSM (`VAYL_KMS=vault`), not the local file.
- **The blind subject index shares the encryption key.** `subject_hmac` (deterministic, for equality
  lookups on encrypted subjects) is keyed with the same 32 bytes as at-rest encryption rather than a
  derived sub-key. It reveals only *which rows share a subject*, never the subject. Low severity;
  a future release may move it to a derived sub-key (a one-time re-index of existing encrypted data).
- **The KDF marker (`<salt>.kdf`) is unauthenticated.** An attacker with write access to the data
  directory can alter or delete it. This cannot decrypt existing data (a changed KDF derives a
  *different*, non-matching key), but is an availability/tamper vector — rely on filesystem
  permissions (`0600`, restricted data dir) and OS full-disk encryption.
- **`user_id` is a scoping parameter, not an identity.** Authentication is per *principal* (API key /
  OIDC, with RBAC); an authenticated member can pass any `user_id` within the deployment. One
  deployment = one org = the isolation boundary; per-space ACLs inside an org don't exist yet.
- **TLS is terminated at your proxy.** `vayl-server` speaks plain HTTP and expects a TLS-terminating
  reverse proxy in front; if you bind it to a non-loopback address **without** one, Bearer keys cross
  the network in cleartext. It does not assert TLS itself — keep it on `127.0.0.1` or behind a proxy.
- **The audit log retains encrypted references after erasure** (accountability vs. Art. 17 — the
  documented tension; see `COMPLIANCE.md` for the three resolutions incl. crypto-shredding).
- **Cloud-LLM egress** (above) if you choose a cloud model.
- **Not audited / not pen-tested** by a third party. Do your own.

## Reporting a vulnerability

Please report security issues **privately** — do not open a public issue or PR.

- **Preferred:** GitHub [private vulnerability reporting](https://github.com/ac12644/vayl/security/advisories/new) (repo → **Security** → **Report a vulnerability**).
- **Email:** ac12644@gmail.com

Include a description, the affected version or commit, and steps to reproduce. We aim to
**acknowledge within 3 business days**, agree a remediation timeline after triage, and credit
reporters who want it. Please allow a reasonable window to ship a fix before public disclosure
(coordinated disclosure).

### Supported versions

Security fixes target the **latest released version** (`main`). Older versions are not maintained;
upgrade to receive fixes.
