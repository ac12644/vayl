"""Vayl as a LlamaIndex memory block (and, optionally, agent tools).

    from llama_index.core.memory import Memory
    from vayl.integrations.llamaindex import VaylMemory

    vayl = VaylMemory(user_id="cust_5521")             # spawns vayl-mcp over stdio
    memory = Memory.from_defaults(session_id="s1", memory_blocks=[vayl.block()])
    agent = FunctionAgent(llm=llm, tools=[...])        # pass memory=memory to agent.run(...)

The block is LlamaIndex's long-term memory slot. Messages that leave short-term memory are written to
`remember`, which reconciles them: a changed fact replaces the old one. Before each turn the block asks
`recall` about the latest user message, so the agent's prompt carries the current facts, never a stale
value. `vayl.tools()` also gives the agent explicit `remember` / `recall` / … tools.

Needs `pip install 'vayl-mcp[llamaindex]'`.
"""
import asyncio
from typing import Any

from vayl.integrations._common import TOOL_DESCRIPTIONS, BaseVaylMemory

try:
    from llama_index.core.base.llms.types import ChatMessage, MessageRole
    from llama_index.core.memory.memory import BaseMemoryBlock
    from llama_index.core.tools import FunctionTool
    from pydantic import Field
except ImportError as e:  # pragma: no cover - exercised only without the extra
    raise ImportError("vayl.integrations.llamaindex needs LlamaIndex: pip install 'vayl-mcp[llamaindex]'") from e


class VaylMemoryBlock(BaseMemoryBlock[str]):
    """A LlamaIndex memory block backed by Vayl. Build it with `VaylMemory(...).block()`."""

    memory: Any = Field(exclude=True, description="The VaylMemory that owns the connection.")
    include_assistant: bool = Field(default=False, description="Also store the assistant's replies.")

    async def _aput(self, messages: list[ChatMessage]) -> None:
        keep = {MessageRole.USER} | ({MessageRole.ASSISTANT} if self.include_assistant else set())
        for m in messages:
            text = (m.content or "").strip() if isinstance(m.content, str) or m.content is None else str(m.content)
            if m.role in keep and text:
                await asyncio.to_thread(self.memory.remember, text)

    async def _aget(self, messages: list[ChatMessage] | None = None, **block_kwargs: Any) -> str:
        question = next((m.content for m in reversed(messages or []) if m.role == MessageRole.USER and m.content), None)
        if not question:
            return ""
        return await asyncio.to_thread(self.memory.recall, question)


class VaylMemory(BaseVaylMemory):
    """Vayl for LlamaIndex: `block()` for the Memory class, `tools()` for an agent."""

    def block(self, name="vayl", *, priority=0, include_assistant=False):
        """A memory block for `Memory.from_defaults(memory_blocks=[...])`. Priority 0 is never truncated."""
        return VaylMemoryBlock(name=name, memory=self, priority=priority, include_assistant=include_assistant,
                               description="Current facts from Vayl's reconciling memory.")

    def tools(self, include=None, exclude=None):
        """`FunctionTool`s for the curated memory surface (scope is bound here, never a tool argument)."""
        fns = {
            "remember": lambda text: self.remember(text),
            "recall": lambda question: self.recall(question),
            "history": lambda subject: self.history(subject),
            "forget": lambda text: self.forget(text),
            "list_memories": lambda: self.list_memories(),
        }
        return [FunctionTool.from_defaults(fn=fns[n], name=n, description=TOOL_DESCRIPTIONS[n])
                for n in self._selected(include, exclude)]
