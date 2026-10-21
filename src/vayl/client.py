"""A small **synchronous** Python client for Vayl — call the tools as methods instead of
hand-writing MCP `tools/call` JSON.

    from vayl import Vayl

    # local: spawns `vayl-mcp` over stdio and talks to it
    with Vayl(user_id="proj_7") as m:
        m.remember("We moved off Redux to Zustand")
        print(m.recall("what do we use for state?"))     # -> "Zustand"

    # a shared team server (authenticated streamable-HTTP):
    with Vayl(url="https://memory.acme.com/mcp", api_key="vayl_sk_…", user_id="cust_5521") as m:
        print(m.recall("what plan are they on?"))

`remember` / `recall` / `forget` have named wrappers; **any** other tool the server exposes is
callable as a method too — `m.check_before_act(subject=...)`, `m.history(subject=...)`,
`m.list_memories()`. Every method returns the tool's text result and raises `VaylError` on a tool
error. A default `user_id` / `agent_id` / `run_id` set on the client is sent on every call and can
be overridden per call.

The MCP session is async; this wraps it on a background event loop so your code stays synchronous.
Use it as a context manager, or call `.close()` when done.
"""
import asyncio
import concurrent.futures
import os
import threading

from vayl.config import env_float

_CONNECT_TIMEOUT = env_float("VAYL_CLIENT_CONNECT_TIMEOUT", 30.0)
_CALL_TIMEOUT = env_float("VAYL_CLIENT_CALL_TIMEOUT", 120.0)


class VaylError(RuntimeError):
    """A Vayl tool returned an error (e.g. access denied, or a failed operation)."""


def _merge_scope(scope, args, accepts=None):
    """Client-default scope + per-call args; per-call wins, empty/None values are dropped.

    When `accepts` (the set of parameter names a tool declares) is given, scope keys the tool does
    not accept are dropped — so a default `user_id` is never sent to a scope-less tool like
    `health()`. FastMCP validates arguments strictly and rejects unknown ones, so this keeps a
    client-wide default scope from breaking calls to tools that don't take it."""
    out = {k: v for k, v in scope.items() if v and (accepts is None or k in accepts)}
    out.update({k: v for k, v in args.items() if v is not None})
    return out


def _text(result):
    """Join the text parts of an MCP CallToolResult; raise VaylError if the tool flagged an error."""
    parts = [c.text for c in (result.content or []) if getattr(c, "type", "") == "text"]
    body = "\n".join(parts)
    # `is_error` directly, not getattr(..., False): if the field were ever renamed again, a default
    # would turn every denied or failed call into a silent success.
    if result.is_error:
        raise VaylError(body or "tool returned an error")
    return body


