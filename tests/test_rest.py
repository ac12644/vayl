"""The REST tool routes (`/v1/tools/...`): the same tools as MCP `tools/call`, with the same auth, scope
and capability checks, and plain JSON in and out. Offline: the model seams are stubbed."""
import os
import sqlite3
import tempfile
import uuid

import pytest
from starlette.testclient import TestClient

os.environ.setdefault("VAYL_DB", os.path.join(tempfile.mkdtemp(), "vayl.db"))
from vayl.api import mcp_server  # noqa: E402
from vayl.api import server as srv  # noqa: E402
from vayl.auth.auth import Auth, Role  # noqa: E402
from vayl.memory import llm_memory  # noqa: E402
from vayl.storage import store as store_mod  # noqa: E402


def _extract(text, active):
    if "suspended" in text:
        return [{"action": "SUPERSEDE" if active else "ADD", "subject": "account_status", "value": "suspended",
                 "scope": "global", "confidence": 0.95, "kind": "state"}]
    return [{"action": "ADD", "subject": "account_status", "value": "active", "scope": "global",
             "confidence": 0.95, "kind": "state"}]


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(mcp_server, "_AUTH_REQUIRED", True)
    monkeypatch.setattr(llm_memory, "llm_extract_classify", _extract)
    monkeypatch.setattr(llm_memory, "_qa", lambda context, question: context)
    monkeypatch.setattr("vayl.memory.llm_client._embed", lambda texts: [[0.1, 0.2] for _ in texts])
    monkeypatch.setattr(store_mod, "_embed", lambda texts: [[0.1, 0.2] for _ in texts])
    auth = Auth(sqlite3.connect(":memory:", check_same_thread=False))
    _a, admin = auth.create("ops", roles=Role.ADMIN)
    _b, agent = auth.create("bot", roles=Role.AGENT)
    mcp_app = mcp_server.mcp.http_app(transport="http", stateless_http=True, **mcp_server._transport_security())
    with TestClient(srv.build_app(mcp_app, auth, mcp_server._store)) as c:
        yield c, {"Authorization": f"Bearer {admin}"}, {"Authorization": f"Bearer {agent}"}


def test_a_tool_call_returns_json_and_reconciles(client):
    c, admin, _ = client
    uid = f"cust_{uuid.uuid4().hex[:6]}"
    r = c.post("/v1/tools/remember", json={"text": "Acme's account is active.", "user_id": uid}, headers=admin)
    assert r.status_code == 200 and r.json() == {"result": "Stored: [ADD] account_status = active"}
    r = c.post("/v1/tools/remember", json={"text": "Acme's account is suspended.", "user_id": uid}, headers=admin)
    assert r.json()["result"] == "Stored: [SUPERSEDE] account_status = suspended"
    assert "x-request-id" in r.headers
    hist = c.post("/v1/tools/history", json={"subject": "account_status", "user_id": uid}, headers=admin).json()
    assert "active  [SUPERSEDED]" in hist["result"] and "suspended  [ACTIVE]" in hist["result"]


def test_auth_is_required(client):
    c, _, _ = client
    assert c.post("/v1/tools/license_status", json={}).status_code == 401
    assert c.get("/v1/tools").status_code == 401


def test_a_missing_capability_is_a_403(client):
    c, _, agent = client
    r = c.post("/v1/tools/create_principal", json={"name": "x"}, headers=agent)
    assert r.status_code == 403 and r.json()["result"].startswith("Access denied:")


def test_bad_requests_name_the_problem(client):
    c, admin, _ = client
    assert c.post("/v1/tools/nope", json={}, headers=admin).status_code == 404
    r = c.post("/v1/tools/remember", json={"bogus": 1}, headers=admin)
    assert r.status_code == 422 and "text" in r.json()["detail"]
    assert c.post("/v1/tools/remember", content=b"[1]", headers=admin).status_code == 400
    assert c.post("/v1/tools/remember", content=b"{nope", headers=admin).status_code == 400


def test_the_tool_list_matches_mcp(client):
    c, admin, _ = client
    tools = c.get("/v1/tools", headers=admin).json()["tools"]
    names = [t["name"] for t in tools]
    assert len(names) == 32 and "remember" in names and names == sorted(names)
    remember = next(t for t in tools if t["name"] == "remember")
    assert remember["input_schema"]["required"] == ["text"]
