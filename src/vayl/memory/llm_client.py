"""How Vayl talks to models: the pooled HTTP transport with retry/backoff, provider selection and
configuration, generation parameters, and the embeddings call.

No prompts live here — extraction and answering (llm_memory) build their requests on top of this. Callers
reach these through the module (`llm_client._embed(...)`), so a test stubs each one in a single place.
"""
import contextlib
import contextvars
import json
import os
import random
import time
import urllib.error
import urllib.parse
import urllib.request

from vayl.config import env_float, env_int

# Connection pool for the LLM/embedding endpoint. Every call previously opened a fresh TCP+TLS
# connection — a 50-200ms handshake per request, which dominates recall latency (the embedding
# round-trip was ~95% of a 1s recall). A pooled, keep-alive sender reuses the connection. urllib3 is
# OPTIONAL: without it we fall back to urllib and lose only the pooling, so the core install keeps
# its two-dependency, minimal-audit-surface property. `pip install vayl-mcp[pooled]` turns it on.
try:
    import urllib3 as _urllib3
    _POOL = _urllib3.PoolManager(
        maxsize=env_int("VAYL_HTTP_POOL", 8),
        retries=False,                       # we do our own 429/5xx backoff below
        headers={"User-Agent": "vayl/0.1"})
except Exception:                            # not installed → transparent urllib fallback
    _urllib3 = None  # type: ignore[assignment]
    _POOL = None  # type: ignore[assignment]


def _retry_after(headers, i):
    wait = headers.get("retry-after") if headers else None
    return float(wait) if wait else min(2 ** i, 30) + random.random()


# Retry budget for _http_json. Normal traffic rides out transient outages with backoff (~2 minutes at the
# default); a diagnostic wants its verdict now, so it narrows the budget for its own calls.
_RETRIES = contextvars.ContextVar("vayl_http_retries", default=10)


@contextlib.contextmanager
def single_attempt():
    """Within this block, model/embedding calls make one attempt and fail fast instead of backing off."""
    token = _RETRIES.set(1)
    try:
        yield
    finally:
        _RETRIES.reset(token)


def _http_json(req, timeout, retries=None):
    retries = _RETRIES.get() if retries is None else retries
    for i in range(retries):
        try:
            if _POOL is not None:
                r = _POOL.request(req.get_method(), req.full_url, body=req.data,
                                  headers=dict(req.headers), timeout=timeout)
                if r.status in (429, 500, 502, 503) and i < retries - 1:
                    time.sleep(_retry_after(r.headers, i)); continue
                if r.status >= 400:
                    raise urllib.error.HTTPError(req.full_url, r.status,
                                                 r.data[:2000].decode("utf-8", "replace"),
                                                 r.headers, None)  # type: ignore[arg-type]  # _retry_after only needs .get()
                return json.loads(r.data)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503) and i < retries - 1:
                time.sleep(_retry_after(e.headers, i)); continue
            raise
        except (urllib.error.URLError, TimeoutError):  # reset / transient / slow-local-model timeout
            if i < retries - 1:
                time.sleep(min(2 ** i, 20) + random.random()); continue
            raise
        except Exception as exc:               # urllib3 transport errors (pool-specific)
            if _POOL is not None and _urllib3 is not None \
                    and isinstance(exc, _urllib3.exceptions.HTTPError) and i < retries - 1:
                time.sleep(min(2 ** i, 20) + random.random()); continue
            raise


def http_error_reason(e):
    """'HTTP 400: <provider's error message>' for an HTTPError, else ''. Only for requests that carry
    no user data (health's fixed probe): providers can echo the request back in their message."""
    if not isinstance(e, urllib.error.HTTPError):
        return ""
    body = ""
    with contextlib.suppress(Exception):   # urlopen errors carry the body; pool errors put it in .msg
        body = e.read().decode("utf-8", "replace")
    body = body or str(e.msg or "")
    try:
        err = json.loads(body).get("error")
        msg = err.get("message", "") if isinstance(err, dict) else str(err or "")
    except Exception:
        msg = ""
    return f"HTTP {e.code}: {msg[:300]}" if msg else f"HTTP {e.code}"


_PROVIDERS = ("anthropic", "openai", "groq")


def _provider():
    """Which LLM backend to use. Explicit LLM_PROVIDER wins; else infer from whichever key is
    present; else default to the OpenAI-compatible path — which, with no cloud key, points at a
    LOCAL Ollama (see _openai_config). So out of the box Vayl runs locally with NO data egress."""
    p = os.environ.get("LLM_PROVIDER", "").strip().lower()
    if p:
        # An unknown value used to fall through to Anthropic for extraction (and to the
        # OpenAI-compatible path for answers) — a typo could send data to a provider nobody chose.
        if p not in _PROVIDERS:
            raise ValueError(f"LLM_PROVIDER must be one of {', '.join(_PROVIDERS)}, got {p!r}. For Ollama "
                             "or any OpenAI-compatible endpoint, use 'openai' with OPENAI_BASE_URL.")
        return p
    if os.environ.get("ANTHROPIC_API_KEY"):
        return "anthropic"
    if os.environ.get("GROQ_API_KEY"):
        return "groq"
    return "openai"


