"""
Pooled HTTP transport.

Every LLM/embedding call opened a fresh TCP+TLS connection — a 50-200ms handshake per request that
dominated recall latency. `_http_json` now sends through a keep-alive urllib3 pool when available and
falls back to urllib when it is not, so the core install keeps its minimal dependency surface. These
tests pin: the pooled path parses a response, the retry/backoff logic still fires on 429/5xx in the
pooled path, a 4xx surfaces as an error, and the urllib fallback still works when the pool is absent.
"""
import urllib.error
from unittest import mock

import pytest

from vayl.memory import llm_client as L


def _req():
    import urllib.request
    return urllib.request.Request("https://api.example/v1/embeddings", data=b'{"input":"x"}',
                                  headers={"Authorization": "Bearer k", "content-type": "application/json"})


class _Resp:
    def __init__(self, status, data=b'{"ok":1}', headers=None):
        self.status = status
        self.data = data
        self.headers = headers or {}


def test_pooled_path_parses_a_response():
    pool = mock.Mock()
    pool.request.return_value = _Resp(200, b'{"data":[{"embedding":[0.1]}]}')
    with mock.patch.object(L, "_POOL", pool):
        out = L._http_json(_req(), timeout=5)
    assert out["data"][0]["embedding"] == [0.1]
    assert pool.request.call_count == 1               # one call, reusing the pool


def test_pooled_path_reuses_one_pool_across_calls():
    pool = mock.Mock()
    pool.request.return_value = _Resp(200)
    with mock.patch.object(L, "_POOL", pool):
        L._http_json(_req(), timeout=5)
        L._http_json(_req(), timeout=5)
        L._http_json(_req(), timeout=5)
    assert pool.request.call_count == 3               # same PoolManager, three sends


def test_pooled_path_retries_on_429_then_succeeds():
    pool = mock.Mock()
    pool.request.side_effect = [_Resp(429, headers={"retry-after": "0"}), _Resp(200, b'{"ok":1}')]
    with mock.patch.object(L, "_POOL", pool), mock.patch.object(L.time, "sleep"):
        out = L._http_json(_req(), timeout=5, retries=3)
    assert out == {"ok": 1} and pool.request.call_count == 2


def test_pooled_path_raises_on_4xx():
    pool = mock.Mock()
    pool.request.return_value = _Resp(401, b'{"error":"bad key"}')
    with mock.patch.object(L, "_POOL", pool):
        with pytest.raises(urllib.error.HTTPError) as err:
            L._http_json(_req(), timeout=5)
        assert err.value.code == 401


def test_urllib_fallback_used_when_no_pool():
    """With urllib3 absent (_POOL is None), the transparent urllib path still works."""
    fake = mock.MagicMock()
    fake.read.return_value = b'{"ok":2}'
    fake.__enter__.return_value = fake
    with mock.patch.object(L, "_POOL", None), \
         mock.patch.object(L.urllib.request, "urlopen", return_value=fake) as urlopen:
        out = L._http_json(_req(), timeout=5)
    assert out == {"ok": 2} and urlopen.call_count == 1


def test_pool_is_a_module_singleton_not_per_call():
    """A fresh PoolManager per call would defeat the point — it must be created once at import."""
    import inspect
    src = inspect.getsource(L)
    # PoolManager is constructed exactly once, at module scope
    assert src.count("PoolManager(") == 1


def test_single_attempt_fails_fast_and_restores_the_default():
    boom = urllib.error.URLError("connection refused")
    with mock.patch.object(L, "_POOL", None), \
         mock.patch.object(L.urllib.request, "urlopen", side_effect=boom) as urlopen, \
         mock.patch.object(L.time, "sleep") as sleep:
        with L.single_attempt():
            with pytest.raises(urllib.error.URLError):
                L._http_json(_req(), timeout=5)
        assert urlopen.call_count == 1 and sleep.call_count == 0
        assert L._RETRIES.get() == 10                        # normal traffic keeps its retry budget


def test_health_reports_an_unreachable_endpoint_without_backoff(monkeypatch):
    """health() is what you run when the model is down; it must not sit through minutes of retries."""
    import os
    import tempfile
    os.environ.setdefault("VAYL_DB", os.path.join(tempfile.mkdtemp(), "vayl.db"))
    from vayl.api import mcp_server
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "x")
    boom = urllib.error.URLError("connection refused")
    with mock.patch.object(L, "_POOL", None), \
         mock.patch.object(L.urllib.request, "urlopen", side_effect=boom) as urlopen, \
         mock.patch.object(L.time, "sleep") as sleep:
        report = mcp_server.health()
    assert "embedder: FAIL (URLError)" in report and "llm: FAIL (URLError)" in report
    assert urlopen.call_count == 2 and sleep.call_count == 0   # one attempt each, no backoff


def test_an_exhausted_quota_fails_at_once_instead_of_retrying(monkeypatch):
    """OpenAI answers 'no credits remaining' with HTTP 429, the code it uses for rate limits. Retrying
    it with backoff made each call hang for minutes; it is permanent, so it fails on the first try."""
    import urllib.error
    import urllib.request

    from vayl.memory import llm_client
    calls = []
    body = b'{"error": {"type": "insufficient_quota", "code": "credit_balance_exhausted", "message": "You have no credits remaining."}}'

    class _Resp:
        status, data, headers = 429, body, {}

    class _Pool:
        def request(self, *a, **k):
            calls.append(1); return _Resp()

    monkeypatch.setattr(llm_client, "_POOL", _Pool())
    monkeypatch.setattr(llm_client.time, "sleep", lambda s: (_ for _ in ()).throw(AssertionError("retried")))
    req = urllib.request.Request("https://api.openai.com/v1/chat/completions", data=b"{}")
    try:
        llm_client._http_json(req, 5)
    except urllib.error.HTTPError as e:
        assert e.code == 429 and "no credits remaining" in llm_client.http_error_reason(e)
    assert calls == [1]
