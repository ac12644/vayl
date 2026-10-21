---
description: Every command the vayl-mcp package installs, with its arguments, output, and exit codes.
icon: terminal
---

# CLI reference

Installing `vayl-mcp` puts five commands on your `PATH`. Two run the MCP server, one manages the database schema, one is a 30-second demo, and one is a vendor-side license tool. All configuration is environment variables; see [Configuration](configuration.md).

| Command | What it does |
| --- | --- |
| `vayl-mcp` | MCP server over stdio, for one local user. |
| `vayl-server` | MCP server over authenticated streamable HTTP, for a team. |
| `vayl-migrate` | Show or apply database schema migrations, or rebuild the graph. |
| `vayl-demo` | Offline demo of reconciliation. No keys needed. |
| `vayl-license` | Vendor-side: generate the signing keypair and mint customer licenses. |

Output below was captured from vayl 0.8.0.

## vayl-mcp

Runs the stdio transport. Your MCP client (Claude Code, Cursor, Claude Desktop) launches it; you don't run it by hand. The caller is a trusted local admin unless `VAYL_AUTH_REQUIRED` is on.

```
$ vayl-mcp --help
usage: vayl-mcp [-h] [--version]

Vayl MCP server over stdio, for one local user. Run it from an MCP client (Claude Desktop, Cursor,
Claude Code), not by hand.

options:
  -h, --help  show this help message and exit
  --version   show program's version number and exit

Configuration: environment variables, see https://vayl.gitbook.io/vayl-docs
```

```
$ vayl-mcp --version
vayl-mcp 0.8.0
```

`--help` and `--version` answer before the database is opened, so they don't create `vayl.db` in the current directory. On start, the server validates settings such as `LLM_PROVIDER` and `VAYL_LOG_FORMAT` and exits with an error naming a bad value. Logs go to stderr; stdout belongs to the MCP protocol.

## vayl-server

Runs the streamable HTTP transport on `VAYL_HOST:VAYL_PORT` (default `127.0.0.1:8080`). Auth is always required. Routes and status codes are in [HTTP endpoints](http-endpoints.md).

```
$ vayl-server --help
usage: vayl-server [-h] [--version]

Vayl MCP server over authenticated streamable HTTP, for a team. Listens on VAYL_HOST:VAYL_PORT
(default 127.0.0.1:8080).

options:
  -h, --help  show this help message and exit
  --version   show program's version number and exit

Configuration: environment variables, see https://vayl.gitbook.io/vayl-docs
```

```
$ vayl-server --version
vayl-server 0.8.0
```

On start it prints the endpoint (the uvicorn lines go to stderr):

```
Vayl server → http://127.0.0.1:8080/mcp   (auth: REQUIRED · Bearer vayl_sk_… or OIDC JWT)
  health: /healthz   ready: /readyz   ·   put TLS/ingress in front; don't expose raw.
```

