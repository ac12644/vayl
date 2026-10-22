"""
API surface — the HTTP server, MCP tool annotations, and config.
"""

import asyncio
import os
import sqlite3
import tempfile

import pytest  # noqa: E402
from starlette.applications import Starlette  # noqa: E402
from starlette.responses import PlainTextResponse  # noqa: E402
from starlette.routing import Route  # noqa: E402
from starlette.testclient import TestClient  # noqa: E402

os.environ.setdefault("VAYL_DB", os.path.join(tempfile.mkdtemp(), "vayl.db"))
from vayl.api import mcp_server  # noqa: E402  # noqa: E402
from vayl.api import server as srv  # noqa: E402  # noqa: E402
from vayl.auth.auth import Auth, Principal, Role  # noqa: E402  # noqa: E402
from vayl.memory.llm_client import _openai_config, _provider  # noqa: E402

# ══════════════════════════════════════════════════════════════════
# from test_server
# ══════════════════════════════════════════════════════════════════

class _StoreStub:
    def __init__(self):
        # TestClient runs the app in a worker thread → the connection must allow cross-thread use
        self.db = sqlite3.connect(":memory:", check_same_thread=False)


@pytest.fixture
def client_and_key(monkeypatch):
    # over the network auth is mandatory; if the middleware fails to bind, the app sees no principal
    monkeypatch.setattr(mcp_server, "_AUTH_REQUIRED", True)
    auth = Auth(sqlite3.connect(":memory:", check_same_thread=False))
    _p, key = auth.create("bot", roles=Role.AGENT)

    async def whoami(request):
        # a stand-in for the MCP app: report whichever principal the middleware bound
        p = mcp_server._current_principal()
        return PlainTextResponse(p.id if p else "none")

    echo = Starlette(routes=[Route("/mcp", whoami)])
    app = srv.build_app(echo, auth, _StoreStub())
    with TestClient(app) as client:      # `with` runs startup/shutdown (lifespan)
        yield client, key


def test_health_endpoints_are_unauthenticated(client_and_key):
    client, _key = client_and_key
    assert client.get("/healthz").status_code == 200
    assert client.get("/readyz").json()["status"] == "ready"


def test_metrics_endpoint_is_open_and_prometheus_formatted(client_and_key):
    client, _key = client_and_key
    r = client.get("/metrics")
    assert r.status_code == 200
    assert "text/plain" in r.headers["content-type"]
    assert "vayl_principals_active" in r.text and "vayl_license_edition_info" in r.text


def test_metrics_requires_a_token_when_configured(client_and_key, monkeypatch):
    client, _key = client_and_key
    monkeypatch.setattr(srv, "_METRICS_TOKEN", "scrape-secret")
    assert client.get("/metrics").status_code == 401                              # no token → denied
    assert client.get("/metrics", headers={"Authorization": "Bearer wrong"}).status_code == 401
    ok = client.get("/metrics", headers={"Authorization": "Bearer scrape-secret"})
    assert ok.status_code == 200 and "vayl_principals_active" in ok.text


def test_request_id_reaches_sync_code_and_is_echoed():
    # a sync endpoint runs on a worker thread, as tools do: the bound ID must follow it there
    def seen(request):
        return PlainTextResponse(mcp_server.REQUEST_ID.get())

    app = srv.RequestIdMiddleware(Starlette(routes=[Route("/x", seen)]))
    with TestClient(app) as c:
        r = c.get("/x")
        assert len(r.text) == 16 and r.headers["x-request-id"] == r.text             # generated
        r = c.get("/x", headers={"X-Request-ID": "lb-7f3a.2"})
        assert r.text == r.headers["x-request-id"] == "lb-7f3a.2"                  # proxy's ID reused
        for bad in ("a b", "x" * 65, "idé"):                                  # not plain → replaced
            r = c.get("/x", headers={"X-Request-ID": bad.encode("latin-1")})
            assert r.text != bad and len(r.text) == 16
    assert mcp_server.REQUEST_ID.get() == "-"                                      # unbound afterwards


def test_rejections_at_the_edge_still_get_a_request_id(client_and_key, monkeypatch):
    client, _key = client_and_key
    monkeypatch.setattr(srv, "_MAX_BODY", 8)
    r = client.post("/mcp", content=b"x" * 64)
    assert r.status_code == 413 and len(r.headers["x-request-id"]) == 16


