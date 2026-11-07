"""The Anthropic extraction call works with models that think by default and reject a prefill."""
import json

from vayl.memory import llm_client, llm_memory


def test_no_prefill_and_thinking_block_first(monkeypatch):
    sent = {}

    def fake(req, timeout=None):
        sent.update(json.loads(req.data))
        return {"content": [{"type": "thinking", "thinking": ""},
                            {"type": "text", "text": '{"facts": [{"subject": "a", "value": "b"}]}'}]}

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.setattr(llm_client, "_http_json", fake)
    assert llm_memory._call_anthropic("hi") == {"facts": [{"subject": "a", "value": "b"}]}
    assert sent["messages"][-1]["role"] == "user"


def test_cut_off_reply_raises_instead_of_salvaging(monkeypatch):
    """A reply stopped at max_tokens holds only some facts; keeping them would lose the rest silently."""
    import pytest
    cut = '{"facts": [{"subject": "a", "value": "b"}, {"subject": "c", "val'
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.setattr(llm_client, "_http_json", lambda req, timeout=None: {
        "stop_reason": "max_tokens", "content": [{"type": "text", "text": cut}]})
    with pytest.raises(llm_memory.ExtractionTruncated):
        llm_memory._call_anthropic("hi")
    assert llm_memory._LAST_REPLY["stop"] == "max_tokens"


def test_openai_length_finish_raises(monkeypatch):
    import pytest
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    monkeypatch.setattr(llm_client, "_http_json", lambda req, timeout=None: {
        "choices": [{"finish_reason": "length", "message": {"content": '{"facts": [{"subject": "a"'}}]})
    with pytest.raises(llm_memory.ExtractionTruncated):
        llm_memory._call_openai("hi")