If the database has no principals yet, it adds a line telling you to bootstrap an admin; see [`create_principal`](../mcp-tools/administration.md#create_principal). If OIDC is configured but the license doesn't grant `sso`, it prints a warning and serves API keys only.

## vayl-migrate

Shows or applies versioned schema migrations against `VAYL_DATABASE_URL` (Postgres) if set, otherwise `VAYL_DB` (default `vayl.db`). Every Vayl process also migrates on startup, so you only need `up` to migrate once before rolling out several server processes.

```
vayl-migrate [status|up|reproject-graph]
```

With no argument it runs `status`. On a fresh database:

```
$ vayl-migrate status
  v1  baseline             PENDING
  v2  policy-per-tenant    PENDING
  v3  tenant-accountability PENDING
schema: v0 (this Vayl: v3)
```

```
$ vayl-migrate up
applied: 1, 2, 3
  v1  baseline             applied 2026-09-26T19:01:45+00:00
  v2  policy-per-tenant    applied 2026-09-26T19:01:45+00:00
  v3  tenant-accountability applied 2026-09-26T19:01:45+00:00
schema: v3 (this Vayl: v3)
```

When nothing is pending, `up` prints `nothing to apply` before the table.

| Version | Migration | Additive |
| --- | --- | --- |
| v1 | `baseline`: the schema as of 0.4–0.6, including every in-place upgrade earlier releases made. | Yes |
| v2 | `policy-per-tenant`: rebuilds the reconcile-policy table with the tenant in its key, keeping existing policies. | **No.** Back up first; 0.6 refuses to start on a v2 database, so rolling back means restoring the backup. |
| v3 | `tenant-accountability`: adds a `tenant_id` column to `decisions`, `receipts` and `audit`. Existing rows are stamped `default`. The audit hash chain doesn't cover the column, so existing chains still verify. | Yes |

### vayl-migrate reproject-graph

Wipes the Neo4j graph and rebuilds it from the store for every tenant. It needs the same graph settings as the server (`VAYL_GRAPH=on`, `NEO4J_URI`, `NEO4J_USER`, `NEO4J_PASSWORD`). Run it once after upgrading from 0.6 or earlier with the graph enabled: edges written before 0.7 use a namespace without the tenant, so `recall_related` doesn't find them and erasure doesn't remove them until they're rebuilt.

On success it prints the number of edges it wrote: `rebuilt the graph: <N> edges across all tenants`.

Without a graph configured it exits with `1`:

```
error: the graph is not enabled. Set VAYL_GRAPH=on and NEO4J_URI / NEO4J_USER / NEO4J_PASSWORD, the same as for the server.
```

| Exit code | Meaning |
| --- | --- |
| `0` | Success. |
| `1` | `reproject-graph` without a graph configured, or the database schema is newer than this Vayl knows (`error: database schema is at version N, but this Vayl only knows up to M. …`). Run the newer version, or restore the backup taken before the upgrade. |
| `2` | Unknown subcommand; prints `usage: vayl-migrate [status|up|reproject-graph]`. |

## vayl-demo

Runs the real reconciliation engine on a four-line scripted conversation and prints what is true now and what history keeps. The default is offline: pre-extracted facts, no model or network.

```
vayl-demo [--live | --offline]
```

`--live` extracts the facts from the raw sentences with your LLM instead (best with a capable model). There is no `--help`: any other argument just runs the demo.

```
$ vayl-demo

  Vayl — reconciling memory for AI agents
  mode: offline — engine on pre-extracted facts (no model needed)

  The conversation:
    you: We use Redux for state management.
    you: Actually, we moved off Redux to Zustand.
    you: We use Sentry for error monitoring.
    you: We dropped Sentry.

  What's true now  (the active set — what an agent gets back):
    state         : Zustand

  Ask it:
    Q: what do we use for state?   A: Zustand
       → Zustand, not "Redux, Zustand" — the switch superseded Redux.
    Q: are we using Sentry?         A: no — retracted

  The history is still there  (nothing is lost — it just left the hot path):
    state = Redux        [SUPERSEDED]
    state = Zustand      [ACTIVE]
    monitoring = Sentry       [SUPERSEDED]
    monitoring = (retracted: Sentry) [HISTORICAL]

  That's reconciling memory: one live value per fact, removal is real, history kept.
  (Point Vayl at any OpenAI-compatible LLM and it extracts all of this from raw text.)

  Next: pip install vayl-mcp  ·  docs: https://vayl.gitbook.io/vayl-docs
```

In a terminal the output is coloured; set `NO_COLOR` to turn that off. If a local LLM looks reachable, offline mode adds a hint to try `--live`.

## vayl-license

The vendor's license tool: generate the Ed25519 keypair once, then sign per-customer license blobs. Customers don't run it; they receive a blob and set `VAYL_LICENSE` to it (or to a file path containing it).

```
$ vayl-license --help
usage: vayl-license [-h] {keygen,mint} ...

Vayl vendor license tool

positional arguments:
  {keygen,mint}
    keygen       generate the vendor keypair (once)
    mint         sign a customer license

options:
  -h, --help     show this help message and exit
```

### keygen

Prints a new private seed and public key to stdout. It writes no files.

```
$ vayl-license keygen
Vendor keypair generated. Do this ONCE and keep the seed offline & secret.

PRIVATE SEED (hex) — SECRET, never commit, store in your vault:
  <64 hex chars>

PUBLIC KEY (hex) — safe to embed/ship (license.DEFAULT_VENDOR_PUBKEY or VAYL_VENDOR_PUBKEY):
  <64 hex chars>
```

{% hint style="danger" %}
The seed is printed in the clear. Run `keygen` where the terminal output isn't logged, and move the seed straight into a secrets vault.
{% endhint %}

### mint

```
$ vayl-license mint --help
usage: vayl-license mint [-h] --seed SEED --customer CUSTOMER [--edition {enterprise,business}]
                         [--seats SEATS] [--features FEATURES] --expires EXPIRES

options:
  -h, --help            show this help message and exit
  --seed SEED           vendor PRIVATE seed hex (from keygen)
  --customer CUSTOMER
  --edition {enterprise,business}
  --seats SEATS
  --features FEATURES   comma-separated (e.g. sso,kms,postgres)
  --expires EXPIRES     ISO date YYYY-MM-DD
```

| Option | Default | Notes |
| --- | --- | --- |
| `--seed` | required | Private seed from `keygen`. Passing it as an argument puts it in shell history. |
| `--customer` | required | Customer name, stored in the license. |
| `--edition` | `enterprise` | `enterprise` or `business`. |
| `--seats` | `10` | Maximum active principals. |
| `--features` | none | Comma-separated, for example `sso,kms,postgres`. `sso` enables OIDC on `vayl-server`. |
| `--expires` | required | `YYYY-MM-DD`. |

```
$ vayl-license mint --seed <SEED> --customer "Acme GmbH" --edition enterprise \
    --seats 25 --features sso,kms,postgres --expires 2027-01-01
# License for Acme GmbH — enterprise, 25 seats, expires 2027-01-01, features: ['sso', 'kms', 'postgres']
vayl_lic_eyJjdXN0b21lciI6IkFjbWUgR21iSCIs…
```

A license is verified against `VAYL_VENDOR_PUBKEY`, falling back to the key compiled into the package (`license.DEFAULT_VENDOR_PUBKEY`). That built-in key is empty, and the published wheels are built from the same source without injecting one. So unless `VAYL_VENDOR_PUBKEY` is set on the server, every license is ignored and the deployment runs as Community. Check the result with [`license_status`](../mcp-tools/administration.md#license_status).

## Next steps

<table data-view="cards"><thead><tr><th></th><th></th><th data-hidden data-card-target data-type="content-ref"></th></tr></thead><tbody><tr><td><h4><i class="fa-network-wired" style="color:$primary;">:network-wired:</i> HTTP endpoints</h4></td><td>The routes <code>vayl-server</code> serves, with a verified curl session.</td><td><a href="http-endpoints.md">http-endpoints.md</a></td></tr><tr><td><h4><i class="fa-sliders" style="color:$primary;">:sliders:</i> Configuration</h4></td><td>Every environment variable these commands read.</td><td><a href="configuration.md">configuration.md</a></td></tr><tr><td><h4><i class="fa-globe" style="color:$primary;">:globe:</i> Deploying vayl-server</h4></td><td>Run the team server with Docker and Postgres.</td><td><a href="../guides/deploying-vayl-server.md">deploying-vayl-server.md</a></td></tr></tbody></table>