def test_json_log_lines_carry_request_id_and_fields(monkeypatch):
    import json
    import logging
    monkeypatch.setenv("VAYL_LOG_FORMAT", "json")
    root = logging.getLogger()
    saved = root.handlers[:], root.level
    root.handlers = []                     # basicConfig is a no-op when handlers exist
    try:
        mcp_server.configure_logging()
        handler = root.handlers[0]
        token = mcp_server.REQUEST_ID.set("req-1")
        rec = logging.LogRecord("vayl.x", logging.INFO, __file__, 1, "GET %s", ("/mcp",), None)
        rec.fields = {"status": 200}
        assert handler.filter(rec)
        mcp_server.REQUEST_ID.reset(token)
        line = json.loads(handler.format(rec))
        assert line["request_id"] == "req-1" and line["msg"] == "GET /mcp" and line["status"] == 200
    finally:
        root.handlers, _ = saved
        root.setLevel(saved[1])
    monkeypatch.setenv("VAYL_LOG_FORMAT", "yaml")
    with pytest.raises(ValueError, match="VAYL_LOG_FORMAT"):
        mcp_server.configure_logging()


def test_render_prometheus_shape():
    snap = {"tools": {"recall": {"calls": 5, "errors": 1, "avg_ms": 12.3}},
            "actions": {"SUPERSEDE": 3}}
    out = srv.render_prometheus(snap, principals_active=4, edition="community")
    assert 'vayl_tool_calls_total{tool="recall"} 5' in out
    assert 'vayl_tool_errors_total{tool="recall"} 1' in out
    assert 'vayl_action_total{action="SUPERSEDE"} 3' in out
    assert "vayl_principals_active 4" in out
    assert 'vayl_license_edition_info{edition="community"} 1' in out


def test_missing_key_is_401(client_and_key):
    client, _key = client_and_key
    r = client.get("/mcp")
    assert r.status_code == 401 and "unauthorized" in r.text
    assert r.headers.get("www-authenticate") == "Bearer"


def test_invalid_or_malformed_key_is_401(client_and_key):
    client, _key = client_and_key
    assert client.get("/mcp", headers={"Authorization": "Bearer vayl_sk_wrong"}).status_code == 401
    assert client.get("/mcp", headers={"Authorization": "Basic abc"}).status_code == 401
    assert client.get("/mcp", headers={"Authorization": "garbage"}).status_code == 401


def test_valid_key_binds_the_principal_for_the_request(client_and_key):
    client, key = client_and_key
    r = client.get("/mcp", headers={"Authorization": f"Bearer {key}"})
    assert r.status_code == 200
    assert r.text.startswith("prin_")     # the middleware resolved AND bound it where tools read it


def test_principal_does_not_leak_between_requests(client_and_key):
    client, key = client_and_key
    client.get("/mcp", headers={"Authorization": f"Bearer {key}"})    # binds then resets
    # a subsequent unauthenticated request is still rejected (no stale principal)
    assert client.get("/mcp").status_code == 401


def test_resolve_bearer_parsing():
    auth = Auth(sqlite3.connect(":memory:"))
    p, key = auth.create("x", roles=Role.MEMBER)
    assert srv.resolve_bearer(f"Bearer {key}", auth).id == p.id
    assert srv.resolve_bearer(f"bearer {key}", auth).id == p.id      # scheme is case-insensitive
    assert srv.resolve_bearer("Bearer nope", auth) is None
    assert srv.resolve_bearer("", auth) is None
    assert srv.resolve_bearer(f"Token {key}", auth) is None          # wrong scheme


def test_resolve_bearer_routes_jwt_to_sso_and_keys_to_auth():
    auth = Auth(sqlite3.connect(":memory:"))
    _p, key = auth.create("bot", roles=Role.AGENT)

    class _FakeSSO:
        def verify(self, token):
            return Principal("sso:u", "u", [Role.VIEWER], "human") if token == "aa.bb.cc" else None

    # a JWT goes to the SSO verifier...
    assert srv.resolve_bearer("Bearer aa.bb.cc", auth, _FakeSSO()).id == "sso:u"
    # ...an API key goes to the auth store (never to SSO)...
    assert srv.resolve_bearer(f"Bearer {key}", auth, _FakeSSO()).id.startswith("prin_")
    # ...and a JWT with no SSO verifier configured is rejected
    assert srv.resolve_bearer("Bearer aa.bb.cc", auth, None) is None