class Vayl:
    def __init__(self, url=None, api_key=None, command="vayl-mcp", args=None, env=None,
                 user_id="", agent_id="", run_id=""):
        self._url = url
        self._api_key = api_key
        self._command = command
        self._args = args or []
        self._env = env
        self._scope = {"user_id": user_id, "agent_id": agent_id, "run_id": run_id}

        self._q: asyncio.Queue | None = None             # created on the loop
        self._tool_accepts = {}                           # tool name -> set of accepted param names
        self._connected: concurrent.futures.Future[bool] = concurrent.futures.Future()  # session live
        self._served: concurrent.futures.Future[None] = concurrent.futures.Future()     # _serve ended
        self._closed = False
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, name="vayl-client", daemon=True)
        self._thread.start()
        # Run the whole session lifecycle inside ONE task so anyio cancel scopes enter/exit in the
        # same task (a per-call open/close would violate that and error).
        self._loop.call_soon_threadsafe(lambda: self._loop.create_task(self._serve()))
        try:
            self._connected.result(timeout=_CONNECT_TIMEOUT)  # raises if connect failed
        except BaseException:
            self.close()       # don't leave the loop thread and half-open transports behind
            raise

    # ── the async session, owned entirely by the background loop ──
    async def _serve(self):

        from mcp import ClientSession

        self._q = asyncio.Queue()
        try:
            if self._url:
                import httpx2
                from mcp.client.streamable_http import streamable_http_client
                # MCP SDK v2 takes a configured HTTP client instead of a headers= argument.
                headers = {"Authorization": f"Bearer {self._api_key}"} if self._api_key else {}
                async with httpx2.AsyncClient(headers=headers) as http, \
                        streamable_http_client(self._url, http_client=http) as (read, write), \
                        ClientSession(read, write) as session:
                    await session.initialize()
                    await self._loop_serve(session)
            else:
                from mcp import StdioServerParameters
                from mcp.client.stdio import stdio_client
                params = StdioServerParameters(command=self._command, args=self._args,
                                               env=self._env if self._env is not None else dict(os.environ))
                async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
                    await session.initialize()
                    await self._loop_serve(session)
        except Exception as e:                            # connect failed → surface to the constructor
            if not self._connected.done():
                self._connected.set_exception(self._connect_error(e))
        finally:
            if not self._served.done():
                self._served.set_result(None)

    def _connect_error(self, e):
        """One readable error for a failed connection. The transports raise nested exception groups
        ("unhandled errors in a TaskGroup"), so dig out the single root cause and name the target."""
        root = e
        while getattr(root, "exceptions", None) and len(root.exceptions) == 1:
            root = root.exceptions[0]
        target = self._url or f"`{self._command}` over stdio"
        hint = " Check the URL and that the API key is valid and active." if self._url else ""
        err = VaylError(f"could not connect to {target}: {type(root).__name__}: {root}.{hint}")
        err.__cause__ = root
        return err

    async def _loop_serve(self, session):
        # Learn each tool's parameters so `call()` only sends scope keys a tool actually accepts.
        try:
            listed = await session.list_tools()
            self._tool_accepts = {t.name: set((t.input_schema or {}).get("properties", {}) or {})
                                  for t in listed.tools}
        except Exception:
            self._tool_accepts = {}   # best-effort: fall back to sending the full scope
        if not self._connected.done():
            self._connected.set_result(True)
        assert self._q is not None   # _serve creates it before handing over
        while True:
            item = await self._q.get()
            if item is None:                              # close() signal
                return
            tool, call_args, fut = item
            try:
                res = await session.call_tool(tool, call_args)
                self._loop.call_soon_threadsafe(fut.set_result, _text(res))
            except Exception as e:
                self._loop.call_soon_threadsafe(fut.set_exception, e)

    # ── the synchronous surface ──
    def call(self, tool, **args):
        """Call any tool by name; returns its text result. Prefer the named methods where they exist."""
        if self._closed:
            raise VaylError("client is closed")
        fut: concurrent.futures.Future[str] = concurrent.futures.Future()
        assert self._q is not None   # set before the constructor returns
        scoped = _merge_scope(self._scope, args, self._tool_accepts.get(tool))
        self._loop.call_soon_threadsafe(self._q.put_nowait, (tool, scoped, fut))
        return fut.result(timeout=_CALL_TIMEOUT)

    def remember(self, text, **kw):
        """Store fact(s) from a natural-language statement (extracted and reconciled)."""
        return self.call("remember", text=text, **kw)

    def recall(self, question, **kw):
        """Answer a question from the active memory, or 'I don't know'."""
        return self.call("recall", question=question, **kw)

    def forget(self, text, **kw):
        """Retract a fact — retired from the active set but kept in history."""
        return self.call("forget", text=text, **kw)

    def __getattr__(self, name):
        # Any other tool (history, list_memories, check_before_act, …) dispatches generically.
        if name.startswith("_"):
            raise AttributeError(name)
        def method(**kw):
            return self.call(name, **kw)
        method.__name__ = name
        return method

    def close(self):
        if self._closed:
            return
        self._closed = True
        try:
            if self._q is not None:
                self._loop.call_soon_threadsafe(self._q.put_nowait, None)  # let _serve unwind in-task
            # Wait for the session and transports to close inside their own task before stopping the
            # loop. Stopping first left them half-closed, and Python 3.10 could crash at exit.
            self._served.result(timeout=_CONNECT_TIMEOUT)
        except Exception:                                   # noqa: BLE001 — best-effort shutdown
            pass
        finally:
            self._loop.call_soon_threadsafe(self._loop.stop)
            self._thread.join(timeout=_CONNECT_TIMEOUT)
            if not self._thread.is_alive():
                self._loop.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
