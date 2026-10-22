"""Vayl as a Google ADK memory service.

    from google.adk.runners import Runner
    from vayl.integrations.adk import VaylMemoryService

    memory = VaylMemoryService()                      # spawns vayl-mcp over stdio
    runner = Runner(agent=agent, app_name="support", session_service=sessions,
                    memory_service=memory)
    ...
    await memory.add_session_to_memory(session)       # after a conversation

`add_session_to_memory` / `add_events_to_memory` / `add_memory` send what the USER said to `remember`,
where it is reconciled: a new value supersedes the old one instead of sitting next to it. The agent's
own replies are skipped by default (they're the model's words, not facts the user asserted).
`search_memory` answers from `recall`, so ADK's `load_memory` tool gets the current value, never a
stale one. ADK's `user_id` is Vayl's `user_id` and its `app_name` is Vayl's `agent_id`.

Needs `pip install 'vayl-mcp[adk]'`.
"""
import asyncio

from vayl.integrations._common import BaseVaylMemory

try:
    from google.adk.memory.base_memory_service import BaseMemoryService, SearchMemoryResponse
    from google.adk.memory.memory_entry import MemoryEntry
    from google.genai import types
except ImportError as e:  # pragma: no cover - exercised only without the extra
    raise ImportError("vayl.integrations.adk needs Google ADK: pip install 'vayl-mcp[adk]'") from e


def _text(content):
    """The plain text of a google.genai Content (or None)."""
    parts = getattr(content, "parts", None) or []
    text = "\n".join(p.text for p in parts if getattr(p, "text", None))
    return text.strip() or None


class VaylMemoryService(BaseVaylMemory, BaseMemoryService):
    """An ADK `BaseMemoryService` backed by Vayl's reconciling memory.

    Takes the same connection arguments as `vayl.Vayl` (`url`, `api_key`, `command`, `env`, …) or an
    existing `client=`. Set `include_agent=True` to also store the agent's replies.
    """

    def __init__(self, client=None, *, include_agent=False, **kw):
        BaseVaylMemory.__init__(self, client, **kw)
        self.include_agent = include_agent
        self._seen = set()   # event ids already stored: ADK may add the same session repeatedly

    async def _store(self, app_name, user_id, texts):
        for text in texts:
            await asyncio.to_thread(self.client.remember, text, user_id=user_id, agent_id=app_name)

    def _event_texts(self, events):
        texts = []
        for ev in events:
            eid = getattr(ev, "id", None)
            if eid and eid in self._seen:
                continue
            if getattr(ev, "author", None) != "user" and not self.include_agent:
                continue
            text = _text(getattr(ev, "content", None))
            if text:
                texts.append(text)
            if eid:
                self._seen.add(eid)
        return texts

    async def add_session_to_memory(self, session):
        await self._store(session.app_name, session.user_id, self._event_texts(session.events))

    async def add_events_to_memory(self, *, app_name, user_id, events, session_id=None, custom_metadata=None):
        await self._store(app_name, user_id, self._event_texts(events))

    async def add_memory(self, *, app_name, user_id, memories, custom_metadata=None):
        await self._store(app_name, user_id, [t for t in (_text(m.content) for m in memories) if t])

    async def search_memory(self, *, app_name, user_id, query):
        answer = await asyncio.to_thread(self.client.recall, query, user_id=user_id, agent_id=app_name)
        entry = MemoryEntry(content=types.Content(role="user", parts=[types.Part(text=answer)]),
                            author="vayl")
        return SearchMemoryResponse(memories=[entry])