# ══════════════════════════════════════════════════════════════════
# from test_mcp_annotations
# ══════════════════════════════════════════════════════════════════

DESTRUCTIVE = {"delete", "delete_all", "purge_expired", "revoke_principal"}   # irreversible


READ_ONLY = {"recall", "recall_related", "history", "get_memory", "list_memories",
             "export_memory", "audit_log", "stats", "health", "explain_decision",
             "check_before_act", "safe_recall", "verify_receipt", "verify_audit", "export_public_key",
             "get_reconcile_policy", "list_principals", "license_status",
             "pending_changes"}


WRITES = {"remember", "forget", "update_memory", "record_decision", "attest",
          "set_reconcile_policy", "create_principal",
          # Resolving a gated change edits memory but destroys nothing: confirming supersedes the
          # old value (kept as history) and rejecting keeps the proposal itself as history.
          "confirm_change", "reject_change"}   # change memory/log/config, keep history


def tools():
    return {t.name: t.annotations for t in asyncio.run(mcp_server.mcp.list_tools())}


def test_every_tool_is_annotated():
    t = tools()
    assert set(t) == DESTRUCTIVE | READ_ONLY | WRITES
    assert all(a is not None for a in t.values())
    assert all(a.title for a in t.values())          # human-readable title for the client UI


def test_irreversible_tools_are_marked_destructive():
    t = tools()
    for name in DESTRUCTIVE:
        a = t[name]
        assert a.destructive_hint is True, f"{name} must warn clients before erasing"
        assert a.read_only_hint is False, name


def test_reads_are_marked_read_only():
    t = tools()
    for name in READ_ONLY:
        assert t[name].read_only_hint is True, f"{name} should be safe for a client to auto-run"


def test_writes_change_memory_but_are_not_destructive():
    # supersede/retract keep history — nothing is lost, so clients needn't hard-warn
    t = tools()
    for name in WRITES:
        a = t[name]
        assert a.read_only_hint is False and a.destructive_hint is False, name


def test_llm_backed_tools_declare_open_world():
    t = tools()
    for name in ("remember", "recall", "health"):
        assert t[name].open_world_hint is True, f"{name} calls an external LLM/embedder"


# ══════════════════════════════════════════════════════════════════
# from test_config
# ══════════════════════════════════════════════════════════════════

_ENV = ["LLM_PROVIDER", "ANTHROPIC_API_KEY", "GROQ_API_KEY", "OPENAI_API_KEY",
        "OPENAI_BASE_URL", "OPENAI_MODEL"]


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for k in _ENV:
        monkeypatch.delenv(k, raising=False)


def test_out_of_the_box_is_local_ollama_no_egress():
    assert _provider() == "openai"
    base, key, model, local = _openai_config()
    assert local and "11434" in base and key == "ollama" and model == "qwen2.5:3b"


