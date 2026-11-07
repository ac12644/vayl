"""Suite-wide guards."""
import socket

import pytest

# The unit suite promises "no LLM, no network". Ports where a real model or cloud API lives:
_MODEL_PORTS = {11434, 443, 80}


@pytest.fixture(autouse=True)
def _no_model_network(monkeypatch):
    """Refuse connections to model/cloud ports and fail the test that tried.

    Refusing makes every machine behave like CI (nothing listening), so a test can't pass locally only
    because an Ollama happens to be running. The teardown check catches the attempt even when the code
    under test swallows the error — recall's lexical fallback does exactly that, which is how 66 such
    attempts once went unnoticed. Stub the LLM/embedder instead."""
    attempts = []
    real_connect = socket.socket.connect

    def guarded(self, address):
        if isinstance(address, tuple) and len(address) >= 2 and address[1] in _MODEL_PORTS:
            attempts.append(f"{address[0]}:{address[1]}")
            raise ConnectionRefusedError(f"unit tests may not reach a model endpoint ({attempts[-1]})")
        return real_connect(self, address)

    monkeypatch.setattr(socket.socket, "connect", guarded)
    yield
    assert not attempts, f"test tried to reach a model endpoint {attempts}; stub the LLM/embedder"


@pytest.fixture(autouse=True)
def _no_verdict_call(monkeypatch):
    """The verdict pass (on by default) is a second model call; tests that want it stub llm_verdict
    and switch it back on themselves."""
    from vayl.memory import llm_memory
    monkeypatch.setattr(llm_memory, "_VERDICT", False)
