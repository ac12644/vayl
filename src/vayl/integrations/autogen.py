"""Vayl as an AutoGen memory (autogen-core's `Memory` protocol).

    from autogen_agentchat.agents import AssistantAgent
    from autogen_core.memory import MemoryContent, MemoryMimeType
    from vayl.integrations.autogen import VaylMemory

    memory = VaylMemory(user_id="cust_5521")           # spawns vayl-mcp over stdio
    await memory.add(MemoryContent(content="Acme is on the Enterprise plan.", mime_type=MemoryMimeType.TEXT))
    agent = AssistantAgent("support", model_client=model_client, memory=[memory])

`add` goes to `remember`, which reconciles: a changed fact replaces the old one. Before each model call
the agent runs `update_context`, which asks `recall` about the latest user message and adds the answer
as a system message, so the model sees current facts, never a stale value. `query` is `recall`.

`clear()` raises instead of erasing: dropping every fact is `delete_all`, which is irreversible and
issues an erasure receipt, so call `memory.client.call("delete_all")` deliberately if you mean it.

Needs `pip install 'vayl-mcp[autogen]'`.
"""
import asyncio

from vayl.integrations._common import BaseVaylMemory

try:
    from autogen_core.memory import Memory, MemoryContent, MemoryMimeType, MemoryQueryResult, UpdateContextResult
    from autogen_core.models import SystemMessage, UserMessage
except ImportError as e:  # pragma: no cover - exercised only without the extra
    raise ImportError("vayl.integrations.autogen needs autogen-core: pip install 'vayl-mcp[autogen]'") from e


def _as_text(content):
    value = content.content if isinstance(content, MemoryContent) else content
    return value if isinstance(value, str) else str(value)


class VaylMemory(BaseVaylMemory, Memory):
    """An autogen-core `Memory` backed by Vayl's reconciling memory."""

    def __init__(self, client=None, **kw):
        BaseVaylMemory.__init__(self, client, **kw)

    async def add(self, content, cancellation_token=None):
        await asyncio.to_thread(self.remember, _as_text(content))

    async def query(self, query="", cancellation_token=None, **kwargs):
        answer = await asyncio.to_thread(self.recall, _as_text(query))
        return MemoryQueryResult(results=[MemoryContent(content=answer, mime_type=MemoryMimeType.TEXT)])

    async def update_context(self, model_context):
        messages = await model_context.get_messages()
        question = next((m.content for m in reversed(messages)
                         if isinstance(m, UserMessage) and isinstance(m.content, str) and m.content), None)
        if not question:
            return UpdateContextResult(memories=MemoryQueryResult(results=[]))
        result = await self.query(question)
        await model_context.add_message(SystemMessage(
            content=f"From long-term memory (current facts): {result.results[0].content}"))
        return UpdateContextResult(memories=result)

    async def clear(self):
        raise NotImplementedError("VaylMemory.clear() would erase every fact irreversibly. "
                                  "Call memory.client.call('delete_all') if that is what you want.")

    async def close(self):
        BaseVaylMemory.close(self)