def _openai_config():
    """Resolve (base_url, key, model, is_local) for the OpenAI-compatible path. With no
    OPENAI_BASE_URL and no OPENAI_API_KEY, defaults to local Ollama — nothing leaves the machine."""
    base = os.environ.get("OPENAI_BASE_URL")
    key = os.environ.get("OPENAI_API_KEY")
    if not base:
        base = "https://api.openai.com/v1" if key else "http://localhost:11434/v1"
    base = base.rstrip("/")
    local = ("localhost" in base) or ("127.0.0.1" in base)
    key = key or ("ollama" if local else "none")
    # Default to gpt-5-mini: 0% silently-wrong on the messy real-world reconciliation suite
    # (benchmarks/messy_eval.py), ~$0.25/$2 per 1M tok. gpt-5-nano is ~5x cheaper but flags instead
    # of superseding on messy corrections (23% silently-wrong there) — only use it for clean inputs.
    model = os.environ.get("OPENAI_MODEL", "qwen2.5:3b" if local else "gpt-5-mini")
    return base, key, model, local


def _openai_gen_params(model, default_max):
    """Chat-completions generation params, adapted per model family. OpenAI reasoning models
    (gpt-5 and later, o-series) take `max_completion_tokens` (not `max_tokens`), allow only the
    default temperature, and spend tokens on internal reasoning — the lowest effort keeps
    extraction/QA fast/cheap ('minimal' on gpt-5/o-series, 'none' from gpt-6), and the budget must
    cover reasoning + output. gpt-4o, local, and other OpenAI-compatible endpoints keep the classic
    `max_tokens` + temperature. Override via OPENAI_MAX_TOKENS / OPENAI_REASONING_EFFORT / OPENAI_TEMP."""
    major = model[4:].split(".")[0].split("-")[0] if model.startswith("gpt-") else ""
    gpt_gen = int(major) if major.isdigit() else 0
    if gpt_gen >= 5 or (model[:1] == "o" and model[1:2].isdigit()):
        lowest = "none" if gpt_gen >= 6 else "minimal"
        return {"max_completion_tokens": env_int("OPENAI_MAX_TOKENS", max(default_max, 2000)),
                "reasoning_effort": os.environ.get("OPENAI_REASONING_EFFORT", lowest)}
    return {"max_tokens": env_int("OPENAI_MAX_TOKENS", default_max),
            "temperature": env_float("OPENAI_TEMP", 0.0)}


def _embed_config():
    """Resolve (base_url, key, model) for embeddings. EMBED_BASE_URL wins, then OPENAI_BASE_URL. With
    neither, follow the chat model: OpenAI (text-embedding-3-small) when OPENAI_API_KEY is set, else a
    local Ollama embedder (nomic-embed-text). Before 0.6 a key-only setup still embedded against
    localhost:11434 — and sent the OpenAI key there — so recall stalled on retries without Ollama."""
    key = os.environ.get("EMBED_API_KEY") or os.environ.get("OPENAI_API_KEY")
    base = os.environ.get("EMBED_BASE_URL") or os.environ.get("OPENAI_BASE_URL")
    if not base:
        base = "https://api.openai.com/v1" if os.environ.get("OPENAI_API_KEY") else "http://localhost:11434/v1"
    base = base.rstrip("/")
    # nomic-embed-text stays the default everywhere it was (Ollama, vLLM, …); only OpenAI itself,
    # which has no such model, gets its own.
    model = os.environ.get("EMBED_MODEL") or (
        "text-embedding-3-small" if urllib.parse.urlsplit(base).hostname == "api.openai.com"
        else "nomic-embed-text")
    return base, key or "ollama", model


def _embed(texts):
    """Embed a batch of texts through any OpenAI-compatible /embeddings endpoint (see _embed_config)."""
    base, key, model = _embed_config()
    payload = json.dumps({"model": model, "input": list(texts)}).encode()
    req = urllib.request.Request(base + "/embeddings", data=payload,
        headers={"Authorization": f"Bearer {key}", "content-type": "application/json", "User-Agent": "vayl/0.1"})
    data = _http_json(req, timeout=env_float("LLM_TIMEOUT", 60.0))
    return [row["embedding"] for row in data["data"]]
