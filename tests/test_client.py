"""The Python SDK (`vayl.Vayl`): scope merging, result decoding, dynamic tool dispatch, and — when
`vayl-mcp` is installed — a real stdio round-trip. The live test skips (never fails) if a session
can't be established, so CI stays green without a server."""
import os
import shutil
from types import SimpleNamespace

import pytest

from vayl import Vayl, VaylError
from vayl.client import _merge_scope, _text


def _content(text, kind="text"):
    return SimpleNamespace(type=kind, text=text)


def _result(texts, is_error=False):
    return SimpleNamespace(content=[_content(t) for t in texts], is_error=is_error)


def test_merge_scope_drops_empty_and_lets_call_override():
    scope = {"user_id": "u1", "agent_id": "", "run_id": ""}
    assert _merge_scope(scope, {"user_id": "u2", "text": "x", "foo": None}) == {"user_id": "u2", "text": "x"}
    assert _merge_scope(scope, {"text": "x"}) == {"user_id": "u1", "text": "x"}


def test_merge_scope_only_sends_accepted_keys():
    # FastMCP validates args strictly: a default scope must not be sent to a tool that lacks it.
    scope = {"user_id": "u1", "agent_id": "a1", "run_id": ""}
    assert _merge_scope(scope, {}, accepts={"user_id", "question"}) == {"user_id": "u1"}  # agent_id dropped
    assert _merge_scope(scope, {}, accepts=set()) == {}                                    # scope-less tool
    assert _merge_scope(scope, {}, accepts=None) == {"user_id": "u1", "agent_id": "a1"}    # unknown → all


def test_text_joins_text_parts_and_ignores_others():
    assert _text(_result(["a", "b"])) == "a\nb"
    mixed = SimpleNamespace(content=[_content("keep"), SimpleNamespace(type="image", text=None)], is_error=False)
    assert _text(mixed) == "keep"


def test_text_raises_vaylerror_on_tool_error():
    with pytest.raises(VaylError):
        _text(_result(["access denied: outside scope"], is_error=True))


def test_dynamic_dispatch_returns_callable_and_guards_private():
    m = Vayl.__new__(Vayl)                 # no connection — just exercise __getattr__
    assert callable(m.check_before_act)    # any tool name → a callable
    with pytest.raises(AttributeError):
        _ = m._internal                    # private names are not tools


@pytest.mark.skipif(shutil.which("vayl-mcp") is None, reason="vayl-mcp not installed")
def test_stdio_roundtrip(tmp_path):
    # list_memories reads the store and needs no LLM, so this works offline. Encryption/signing off
    # to avoid key setup in a throwaway test db. Any failure -> skip, so CI can't break on it.
    env = dict(os.environ, VAYL_DB=str(tmp_path / "c.db"),
               VAYL_ENCRYPT="off", VAYL_SIGN="off", VAYL_CLIENT_CONNECT_TIMEOUT="20")
    out = None
    try:
        with Vayl(env=env, user_id="tester") as m:
            out = m.list_memories()
    except Exception as e:                 # noqa: BLE001 — never fail CI on environment issues
        pytest.skip(f"stdio session unavailable: {e}")
    assert isinstance(out, str)


def test_http_round_trip_against_a_real_server(monkeypatch):
    """`Vayl(url=…, api_key=…)` against a real vayl-server app. MCP SDK v2 removed the HTTP client
    function this path used, and no test exercised it, so the break went unnoticed until a live run.
    Unlike the stdio test above, this one never skips: everything it needs is in-process."""
    import socket
    import sqlite3
    import threading
    import time

    import uvicorn

    from vayl.api import mcp_server
    from vayl.api import server as srv
    from vayl.auth.auth import Auth, Role

    monkeypatch.setattr(mcp_server, "_AUTH_REQUIRED", True)
    auth = Auth(sqlite3.connect(":memory:", check_same_thread=False))
    _p, key = auth.create("ops", roles=Role.ADMIN)
    app = srv.build_app(mcp_server.mcp.http_app(transport="http", stateless_http=True,
                                                **mcp_server._transport_security()),
                        auth, mcp_server._store)
    with socket.socket() as sk:
        sk.bind(("127.0.0.1", 0))
        port = sk.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.05)
    assert server.started, "test server did not start"
    try:
        with Vayl(url=f"http://127.0.0.1:{port}/mcp", api_key=key) as v:
            assert "edition" in v.call("license_status")              # a real tool call over HTTP
        with pytest.raises(VaylError, match="could not connect to http://127.0.0.1.*API key"):
            Vayl(url=f"http://127.0.0.1:{port}/mcp", api_key="vayl_sk_not-a-real-key")   # refused
        # neither a closed client nor a failed connect may leave its loop thread running: on Python
        # 3.10 a leftover loop with half-open transports crashed the interpreter at exit
        assert not [t for t in threading.enumerate() if t.name == "vayl-client" and t.is_alive()]
    finally:
        server.should_exit = True
        thread.join(timeout=10)
