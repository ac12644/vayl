"""Vayl as a framework's memory backend: Google ADK (BaseMemoryService), LlamaIndex (memory block) and
AutoGen (Memory). Offline: a fake client stands in for vayl-mcp. Each test skips when its framework
isn't installed; CI installs LlamaIndex and AutoGen but not ADK."""
import asyncio

import pytest


class FakeClient:
    def __init__(self, answer="Acme is on the Pro plan."):
        self.calls, self.answer, self.closed = [], answer, False

    def remember(self, text, **kw):
        self.calls.append(("remember", text, kw)); return "Stored: [ADD] x"

    def recall(self, question, **kw):
        self.calls.append(("recall", question, kw)); return self.answer

    def forget(self, text, **kw):
        return ""

    def call(self, tool, **kw):
        self.calls.append((tool, None, kw)); return ""

    def close(self):
        self.closed = True


def _stored(fake):
    return [(text, kw) for name, text, kw in fake.calls if name == "remember"]


# ── Google ADK ──

def test_adk_stores_user_turns_once_and_answers_from_recall():
    pytest.importorskip("google.adk")
    from google.adk.events import Event
    from google.adk.sessions import InMemorySessionService
    from google.genai import types

    from vayl.integrations.adk import VaylMemoryService

    async def run():
        svc = InMemorySessionService()
        s = await svc.create_session(app_name="support", user_id="cust_5521")
        for author, text in [("user", "Acme is on Enterprise."), ("support", "Noted."), ("user", "Acme moved to Pro.")]:
            await svc.append_event(s, Event(author=author, content=types.Content(parts=[types.Part(text=text)])))
        s = await svc.get_session(app_name="support", user_id="cust_5521", session_id=s.id)
        fake = FakeClient()
        mem = VaylMemoryService(client=fake)
        await mem.add_session_to_memory(s)
        await mem.add_session_to_memory(s)          # ADK may add a session repeatedly
        found = await mem.search_memory(app_name="support", user_id="cust_5521", query="What plan is Acme on?")
        return fake, found

    fake, found = asyncio.run(run())
    assert _stored(fake) == [("Acme is on Enterprise.", {"user_id": "cust_5521", "agent_id": "support"}),
                             ("Acme moved to Pro.", {"user_id": "cust_5521", "agent_id": "support"})]
    assert found.memories[0].content.parts[0].text == "Acme is on the Pro plan."
    assert ("recall", "What plan is Acme on?", {"user_id": "cust_5521", "agent_id": "support"}) in fake.calls


def test_adk_add_memory_writes_explicit_entries():
    pytest.importorskip("google.adk")
    from google.adk.memory.memory_entry import MemoryEntry
    from google.genai import types

    from vayl.integrations.adk import VaylMemoryService
    fake = FakeClient()
    entry = MemoryEntry(content=types.Content(parts=[types.Part(text="Prefers email.")]))
    asyncio.run(VaylMemoryService(client=fake).add_memory(app_name="a", user_id="u", memories=[entry]))
    assert _stored(fake) == [("Prefers email.", {"user_id": "u", "agent_id": "a"})]


# ── LlamaIndex ──

def test_llamaindex_block_stores_user_messages_and_recalls_the_latest_question():
    pytest.importorskip("llama_index.core")
    from llama_index.core.base.llms.types import ChatMessage

    from vayl.integrations.llamaindex import VaylMemory
    fake = FakeClient()
    block = VaylMemory(client=fake).block()
    msgs = [ChatMessage(role="user", content="Acme moved to Pro."), ChatMessage(role="assistant", content="Noted."),
            ChatMessage(role="user", content="What plan is Acme on?")]
    asyncio.run(block.aput(msgs[:2]))
    got = asyncio.run(block.aget(msgs))
    assert [t for t, _ in _stored(fake)] == ["Acme moved to Pro."]          # the assistant's reply isn't a fact
    assert got == "Acme is on the Pro plan." and ("recall", "What plan is Acme on?", {}) in fake.calls
    assert asyncio.run(block.aget([])) == ""                                # nothing to ask about


def test_llamaindex_tools_bind_scope_and_borrowed_client_stays_open():
    pytest.importorskip("llama_index.core")
    from vayl.integrations.llamaindex import VaylMemory
    fake = FakeClient()
    with VaylMemory(client=fake) as v:
        tools = {t.metadata.name: t for t in v.tools()}
        assert set(tools) == {"remember", "recall", "history", "forget", "list_memories"}
        assert "user_id" not in str(tools["remember"].metadata.fn_schema.model_json_schema())
        tools["remember"].call(text="Acme moved to Pro.")
    assert _stored(fake) == [("Acme moved to Pro.", {})] and not fake.closed


# ── AutoGen ──

def test_autogen_add_query_and_update_context():
    pytest.importorskip("autogen_core")
    from autogen_core.memory import MemoryContent, MemoryMimeType
    from autogen_core.model_context import UnboundedChatCompletionContext
    from autogen_core.models import SystemMessage, UserMessage

    from vayl.integrations.autogen import VaylMemory

    async def run():
        fake = FakeClient()
        m = VaylMemory(client=fake)
        await m.add(MemoryContent(content="Acme moved to Pro.", mime_type=MemoryMimeType.TEXT))
        q = await m.query("What plan is Acme on?")
        ctx = UnboundedChatCompletionContext()
        await ctx.add_message(UserMessage(content="What plan is Acme on?", source="user"))
        res = await m.update_context(ctx)
        return fake, q, res, await ctx.get_messages()

    fake, q, res, messages = asyncio.run(run())
    assert _stored(fake) == [("Acme moved to Pro.", {})]
    assert q.results[0].content == "Acme is on the Pro plan."
    assert isinstance(messages[-1], SystemMessage) and "Acme is on the Pro plan." in messages[-1].content
    assert res.memories.results[0].content == "Acme is on the Pro plan."


def test_autogen_clear_refuses_to_erase_everything():
    pytest.importorskip("autogen_core")
    from vayl.integrations.autogen import VaylMemory
    fake = FakeClient()
    with pytest.raises(NotImplementedError, match="delete_all"):
        asyncio.run(VaylMemory(client=fake).clear())
    assert not [c for c in fake.calls if c[0] == "delete_all"]
