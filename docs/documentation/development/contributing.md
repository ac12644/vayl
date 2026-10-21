---
description: >-
  How to get a change into Vayl: open an issue, branch, pass the required
  checks, and follow the project's test, lint and changelog conventions.
icon: code-pull-request
---

# Contributing

Bug reports, docs, tests, new LLM providers, and reconciliation edge cases are all welcome. This page covers the workflow and conventions. The short version is in [CONTRIBUTING.md](https://github.com/ac12644/vayl/blob/main/CONTRIBUTING.md) on GitHub, and this page follows it.

By contributing, you agree that your contributions are licensed under Apache-2.0.

## Where to start

Look for the [`good first issue`](https://github.com/ac12644/vayl/labels/good%20first%20issue) label, or pick one of these self-contained tasks:

* **A slot-schema preset for a domain you know** (legal, devops, real estate, recruiting, ...). A preset is one JSON file. Copy `src/vayl/presets/coding.json` or `support.json`, add assertions for it to `tests/test_presets.py`, and it is bundled into the wheel automatically. Users enable it with `VAYL_SLOT_SCHEMA=preset:<name>`. This is the easiest way to contribute.
* **A framework adapter** for another agent framework (LlamaIndex, Pydantic AI, AutoGen, ...). Python adapters live in `src/vayl/integrations/`, subclass `BaseVaylMemory`, and use `langgraph.py` as the template. TypeScript adapters are subpath modules under `clients/typescript/src/integrations/`. Every adapter exposes the same tool set from `_common`.
* **Docs and setup guides**: a walkthrough for another MCP client (Windsurf, Zed), a tutorial, or a worked example. See [Contributing to the docs](#contributing-to-the-docs).

For anything substantial, **open an issue first** so the approach is agreed before you write a large pull request.

## Workflow

{% stepper %}
{% step %}
### Fork and branch

Fork [ac12644/vayl](https://github.com/ac12644/vayl), clone your fork, and create a branch from `main`. Set up the checkout as described in [Local development](local-development.md).
{% endstep %}

{% step %}
### Make the change, with tests

Add or update tests for any new behaviour, and keep them offline (see [Conventions](#conventions)). Run the checks locally:

```bash
pytest
ruff check .
mypy
```
{% endstep %}

{% step %}
### Open a pull request against main

`main` is protected. A pull request needs these checks to pass, and one approving review:

| Required check | What it runs |
| --- | --- |
| `test (3.11)`, `test (3.12)` | ruff, mypy, an import smoke test, and `pytest` on each Python version. On 3.12 it also runs `pip-audit`, which fails on a known-vulnerable dependency. |
| `integration` | The Postgres 17 and Neo4j 5 suites against real services. Fails if any test is skipped. |

CodeQL also analyses every pull request to `main` and reports to the Security tab. TS SDK CI runs when `clients/typescript/` changes. Neither is a required check.
{% endstep %}
{% endstepper %}

## Conventions

* **New behaviour needs a test.**
* **Unit tests stay offline and deterministic.** `tests/conftest.py` fails any test that tries to reach a model or cloud port (Ollama on 11434, HTTP, HTTPS), even if the code swallows the error. Stub the model seams (`llm_memory.llm_extract_classify`, `llm_memory._qa`, `vayl.memory.llm_client._embed`, `vayl.storage.store._embed`) as [Local development](local-development.md#the-offline-guard) shows. Checks that need a real model belong in `benchmarks/`.
* **`ruff check .` and `mypy` must pass.** CI runs both.
* **Keep the core dependencies small.** Anything heavier goes behind an optional extra in `pyproject.toml`, imported lazily, the way the framework adapters are.
* **Audit-chain changes need a concurrency test.** The audit hash chain is a security guarantee, so changes under `src/vayl/security/audit.py` need one (see `tests/test_accountability.py`).
* **Add a CHANGELOG entry** for user-visible changes. Put it in `CHANGELOG.md` under an `## [Unreleased]` heading at the top (add the heading if it isn't there), in the matching subsection (`### Added`, `### Changed`, `### Fixed`, `### Security`, `### Removed`). Breaking changes also get upgrade notes. Say what changed for the user and what they need to do.

### Commit and pull request titles

Titles follow a Conventional Commits style, lowercase after the prefix, with an optional scope:

```
fix(server): stop cancelling every streamed MCP response
feat(storage): versioned schema migrations with a version gate
ci: make pip-audit blocking; document secret scanning
docs(readme): lead with the coding-agent wedge (Cursor/Claude Code)
refactor!: remove the unwired vayl.clinical module
```

Common prefixes are `feat`, `fix`, `perf`, `refactor`, `docs`, `ci`, `chore` and `build`. `!` marks a breaking change. Use the body to explain why the change was needed.

## Contributing to the docs

The docs site at [vayl.gitbook.io/vayl-docs](https://vayl.gitbook.io/vayl-docs) is synced with the `docs/` folder on `main` through GitBook Git Sync. Each section of the site has its own folder: `docs/documentation/` (these pages), `docs/home/`, `docs/api-reference/`, `docs/changelog/` and `docs/help-center/`, and `docs/gitbook-docs.yaml` describes the site's structure.

* **By pull request (the usual way).** Edit the Markdown under `docs/<section>/` and open a pull request against `main`, like any code change. Once it merges, GitBook imports it within about a minute.
* **In GitBook.** Edits merged in GitBook are committed straight to `main` by the GitBook app, which is allowed past `main`'s protection for this. Use a GitBook change request if you want a review first.

The API Reference pages are generated from `openapi/vayl-server.yaml` at the repository root, not from Markdown. Change the spec to change them.

{% hint style="info" %}
Links between pages inside one section use relative `.md` paths. Links into another section use its full `https://vayl.gitbook.io/vayl-docs/<section>/…` URL. Page URLs follow the groups in each section's `SUMMARY.md`, not the folder names.
{% endhint %}

In the docs, use relative links to `.md` files (for example `../reference/configuration.md`), and quote tool output only after running it.

## Reporting bugs and security issues

* **Bugs and feature requests:** open a [GitHub issue](https://github.com/ac12644/vayl/issues). Include your Vayl version (`vayl-mcp --version`) and the output of the `health` tool. Leave out DEBUG logs from real data, since they can contain memory content.
* **Security vulnerabilities:** do not open a public issue or pull request. Report privately through GitHub [private vulnerability reporting](https://github.com/ac12644/vayl/security/advisories/new) (repo → **Security** → **Report a vulnerability**), or by the email address in [SECURITY.md](https://github.com/ac12644/vayl/blob/main/SECURITY.md). The maintainers aim to acknowledge reports within 3 business days.

## Next steps

<table data-view="cards"><thead><tr><th></th><th></th><th data-hidden data-card-target data-type="content-ref"></th></tr></thead><tbody><tr><td><h4><i class="fa-laptop-code" style="color:$primary;">:laptop-code:</i> Local development</h4></td><td>Set up a checkout and run the same checks as CI.</td><td><a href="local-development.md">local-development.md</a></td></tr><tr><td><h4><i class="fa-diagram-project" style="color:$primary;">:diagram-project:</i> How Vayl works</h4></td><td>The architecture you'll be changing.</td><td><a href="../how-vayl-works.md">how-vayl-works.md</a></td></tr><tr><td><h4><i class="fa-puzzle-piece" style="color:$primary;">:puzzle-piece:</i> Agent frameworks</h4></td><td>The adapters a new framework integration should match.</td><td><a href="../integrations/agent-frameworks.md">agent-frameworks.md</a></td></tr></tbody></table>
