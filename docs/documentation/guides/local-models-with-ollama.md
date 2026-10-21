---
description: >-
  Run Vayl fully offline on Ollama, with no API key and no data leaving the
  machine, or mix a cloud chat model with local embeddings.
icon: microchip
---

# Local models with Ollama

Vayl can run entirely on your machine: a local chat model extracts and reconciles facts, a local embedder ranks them, and nothing leaves the host. Ollama is the default. With no model variables set, Vayl already expects it at `http://localhost:11434`.

Vayl talks to Ollama through its OpenAI-compatible API (`/v1/chat/completions` and `/v1/embeddings`). The same settings work for vLLM, LM Studio or any other OpenAI-compatible server.

## Run fully offline

{% stepper %}
{% step %}
### Install Ollama and pull the two models

Install Ollama from [ollama.com](https://ollama.com), then pull the default chat model and embedder:

```bash
ollama pull qwen2.5:3b
ollama pull nomic-embed-text
```

Ollama serves on `http://localhost:11434` once it's running.
{% endstep %}

{% step %}
### Start Vayl with no model variables

Leave `LLM_PROVIDER`, `OPENAI_*`, `EMBED_*`, `ANTHROPIC_API_KEY` and `GROQ_API_KEY` unset. For example, in an MCP client config:

```json
{
  "mcpServers": {
    "vayl": {
      "command": "vayl-mcp",
      "env": { "VAYL_DB": "/abs/path/vayl.db" }
    }
  }
}
```

With nothing set, Vayl resolves to:

| Setting | Value |
| --- | --- |
| Provider | `openai` (the OpenAI-compatible path) |
| Chat endpoint | `http://localhost:11434/v1` |
| Chat model | `qwen2.5:3b` |
| Embeddings endpoint | `http://localhost:11434/v1` |
| Embedding model | `nomic-embed-text` |
| API key sent | the placeholder `ollama` (Ollama ignores it) |
{% endstep %}

{% step %}
### Verify with the `health` tool

Ask your agent to run the `health` tool, or call it from code. Each dependency gets one attempt, so an unreachable endpoint shows up immediately:

```
config: LLM_PROVIDER=(unset), model=(default)
license: community
encryption: on · signing: on
db: ok
embedder: ok
llm: ok
graph: disabled
```

`embedder: FAIL (NewConnectionError)` or `llm: FAIL (…)` means Ollama isn't running or isn't reachable at that address. `FAIL (HTTP 404: …)` on either line usually means that model isn't pulled; the message names it.
{% endstep %}
{% endstepper %}

{% hint style="info" %}
`qwen2.5:3b` is small enough for a laptop. Larger local models extract and reconcile more reliably on messy input; set `OPENAI_MODEL` to any chat model you've pulled.
{% endhint %}

## Configure it explicitly

Set the variables explicitly when Ollama runs elsewhere, when you want a different model, or to make the config self-documenting:

```bash
LLM_PROVIDER=openai
OPENAI_BASE_URL=http://localhost:11434/v1
OPENAI_MODEL=qwen2.5:7b
EMBED_BASE_URL=http://localhost:11434/v1
EMBED_MODEL=nomic-embed-text
```

| Variable | Why you'd set it |
| --- | --- |
| `LLM_PROVIDER=openai` | Ollama is reached through the OpenAI-compatible provider. `LLM_PROVIDER=ollama` is rejected at startup. |
| `OPENAI_BASE_URL` | Ollama on another host or port. The default model only switches to `qwen2.5:3b` when the URL contains `localhost` or `127.0.0.1`; for any other host, set `OPENAI_MODEL` too. |
| `OPENAI_MODEL` | Any chat model you've pulled. |
| `EMBED_BASE_URL` / `EMBED_MODEL` | Embeddings from a different server or model. Without `EMBED_BASE_URL`, embeddings follow `OPENAI_BASE_URL`. |
| `OPENAI_JSON=off` | Vayl requests JSON mode for extraction. Turn it off if your model or server rejects `response_format`. |
| `OPENAI_SYSTEM_PREFIX=/no_think` | For a reasoning model that thinks before answering, if it supports that switch. |
| `LLM_TIMEOUT` | Seconds per request (default `60`). Raise it for a slow model on modest hardware. |

Every option is in [Configuration](../reference/configuration.md#model-and-embedder).

## Mix a cloud chat model with local embeddings

You can send extraction and answers to a hosted model while keeping embeddings on the machine. Point only the embedder at Ollama:

```bash
OPENAI_API_KEY=sk-…                          # chat: https://api.openai.com/v1, gpt-5-mini
EMBED_BASE_URL=http://localhost:11434/v1     # embeddings: local nomic-embed-text
EMBED_API_KEY=ollama                         # optional: otherwise OPENAI_API_KEY is sent here too
```

The chat model still receives the text you store and the facts you recall, so this reduces what leaves the machine but doesn't eliminate it. It's also how you give an Anthropic or Groq setup an embedder: those providers serve chat only, and embeddings always go to an OpenAI-compatible endpoint.

The reverse also works: `OPENAI_BASE_URL=http://localhost:11434/v1` for chat, with `EMBED_BASE_URL=https://api.openai.com/v1` and `EMBED_API_KEY=sk-…` for embeddings.

## Run Vayl in Docker against Ollama on the host

`docker-compose.yml` maps `host.docker.internal` to the Docker host, so the container can reach an Ollama running there. Set these in `.env`:

```bash
LLM_PROVIDER=openai
OPENAI_BASE_URL=http://host.docker.internal:11434/v1
OPENAI_MODEL=qwen2.5:3b
EMBED_MODEL=nomic-embed-text
```

Set `OPENAI_MODEL` and `EMBED_MODEL` explicitly: the Compose file otherwise fills in `gpt-5-mini` and `text-embedding-3-small`, and `host.docker.internal` doesn't count as local for Vayl's own defaults. Ollama also has to accept connections from the container. On Linux it listens on `127.0.0.1` by default; start it with `OLLAMA_HOST=0.0.0.0` so the Docker bridge can reach it.

Verify from inside the stack with `health`, as above.

## Change embedders later

Embeddings from different models have different sizes (768 dimensions for `nomic-embed-text`, 1536 for `text-embedding-3-small`) and can't be compared. If you switch `EMBED_MODEL` on an existing database:

* Facts already stored keep their old embeddings. Re-embedding is not automatic.
* Vayl detects the size mismatch, logs `semantic ranking unavailable (ValueError); recall uses lexical ranking`, and ranks those recalls by keyword instead of failing.
* A space at or below `VAYL_RECALL_CONTEXT` facts (40 by default) is unaffected, because recall passes every fact to the model without ranking.

Pick the embedder before you store much, and keep it for the life of the database. To move to a new embedder, start a new database.

## Next steps

<table data-view="cards"><thead><tr><th></th><th></th><th data-hidden data-card-target data-type="content-ref"></th></tr></thead><tbody><tr><td><h4><i class="fa-sliders" style="color:$primary;">:sliders:</i> Configuration</h4></td><td>Every model and embedder variable, with exact defaults.</td><td><a href="../reference/configuration.md">configuration.md</a></td></tr><tr><td><h4><i class="fa-globe" style="color:$primary;">:globe:</i> Deploying vayl-server</h4></td><td>Run Vayl for a team, in Docker or on a host.</td><td><a href="deploying-vayl-server.md">deploying-vayl-server.md</a></td></tr><tr><td><h4><i class="fa-wrench" style="color:$primary;">:wrench:</i> Troubleshooting</h4></td><td>Unreachable embedder, slow calls and other model errors.</td><td><a href="../reference/troubleshooting.md">troubleshooting.md</a></td></tr></tbody></table>