def test_openai_key_switches_to_cloud(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-x")
    base, key, model, local = _openai_config()
    assert base == "https://api.openai.com/v1" and not local
    assert key == "sk-x" and model == "gpt-5-mini"


def test_provider_inferred_from_present_key(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    assert _provider() == "anthropic"
    monkeypatch.setenv("GROQ_API_KEY", "y")
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    assert _provider() == "groq"


def test_explicit_provider_wins(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "groq")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    assert _provider() == "groq"


def test_custom_base_url_detects_local(monkeypatch):
    monkeypatch.setenv("OPENAI_BASE_URL", "http://localhost:11434/v1")
    base, key, model, local = _openai_config()
    assert local and key == "ollama" and model == "qwen2.5:3b"


def test_delete_issues_no_receipt_when_the_graph_purge_fails(monkeypatch):
    """A signed erasure receipt must never attest to an erasure that left data behind."""
    from vayl.memory.llm_memory import LLMMemory
    from vayl.storage import store as store_mod
    monkeypatch.setattr(store_mod, "_embed", lambda texts: [[0.0] for _ in texts])
    st = mcp_server._store
    m = LLMMemory()
    m._apply({"action": "ADD", "subject": "state", "value": "Redux", "scope": "global", "confidence": 0.9},
             "we use Redux")
    st.save("erasure_u", m)

    class _GraphDown:
        def delete_edges(self, **where):
            raise ConnectionError("neo4j unreachable")

    receipts = lambda: st.db.execute("SELECT COUNT(*) FROM receipts").fetchone()[0]  # noqa: E731
    before = receipts()
    monkeypatch.setattr(st, "graph", _GraphDown())
    out = mcp_server.delete(subject="state", user_id="erasure_u")

    assert "couldn't complete" in out and "receipt" not in out
    assert receipts() == before                                    # nothing signed
    monkeypatch.setattr(st, "graph", None)
    assert [s.value for s in st.load("erasure_u").active()] == ["Redux"]   # nothing erased


def test_graph_without_a_password_fails_at_startup(monkeypatch):
    """A missing NEO4J_PASSWORD is misconfiguration: refuse, don't fall back to a well-known default."""
    monkeypatch.setenv("VAYL_GRAPH", "1")
    monkeypatch.delenv("NEO4J_PASSWORD", raising=False)
    with pytest.raises(RuntimeError, match="NEO4J_PASSWORD"):
        mcp_server._maybe_graph()


def test_graph_disabled_needs_no_password(monkeypatch):
    monkeypatch.delenv("VAYL_GRAPH", raising=False)
    monkeypatch.delenv("NEO4J_PASSWORD", raising=False)
    assert mcp_server._maybe_graph() is None


def test_neo4j_graph_refuses_an_empty_password():
    from vayl.storage.graph_store import Neo4jGraph
    with pytest.raises(ValueError, match="password"):
        Neo4jGraph(pw="")


def test_real_mcp_tool_call_over_http_returns_its_result(monkeypatch, tmp_path):
    """End to end through every middleware: a streamable-HTTP tools/call must come back with its
    result. LimitsMiddleware once answered every receive() after the body with http.disconnect,
    which made the SSE response cancel itself — every call returned 200 with an empty body."""
    monkeypatch.setattr(mcp_server, "_AUTH_REQUIRED", True)
    auth = Auth(sqlite3.connect(":memory:", check_same_thread=False))
    _p, key = auth.create("ops", roles=Role.ADMIN)
    mcp_app = mcp_server.mcp.http_app(transport="http", stateless_http=True)
    app = srv.build_app(mcp_app, auth, _StoreStub())
    headers = {"Authorization": f"Bearer {key}", "Accept": "application/json, text/event-stream"}
    call = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "export_public_key", "arguments": {}}}
    with TestClient(app) as c:
        r = c.post("/mcp", json=call, headers=headers)
    assert r.status_code == 200
    assert '"result"' in r.text and '"isError":true' not in r.text.replace(" ", ""), r.text


def test_openapi_spec_matches_the_server_routes():
    """openapi/vayl-server.yaml renders the API Reference; it must list exactly the routes served,
    with one /v1/tools/<name> operation per registered tool."""
    yaml = pytest.importorskip("yaml")
    import pathlib

    import vayl
    spec = yaml.safe_load((pathlib.Path(__file__).parents[1] / "openapi" / "vayl-server.yaml").read_text())
    app = srv.build_app(Starlette(routes=[]), Auth(sqlite3.connect(":memory:")), _StoreStub())
    routes = {getattr(r, "path", "") for r in app.app.app.routes}          # through both middlewares
    served = {"/mcp" if p == "" else p for p in routes}                     # the MCP app is mounted at /
    tools = {t.name for t in asyncio.run(mcp_server.mcp.list_tools())}
    rest = {"/v1/tools"} | {f"/v1/tools/{t}" for t in tools}
    assert "/v1" in served
    assert set(spec["paths"]) == (served - {"/v1"}) | rest
    assert spec["info"]["version"] == vayl.__version__


def test_the_generated_spec_and_tool_pages_are_current():
    """The spec and docs/api-reference/tools/*.md are generated from the tool registry. A tool change
    without rerunning `python openapi/generate.py` fails here instead of shipping stale docs."""
    pytest.importorskip("yaml")
    import pathlib
    import subprocess
    import sys
    root = pathlib.Path(__file__).parents[1]
    r = subprocess.run([sys.executable, str(root / "openapi" / "generate.py"), "--check"],
                       capture_output=True, text=True, cwd=root)
    assert r.returncode == 0, r.stderr
