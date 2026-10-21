#!/usr/bin/env python3
"""
Vayl MCP server — reconciling memory for AI agents, over the Model Context Protocol.
Any MCP client (Claude Desktop, Cursor, Claude Code, …) can plug in for memory that
answers what's TRUE NOW, retracts correctly, and remembers history.

Install & run:
    pip install .                     # or:  uvx vayl-mcp   (once published)
    OPENAI_API_KEY=... LLM_PROVIDER=openai VAYL_DB=~/vayl.db vayl-mcp

Client config (Claude Desktop / Cursor -> mcpServers):
  "vayl": {
    "command": "vayl-mcp",
    "env": {"OPENAI_API_KEY": "sk-...", "LLM_PROVIDER": "openai", "VAYL_DB": "/abs/path/vayl.db"}
  }
"""
import contextvars
import json
import logging
import os
import secrets
import sys
import time
import traceback
import urllib.error

from fastmcp import FastMCP
from mcp.types import ToolAnnotations

from vayl import __version__
from vayl.auth import auth
from vayl.auth.auth import Auth
from vayl.auth.auth import Capability as C
from vayl.config import env_bool
from vayl.licensing import license as license_mod
from vayl.licensing import receipts as receipts_mod
from vayl.licensing.receipts import Receipts
from vayl.memory import llm_memory
from vayl.memory.decisions import Decisions
from vayl.security import crypto
from vayl.security.audit import Audit
from vayl.storage.store import Store, bind_tenant, reset_tenant
from vayl.telemetry.metrics import Metrics

log = logging.getLogger(__name__)

# ── tool safety annotations ───────────────────────────────────────────────────
# Tell MCP clients which tools are safe to auto-run vs. which need confirmation.
#   read-only    : never changes what memory holds (audit/metrics bookkeeping aside)
#   write        : changes memory, but nothing is lost — supersede/retract keep history
#   destructive  : IRREVERSIBLE hard delete
# openWorld = talks to an external service (the LLM/embedder) rather than just local state.

def _ro(title, open_world=False):
    return ToolAnnotations(title=title, read_only_hint=True, open_world_hint=open_world)


def _write(title, open_world=True):
    return ToolAnnotations(title=title, read_only_hint=False, destructive_hint=False,
                           idempotent_hint=False, open_world_hint=open_world)


def _destructive(title):
    return ToolAnnotations(title=title, read_only_hint=False, destructive_hint=True,
                           idempotent_hint=True, open_world_hint=False)


def _maybe_graph():
    """Attach the optional Neo4j graph projection when configured (VAYL_GRAPH=1).
    Fully graceful: if the driver or DB is unavailable, we run slot-only rather than crash."""
    if not env_bool("VAYL_GRAPH", False):
        return None
    # A missing password is misconfiguration, not an outage: fail at startup instead of silently
    # connecting with a well-known default.
    if not os.environ.get("NEO4J_PASSWORD"):
        raise RuntimeError("VAYL_GRAPH is set but NEO4J_PASSWORD is not. Set the Neo4j password, "
                           "or unset VAYL_GRAPH to run without the graph.")
    try:
        from vayl.storage.graph_store import Neo4jGraph
        return Neo4jGraph(
            uri=os.environ.get("NEO4J_URI", "bolt://localhost:7687"),
            user=os.environ.get("NEO4J_USER", "neo4j"),
            pw=os.environ["NEO4J_PASSWORD"],
        )
    except Exception:
        # slot-only fallback — the graph is a bonus, never a hard dependency. But the operator asked
        # for it, so say why they aren't getting it.
        log.warning("VAYL_GRAPH is set but the Neo4j graph is unavailable; running slot-only",
                    exc_info=True)
        return None


def _transport_security():
    """DNS-rebinding protection for the HTTP transport, set EXPLICITLY (never left to an SDK default
    that silently turns off when the bind host isn't localhost). Protection is always ON; the allowed
    Host/Origin values default to localhost and are widened via env for a real deployment:
        VAYL_ALLOWED_HOSTS    CSV of Host values the server answers to (e.g. memory.acme.com,127.0.0.1:8080)
        VAYL_ALLOWED_ORIGINS  CSV of allowed Origin headers (browser clients); optional
    A deployment bound to 0.0.0.0 behind a proxy MUST set VAYL_ALLOWED_HOSTS to its public host —
    otherwise legitimate traffic is rejected (a safe, visible failure), not silently unprotected.

    Returns kwargs for FastMCP's `http_app()`: with the standalone framework, transport security and
    statelessness live on the HTTP app, not the constructor (stdio has no HTTP, so it doesn't apply)."""
    default_hosts = ["127.0.0.1:*", "localhost:*", "[::1]:*"]
    hosts = [h.strip() for h in os.environ.get("VAYL_ALLOWED_HOSTS", "").split(",") if h.strip()]
    origins = [o.strip() for o in os.environ.get("VAYL_ALLOWED_ORIGINS", "").split(",") if o.strip()]
    return {
        "host_origin_protection": True,   # DNS-rebinding protection, always on
        "allowed_hosts": hosts or default_hosts,
        "allowed_origins": origins or ["http://localhost:*", "http://127.0.0.1:*"],
    }


# Sent to the client at initialize; Claude Code and others show it to the model. Without it a model
# told "remember X for this project" wrote X to a notes file and never called Vayl.
_INSTRUCTIONS = (
    "Vayl is persistent, reconciling memory. When the user asks you to remember, note or keep track "
    "of something, or states a fact or a change to one (a decision, a preference, a dose, an "
    "account setting, 'we switched to X'), call `remember` with one plain sentence: Vayl retires the "
    "old value instead of keeping both, which a notes file cannot do. Before answering from what you "
    "were told earlier (a project's stack, a customer's plan, a patient's current medication), call "
    "`recall`, with include_history=true for 'what was it before?'. Use `forget` when something is "
    "no longer true and has no replacement. If recall doesn't know, say so instead of guessing."
)
mcp = FastMCP("vayl", version=__version__, instructions=_INSTRUCTIONS)   # serverInfo.version; FastMCP's own otherwise
_db_path = os.path.expanduser(os.environ.get("VAYL_DB", "vayl.db"))
_store = Store(_db_path, graph=_maybe_graph())
_metrics = Metrics(_store.db, _store.crypter)
_signer = crypto.resolve_signer(_db_path)
# accountability log: encrypted at rest, hash-chained + signed (tamper-evident)
_audit = Audit(_store.db, _store.crypter, _signer)
# Feature 1: signed, immutable snapshots of the beliefs behind each action
_decisions = Decisions(_store.db, _store.crypter, _signer)
# Feature 3: signed, third-party-verifiable erasure receipts & attestations
_receipts = Receipts(_store.db, _store.crypter, _signer)
# M1: API-key auth; roles grant capabilities (names encrypted at rest — team-member personal data)
_auth = Auth(_store.db, _store.crypter)
# M3: signed offline license/seat entitlements; missing/invalid → Community
_license = license_mod.load()

# The authenticated caller for the current request. The remote HTTP server (M2) sets this per request
# from the Bearer key; under local stdio it's unset → we fall back to a trusted local admin, UNLESS
# VAYL_AUTH_REQUIRED is on (the remote server sets it), in which case an unset principal is denied.
_principal_ctx = contextvars.ContextVar("vayl_principal", default=None)
_AUTH_REQUIRED = env_bool("VAYL_AUTH_REQUIRED", False)


def set_principal(principal):
    """Bind the authenticated caller for this request (used by the remote transport in M2), AND its
    tenant, so every store query in the request is hard-partitioned to the caller's org. Returns an
    opaque token to pass to reset_principal() when the request ends."""
    ttok = bind_tenant(getattr(principal, "tenant", None))
    ptok = _principal_ctx.set(principal)
    return (ptok, ttok)


def reset_principal(token):
    """Unbind the request's principal and tenant (paired with set_principal in the transport middleware)."""
    ptok, ttok = token
    _principal_ctx.reset(ptok)
    reset_tenant(ttok)


def _current_principal():
    p = _principal_ctx.get()
    if p is not None:
        return p
    return None if _AUTH_REQUIRED else auth.local_admin()   # stdio local dev → trusted admin


# There is no process-global memory lock. It used to serialize EVERY tool because it secretly
# protected three things at once: id allocation (a process-global counter), the shared DB connection,
# and the audit hash-chain's append order. Each now handles its own concurrency — ids are allocated
# per space (seeded from MAX(id) at load), connections are thread-local, and Audit.record serializes
# its own chain append — so tools to DIFFERENT spaces run in parallel. Writes to the SAME space still
# serialize, but via db.space_lock (an in-process lock on SQLite, a cross-process advisory lock on
# Postgres) held around load→mutate→save, so no two writers seed ids from the same MAX.


def _deny(tool, reason, message):
    """Record a denied access attempt (accountability) and return the client-facing message."""
    try:
        _metrics.record_call(tool, 0.0, ok=False)
        _metrics.record_error(tool, "AccessDenied", reason)
        _audit.record("access_denied", getattr(_current_principal(), "id", "") or "", "", "",
                      f"{tool}: {reason}")
    except Exception:
        # the denial itself stands; losing its audit record is an accountability gap worth surfacing
        log.error("failed to record access denial for %s", tool, exc_info=True)
    return message


def _is_operator(principal):
    """The deployment operator: an admin of the 'default' tenant, which includes the local stdio admin.
    Only the operator reaches across tenants (principals in every tenant, the deployment-wide audit
    purge, deployment-wide metrics). An admin of any other tenant runs only that tenant."""
    return principal is not None and principal.can(C.ADMIN) and principal.tenant == "default"


def _critical(spec):
    """Parse a comma-separated critical-category list. Empty means 'use the deployment default'
    (VAYL_CRITICAL_CATEGORIES), NOT 'none' — a caller must not be able to silently opt out of
    always-injecting the categories an operator marked as safety-critical."""
    cats = [c.strip() for c in (spec or "").split(",") if c.strip()]
    return cats or None


def _space_key(user_id, agent_id="", run_id=""):
    """The advisory-lock key for one memory space (tenant-qualified). On Postgres this serializes
    same-space statement writes ACROSS vayl-server processes (via pg_advisory_xact_lock), so multiple
    processes on one database don't collide on id allocation; different spaces run in parallel. On
    SQLite it's a per-key in-process lock — now the actual same-space serializer (there is no longer
    a global lock above it), so concurrent writers to one space take turns while other spaces run."""
    return f"{_store.tenant}\x1f{user_id}\x1f{agent_id}\x1f{run_id}"


def _guard(tool, fn, cap=None, space=None):
    """Authorize, then run the tool. Authorization is fail-closed: an unauthenticated caller (when auth
    is required) is denied, a caller whose role lacks the tool's capability is denied, and a caller
    reaching for a memory space outside its scope is denied — all three are recorded.

    `space` is the user_id the call touches. It matters because user_id arrives from the CALLER: a
    valid key could otherwise read any tenant by passing that tenant's id. Capability answers "may
    this caller read"; scope answers "whose memory". Then: never let a tool crash the client (failures
    become a readable message), and time + record the call. The metric/audit bookkeeping is safe to run
    concurrently — metrics are atomic upserts and Audit.record serializes its own chain append."""
    principal = _current_principal()
    if principal is None:
        return _deny(tool, "unauthenticated",
                     "Access denied: authentication required. Provide a valid API key "
                     "(Authorization: Bearer vayl_sk_…).")
    if cap is not None and not principal.can(cap):
        return _deny(tool, f"role {principal.role_names} lacks '{cap.value}'",
                     f"Access denied: '{tool}' requires the '{cap.value}' capability; your role(s) "
                     f"{principal.role_names} do not grant it.")
    if space is not None and not principal.may_access(space):
        # Deliberately does not echo the requested user_id back: a scoped caller should not be
        # able to probe which spaces exist by reading the error text.
        return _deny(tool, f"principal {principal.id} is not scoped to the requested space",
                     f"Access denied: '{tool}' targets a memory space outside your assigned scope.")
    t0 = time.perf_counter()
    err: Exception | None = None
    ref = None
    try:
        return fn()
    except KeyError as e:
        # config-hint only: the message is a missing ENV VAR NAME, never data — safe to surface
        err = e
        return (f"Vayl config error: missing {e}. Set OPENAI_API_KEY (or ANTHROPIC_API_KEY / "
                "GROQ_API_KEY) and LLM_PROVIDER in the server's env, then restart.")
    except Exception as e:
        # Do NOT leak the exception to the client — its text can carry file paths or data fragments.
        # The client gets an opaque reference; the full detail goes to the server log + metrics.
        err = e
        ref = secrets.token_hex(4)
        # The exception TEXT can carry memory content (an LLM/HTTP error often echoes the prompt), and
        # logs usually leave the machine in plaintext, beyond the reach of erasure. So the log gets the
        # ref, the type and where it happened; the text goes to DEBUG and the encrypted metrics store.
        tb = traceback.extract_tb(e.__traceback__)
        where = f" at {os.path.basename(tb[-1].filename)}:{tb[-1].lineno} in {tb[-1].name}" if tb else ""
        log.error("[vayl %s] %s: %s%s", ref, tool, type(e).__name__, where)
        log.debug("[vayl %s] %s: full detail", ref, tool, exc_info=True)
        if isinstance(e, urllib.error.HTTPError):   # the status code carries no data; the text might
            return (f"Vayl couldn't complete that (ref {ref}): the model provider rejected the request "
                    f"(HTTP {e.code}). Run the `health` tool to see the provider's reason.")
        return (f"Vayl couldn't complete that (ref {ref}). Retry if it was a transient blip; "
                "otherwise the full detail is in the server logs under that reference.")
    finally:
        try:
            _metrics.record_call(tool, (time.perf_counter() - t0) * 1000, ok=err is None)
            if err is not None:
                _metrics.record_error(tool, type(err).__name__, (f"[{ref}] " if ref else "") + str(err))
        except Exception:
            log.debug("metrics recording failed for %s", tool, exc_info=True)  # never break a tool


# All memory tools accept optional agent_id / run_id: each (user_id, agent_id, run_id)
# combination is its own isolated memory space (leave them blank for a single space).


@mcp.tool(annotations=_write("Remember (extract & reconcile facts)"))
def remember(text: str, user_id: str = "default", agent_id: str = "", run_id: str = "",
             metadata: dict | None = None, source: str = "") -> str:
    """Store fact(s) from a statement. Reconciles automatically: a new value SUPERSEDES the old
    one (not appended), removals are RETRACTED, and multiple facts in one message are all captured.
    Optional `metadata` tags the facts stored in this call. `source` records WHO/WHAT asserted the
    fact (an agent id, a person, a connector) — belief provenance, and the basis for source-aware
    reconciliation in a shared space (see set_reconcile_policy). Examples: 'We switched from Zustand
    to Redux Toolkit', 'Alice is the new team lead'."""
    def go():
        if not _may_write_as(source):
            return _deny("remember", f"not authorized to write as trusted source '{source}'",
                         f"Access denied: '{source}' is a trusted source (VAYL_TRUSTED_SOURCES), so its "
                         f"changes skip the confirmation gate. Only a key named '{source}', or one with "
                         f"the 'approve' capability, may write as it.")
        with _store.db.space_lock(_space_key(user_id, agent_id, run_id)):
            m = _store.load(user_id, agent_id, run_id)
            before = {s.id for s in m.statements}
            results = m.add(text, source=source)
            if metadata:
                for s in m.statements:
                    if s.id not in before:
                        # Merge rather than assign-if-empty. The engine now stamps its own
                        # metadata (kind=event), and an assign-if-None rule silently dropped
                        # the caller's tags on exactly those facts — including `category`,
                        # which decides whether a fact is treated as critical.
                        s.metadata = {**(s.metadata or {}), **metadata}
            _store.save(user_id, m, agent_id, run_id)
        _metrics.record_actions([a.value for a, _s, _v in results])
        _audit.record("remember", user_id, agent_id, run_id,
                      (f"[{source}] " if source else "")
                      + ("; ".join(f"{a.value} {s}" for a, s, v in results) or "no durable fact"))
        # SKIP is the engine declining to record something (a hypothetical, sarcasm, a removal with
        # nothing to remove) — reporting it under "Stored:" told the caller the opposite.
        stored = [(a, s, v) for a, s, v in results if a.value != "SKIP"]
        skipped = [(s, v) for a, s, v in results if a.value == "SKIP"]
        if not stored and not skipped:
            return "No durable fact found (looked like chatter or a question)."
        out = []
        if stored:
            out.append("Stored: " + "; ".join(f"[{a.value}] {s} = {v}" for a, s, v in stored))
        if skipped:
            out.append("Not stored (hypothetical, sarcasm, or nothing to change): "
                       + "; ".join(f"{s} = {v}" for s, v in skipped))
        return "\n".join(out)
    return _guard("remember", go, cap=C.WRITE, space=user_id)


@mcp.tool(annotations=_ro("Recall from current memory", open_world=True))
def recall(question: str, user_id: str = "default", agent_id: str = "", run_id: str = "",
           explain: bool = False, include_history: bool = False,
           critical_categories: str = "") -> str:
    """Answer a question from CURRENT memory — the active value ('what state library do we use?')
    or a multi-hop question over current facts. Returns 'I don't know' rather than guessing, and
    NEVER answers from a retired/retracted fact. For the timeline of a subject, use `history`.

    Set `include_history=True` ONLY for questions explicitly about the past — 'what did we use
    before?', 'when did we switch?' — where retired facts are the answer. It is off by default
    because with it off, superseded values are never loaded at all, so a stale value cannot be
    returned as current regardless of how the model behaves. Leaving it off is the guarantee;
    turning it on trades that for reach into the timeline.

    `critical_categories` (comma-separated) names fact categories that must ALWAYS reach the
    answer regardless of ranking — 'allergy,active_medication' in a clinical deployment. Ordinary
    recall is semantic top-k, so a fact that does not rank is invisible, not merely deprioritised;
    for some categories that is a safety failure rather than a quality one. Leave empty to use the
    deployment-wide VAYL_CRITICAL_CATEGORIES; an empty value never means 'none'.

    Set `explain=True` to also get the PROVENANCE — the exact facts used to answer, each with who
    asserted it, its confidence, and what it superseded. That's the truthful basis for an audit of
    why the agent believes what it does (and it's what `record_decision` snapshots)."""
    def go():
        m = _store.load(user_id, agent_id, run_id)   # active-only: retired facts can't leak in
        _audit.record("recall", user_id, agent_id, run_id, question[:80])
        crit = _critical(critical_categories)
        if not explain:
            return m.query(question, include_history=include_history, critical_categories=crit)
        answer, used = m.query(question, with_provenance=True,
                               include_history=include_history, critical_categories=crit)
        if not used:
            return f"{answer}\n\n(no stored facts were used)"
        lines = "\n".join(
            f"  • #{p['id']} {p['subject']} = {p['value']}  "
            f"[conf {p['confidence']}"
            + (f", from {p['source']}" if p['source'] else "")
            + (f", supersedes #{p['supersedes']}" if p['supersedes'] else "") + "]"
            for p in used)
        return f"{answer}\n\nBased on these facts:\n{lines}"
    return _guard("recall", go, cap=C.READ, space=user_id)


@mcp.tool(annotations=_ro("Recall (relational / graph)", open_world=True))
def recall_related(question: str, user_id: str = "default", agent_id: str = "", run_id: str = "") -> str:
    """Answer a DEEP / RELATIONAL / multi-hop question using the entity graph
    ('who owns the company Bob works for?', 'which services depend on the auth module?').
    Uses the Neo4j graph projection when configured; otherwise falls back to slot recall."""
    def go():
        m = _store.load(user_id, agent_id, run_id)
        answer, _seeds, _edges = m.graph_query(question)
        return answer
    return _guard("recall_related", go, cap=C.READ, space=user_id)


@mcp.tool(annotations=_write("Forget / retract (kept in history for audit)"))
def forget(text: str, user_id: str = "default", agent_id: str = "", run_id: str = "") -> str:
    """Retract a fact — a removal with no replacement. Unlike additive stores, this actually
    retires it so it stops being returned as current (kept in history for audit). Examples:
    'We dropped Sentry', 'Alice left the Platform team', 'we no longer support IE11'."""
    def go():
        with _store.db.space_lock(_space_key(user_id, agent_id, run_id)):
            m = _store.load(user_id, agent_id, run_id)
            results = m.forget(text)
            _store.save(user_id, m, agent_id, run_id)
        _metrics.record_actions([a.value for a, _s, _v in results])
        _audit.record("forget", user_id, agent_id, run_id,
                      "; ".join(f"{a.value} {s}={v}" for a, s, v in results
                                if a.value in ("RETRACT", "FLAG", "DEDUP")) or "nothing to retract")
        # On a confirm-required slot the engine PROPOSES the removal (FLAG) instead of applying it,
        # or finds the same removal already queued (DEDUP). Saying "nothing to retract" there hid a
        # pending proposal from the caller.
        by = {k: [(s, v) for a, s, v in results if a.value == k] for k in ("RETRACT", "FLAG", "DEDUP")}
        out = []
        if by["RETRACT"]:
            out.append("Retracted (retained in history for audit): "
                       + "; ".join(f"{s} = {v}" for s, v in by["RETRACT"]))
        if by["FLAG"]:
            out.append("Proposed for removal, awaiting approval (the value stays current until someone "
                       "approves it with confirm_change; see pending_changes): "
                       + "; ".join(f"{s} = {v}" for s, v in by["FLAG"]))
        if by["DEDUP"]:
            out.append("Already awaiting approval (see pending_changes): "
                       + "; ".join(s for s, _v in by["DEDUP"]))
        return "\n".join(out) or "Nothing matching to retract (that fact isn't currently stored)."
    return _guard("forget", go, cap=C.WRITE, space=user_id)


@mcp.tool(annotations=_ro("Get one memory by id"))
def get_memory(memory_id: int, user_id: str = "default", agent_id: str = "", run_id: str = "") -> str:
    """Fetch one memory by its id (the `#id` shown by `list_memories`) — its value, status, scope,
    confidence, what it supersedes, and any metadata."""
    def go():
        s = _store.get_row(user_id, memory_id, agent_id, run_id)   # cold read — works for any id
        if s is None:
            return f"No memory with id #{memory_id}."
        sup = f"  supersedes=#{s.supersedes}" if s.supersedes else ""
        meta = f"  metadata={s.metadata}" if s.metadata else ""
        return (f"#{s.id}  {s.subject} = {s.value}\n"
                f"  status={s.status.value}  scope={s.scope}  confidence={s.confidence}{sup}{meta}")
    return _guard("get_memory", go, cap=C.READ, space=user_id)


@mcp.tool(annotations=_write("Update a memory (audit-preserving)"))
def update_memory(memory_id: int, new_value: str, user_id: str = "default",
                  agent_id: str = "", run_id: str = "") -> str:
    """Correct a memory by id (the `#id` from `list_memories`). Audit-preserving: the old value is
    retired to history and the new value becomes active — so the change still shows up in `history`."""
    def go():
        with _store.db.space_lock(_space_key(user_id, agent_id, run_id)):
            m = _store.load(user_id, agent_id, run_id)
            old = m.get(memory_id)
            if old is None:
                return f"No memory with id #{memory_id}."
            prev = old.value
            new = m.update(memory_id, new_value)
            _store.save(user_id, m, agent_id, run_id)
            _audit.record("update", user_id, agent_id, run_id, f"#{memory_id}: '{prev}' -> '{new_value[:60]}'")
        return f"Updated #{memory_id}: '{prev}' → '{new.value}' (now #{new.id}; old kept in history)."
    return _guard("update_memory", go, cap=C.WRITE, space=user_id)


@mcp.tool(annotations=_ro("Change history for a subject"))
def history(subject: str, user_id: str = "default", agent_id: str = "", run_id: str = "") -> str:
    """The change-log for a subject — every value it has held, oldest → newest, with status.
    The 'what did we use first vs now' view that additive stores can't give you. The `subject`
    is the key shown by `list_memories`."""
    def go():
        rows = _store.history_rows(user_id, subject, agent_id, run_id)
        if not rows:
            return f"No history for '{subject}'."
        return f"History for '{subject}' (oldest → newest):\n" + "\n".join(
            f"  {i + 1}. {s.value}  [{s.status.value}]" for i, s in enumerate(rows))
    return _guard("history", go, cap=C.READ, space=user_id)


# ── decision audit (Feature 1): record WHY an agent acted, explain it later ────

@mcp.tool(annotations=_write("Record a decision + the beliefs behind it"))
def record_decision(action_summary: str, question: str, user_id: str = "default",
                    agent_id: str = "", run_id: str = "") -> str:
    """Log a decision an agent is about to take, bound to the EXACT facts it consulted. Atomically
    recalls `question` with provenance and snapshots those beliefs (value, source, confidence, what
    they superseded) alongside `action_summary`. The snapshot is immutable and signed, so 'why did
    the agent do X?' stays answerable even after the facts later change. Returns the decision id and
    a verifiable receipt hash. Example: action_summary='Issued refund to order #91', question='what
    is the refund policy and this customer's tier?'."""
    def go():
        m = _store.load(user_id, agent_id, run_id)
        answer, used = m.query(question, with_provenance=True)
        anchor = _audit.head_hash()
        _audit.record("record_decision", user_id, agent_id, run_id, action_summary[:80])
        did, digest = _decisions.record(action_summary, used, user_id, agent_id, run_id, anchor=anchor)
        return (f"Decision #{did} recorded, bound to {len(used)} belief(s).\n"
                f"  based on: {answer}\n"
                f"  receipt: {digest[:16]}…  (signed; verify with explain_decision #{did})")
    return _guard("record_decision", go, cap=C.WRITE, space=user_id)


@mcp.tool(annotations=_ro("Explain a past decision (why the agent acted)"))
def explain_decision(decision_id: int, user_id: str = "default") -> str:
    """Reconstruct why an agent acted: the recorded action and the exact beliefs it held at that
    moment — even if those facts have since been superseded, retracted, or erased. Also verifies the
    decision's signed receipt so you know the record itself wasn't altered."""
    def go():
        d = _decisions.get(decision_id, user_id=user_id)
        if d is None:
            return f"No decision #{decision_id} in this scope."
        seal = ("✓ receipt verified (record intact)" if d["verified"]
                else "⚠ receipt FAILED verification (record altered)" if d["verified"] is False
                else "(unsigned)")
        beliefs = d["beliefs"] or []
        lines = "\n".join(
            f"  • #{b.get('id')} {b.get('subject')} = {b.get('value')}  "
            f"[conf {b.get('confidence')}"
            + (f", from {b.get('source')}" if b.get('source') else "")
            + (f", superseded #{b.get('supersedes')}" if b.get('supersedes') else "")
            + (f", status {b.get('status')}" if b.get('status') else "") + "]"
            for b in beliefs) or "  (no beliefs were recorded)"
        return (f"Decision #{d['id']}  ({d['ts']})\n"
                f"  action: {d['summary']}\n"
                f"  believed at the time:\n{lines}\n"
                f"  {seal}")
    return _guard("explain_decision", go, cap=C.VERIFY, space=user_id)


# ── autonomous-action safety gate (Feature 2): refuse to act on unsafe memory ──

def _policy(min_confidence, require_active, block_on_flagged, max_staleness_days, block_on_recent_change_days):
    from vayl.security.safety import SafetyPolicy
    return SafetyPolicy(
        min_confidence=min_confidence, require_active=require_active, block_on_flagged=block_on_flagged,
        max_staleness_days=(max_staleness_days if max_staleness_days and max_staleness_days > 0 else None),
        block_on_recent_change_days=(block_on_recent_change_days if block_on_recent_change_days and block_on_recent_change_days > 0 else None))


@mcp.tool(annotations=_ro("Safety check before acting on a fact"))
def check_before_act(subject: str, user_id: str = "default", agent_id: str = "", run_id: str = "",
                     min_confidence: float = 0.7, require_active: bool = True, block_on_flagged: bool = True,
                     max_staleness_days: float = 0, block_on_recent_change_days: float = 0) -> str:
    """Ask whether it's SAFE to take an autonomous action based on what memory knows about `subject`.
    Returns SAFE, or BLOCKED with explicit reasons — an unresolved conflict, confidence below the bar,
    a value that's gone stale, or one that just changed. Call this before any irreversible action
    (a payment, an email, a config change) so the agent never silently acts on bad memory. Tune the
    policy via the params (e.g. min_confidence=0.9, max_staleness_days=90 for high-stakes actions)."""
    def go():
        policy = _policy(min_confidence, require_active, block_on_flagged, max_staleness_days, block_on_recent_change_days)
        m = _store.load(user_id, agent_id, run_id)
        v = m.check(subject, policy)
        _audit.record("check_before_act", user_id, agent_id, run_id,
                      f"{subject}: {'SAFE' if v['ok'] else 'BLOCKED'}")
        if v["ok"]:
            vals = "; ".join(f"{f['subject']} = {f['value']}" for f in v["facts"])
            return f"✅ SAFE to act on '{subject}'.\n  current: {vals}"
        return (f"⛔ BLOCKED — do NOT act on '{subject}':\n"
                + "\n".join(f"  • {r}" for r in v["reasons"]))
    return _guard("check_before_act", go, cap=C.READ, space=user_id)


@mcp.tool(annotations=_ro("Gated recall — answers only if safe to act on", open_world=True))
def safe_recall(question: str, user_id: str = "default", agent_id: str = "", run_id: str = "",
                min_confidence: float = 0.7, require_active: bool = True, block_on_flagged: bool = True,
                max_staleness_days: float = 0, block_on_recent_change_days: float = 0,
                critical_categories: str = "") -> str:
    """Like `recall`, but for when the agent will ACT on the answer. It answers only if every current
    fact behind the answer passes the safety policy; otherwise it withholds the answer and returns the
    reasons (disputed value, low confidence, stale, or nothing current). Use this instead of `recall`
    on the path to an irreversible action.

    `critical_categories` behaves as in `recall`, and matters more here: this gate can only judge
    the facts it was handed, so a critical fact ranked out of context would be invisible to the
    check as well — it would pass, on an answer it never knew was incomplete."""
    def go():
        policy = _policy(min_confidence, require_active, block_on_flagged, max_staleness_days, block_on_recent_change_days)
        m = _store.load(user_id, agent_id, run_id)
        r = m.safe_recall(question, policy, critical_categories=_critical(critical_categories))
        _audit.record("safe_recall", user_id, agent_id, run_id,
                      f"{question[:60]}: {'ANSWERED' if r['ok'] else 'WITHHELD'}")
        if r["ok"]:
            return r["answer"]
        return ("⛔ WITHHELD — not safe to act on this answer:\n"
                + "\n".join(f"  • {r_}" for r_ in r["reasons"]))
    return _guard("safe_recall", go, cap=C.READ, space=user_id)


@mcp.tool(annotations=_destructive("Erase a subject permanently (GDPR erasure)"))
def delete(subject: str, user_id: str = "default", agent_id: str = "", run_id: str = "") -> str:
    """PERMANENTLY erase every record of a subject — including history and tombstones — for a user.
    Unlike `forget` (which retires but retains for audit), this is a hard delete for
    right-to-be-forgotten / GDPR erasure. The `subject` is the key shown by `list_memories`."""
    def go():
        with _store.db.space_lock(_space_key(user_id, agent_id, run_id)):
            n = _store.delete(user_id, subject, agent_id, run_id)
        # Art. 17 follow-through: scrub the erased subject's values from decision snapshots too
        nd = _decisions.redact(user_id, subject=subject, agent_id=agent_id, run_id=run_id)
        chain_hash = _audit.record("delete(erasure)", user_id, agent_id, run_id,
                                   f"subject={subject} rows={n} decisions_redacted={nd}")
        if not n and not nd:
            # The request is in the audit log; a signed receipt for erasing nothing would only be
            # an unreported receipt number the caller never sees.
            return f"No records found for '{subject}'."
        rec = receipts_mod.make_receipt(_signer, "delete", f"{user_id}/{agent_id}/{run_id}",
                                        subject, n, chain_hash)
        rid = _receipts.save(rec)
        return (f"Erased {n} record(s) for '{subject}'"
                + (f"; redacted its values from {nd} decision snapshot(s)" if nd else "") + ".\n"
                f"  signed erasure receipt #{rid} issued — verify with verify_receipt #{rid} "
                f"(or independently with the public key from export_public_key).")
    return _guard("delete", go, cap=C.DELETE, space=user_id)


@mcp.tool(annotations=_destructive("Erase ALL memory for a user permanently (GDPR erasure)"))
def delete_all(user_id: str = "default", agent_id: str = "", run_id: str = "") -> str:
    """PERMANENTLY erase memory (history included). With no agent_id/run_id, erases ALL of the
    user's memory across every space (account deletion / GDPR); pass them to erase just one space.
    Irreversible — not the same as `forget`, which retains history for audit."""
    def go():
        with _store.db.space_lock(_space_key(user_id, agent_id, run_id)):
            if not agent_id and not run_id:
                n = _store.delete_all(user_id)
                nd = _decisions.redact(user_id)   # Art. 17: scrub values from ALL their snapshots
            else:
                n = _store.delete_all(user_id, agent_id, run_id)
                nd = _decisions.redact(user_id, agent_id=agent_id, run_id=run_id)
        chain_hash = _audit.record("delete_all(erasure)", user_id, agent_id, run_id,
                                   f"rows={n} decisions_redacted={nd}")
        rec = receipts_mod.make_receipt(_signer, "delete_all", f"{user_id}/{agent_id}/{run_id}",
                                        "(all)", n, chain_hash)
        rid = _receipts.save(rec)
        return (f"Erased all memory for '{user_id}' ({n} record(s)"
                + (f"; values redacted from {nd} decision snapshot(s)" if nd else "") + ").\n"
                f"  signed erasure receipt #{rid} issued — verify with verify_receipt #{rid}.")
    return _guard("delete_all", go, cap=C.ADMIN, space=user_id)


# ── verifiable memory (Feature 3): prove what was known & erased ──────────────

@mcp.tool(annotations=_write("Attest the current value of a subject (signed proof)", open_world=False))
def attest(subject: str, user_id: str = "default", agent_id: str = "", run_id: str = "") -> str:
    """Issue a signed, third-party-verifiable attestation of what memory currently holds for
    `subject` — 'as of now, the active value is X', anchored to the tamper-evident audit-log head.
    Use it to prove what was known at a point in time. Verify later with verify_receipt, or
    independently with export_public_key."""
    def go():
        m = _store.load(user_id, agent_id, run_id)
        active = [s for s in m.active() if s.subject == subject]
        value = " · ".join(s.value for s in active) if active else "(nothing known)"
        chain_hash = _audit.record("attest", user_id, agent_id, run_id, f"subject={subject}")
        att = receipts_mod.make_attestation(_signer, subject, value, chain_hash,
                                            scope=f"{user_id}/{agent_id}/{run_id}")
        rid = _receipts.save(att)
        return (f"Attestation #{rid}: '{subject}' = {value}  (as of {att['payload']['ts']}).\n"
                f"  signed & anchored to audit head {chain_hash[:16]}… — verify with verify_receipt #{rid}.")
    return _guard("attest", go, cap=C.WRITE, space=user_id)


@mcp.tool(annotations=_ro("Verify a signed receipt / attestation"))
def verify_receipt(receipt_id: int) -> str:
    """Verify a persisted erasure receipt or knowledge attestation: recompute its canonical body and
    check the Ed25519 signature. Confirms the artifact is authentic and unaltered — the same check a
    third party can run with only the public key."""
    def go():
        rec = _receipts.get(receipt_id)
        if rec is None:
            return f"No receipt #{receipt_id}."
        # Receipt ids are sequential, so scope the lookup: a caller may only verify receipts belonging
        # to a space in its scope. The owner is the user_id at the head of the receipt's `scope`
        # ("user_id/agent_id/run_id"). A receipt with no scope (legacy) requires ADMIN to verify.
        caller = _current_principal()
        owner = ((rec.get("payload") or {}).get("scope") or "").split("/")[0]
        if owner:
            if not caller.may_access(owner):
                return "Access denied: that receipt belongs to a memory space outside your scope."
        elif not caller.can(C.ADMIN):
            return "Access denied: verifying an unscoped receipt requires the 'admin' capability."
        ok = receipts_mod.verify(rec)
        p = rec["payload"]
        detail = ("erased " + str(p.get("count")) + f" record(s) of '{p.get('subject')}'"
                  if p.get("kind") == "erasure_receipt"
                  else f"'{p.get('subject')}' = {p.get('value')}")
        return (f"Receipt #{receipt_id} [{p.get('kind')}] — {detail}  (ts {p.get('ts')})\n"
                f"  {'✓ VALID — signature verified, payload intact' if ok else '⚠ INVALID — signature does not match (altered or unsigned)'}")
    return _guard("verify_receipt", go, cap=C.VERIFY)


@mcp.tool(annotations=_ro("Verify the audit log is untampered"))
def verify_audit() -> str:
    """Verify the tamper-evident audit chain end-to-end. Recomputes every row's hash and (if signed)
    signature; reports INTACT, or the exact row where the chain breaks — so you can prove the
    accountability log wasn't edited, reordered, or truncated."""
    def go():
        r = _audit.verify_chain()
        if r["ok"]:
            return (f"✓ Audit chain INTACT — {r['verified']} entries verified"
                    + (f" ({r['legacy']} legacy/unchained)" if r["legacy"] else "") + ".")
        return f"⚠ Audit chain BROKEN at seq {r['broken_at']}: {r['reason']}."
    return _guard("verify_audit", go, cap=C.VERIFY)


@mcp.tool(annotations=_ro("Export the public key for third-party verification"))
def export_public_key() -> str:
    """Return Vayl's Ed25519 public key (hex). Anyone can use it to verify erasure receipts,
    attestations, and the signed audit chain WITHOUT the secret key or access to the database."""
    def go():
        if _signer is None:
            return "No signer configured (the `cryptography` package is unavailable)."
        return f"Ed25519 public key (hex):\n{_signer.public_key_hex()}"
    return _guard("export_public_key", go, cap=C.VERIFY)


# ── shared organizational memory (Feature 4): source-aware reconciliation ──────

@mcp.tool(annotations=_write("Configure how a shared space reconciles conflicting sources", open_world=False))
def set_reconcile_policy(mode: str = "RECENCY", authority: dict | None = None,
                         user_id: str = "default", agent_id: str = "", run_id: str = "") -> str:
    """Configure how a SHARED space resolves conflicts between DIFFERENT contributors (sources).
    A shared space is any space (e.g. user_id='org:acme') that multiple agents/people write to with
    a `source` on each remember. Modes:
      • RECENCY   — newer assertion wins (default; contributors equally trusted).
      • AUTHORITY — a higher-ranked source wins; a lower-ranked source contradicting it is FLAGGED,
                    not overwritten. Pass `authority` as {source: rank} (higher = more authoritative).
      • REVIEW    — any cross-source conflict is FLAGGED for a human.
    A source correcting its OWN earlier fact always supersedes, regardless of mode."""
    def go():
        from vayl.memory.orgmemory import ReconcileMode, ReconcilePolicy
        try:
            m = ReconcileMode(mode.upper())
        except ValueError:
            return f"Unknown mode '{mode}'. Use RECENCY, AUTHORITY, or REVIEW."
        policy = ReconcilePolicy(mode=m, authority={k: int(v) for k, v in (authority or {}).items()})
        with _store.db.space_lock(_space_key(user_id, agent_id, run_id)):
            _store.set_policy(user_id, policy, agent_id, run_id)
            _audit.record("set_reconcile_policy", user_id, agent_id, run_id,
                          f"mode={m.value} authority={policy.authority}")
        auth = f"  authority ranks: {policy.authority}" if policy.authority else ""
        return f"Reconciliation policy for this space set to {m.value}.{auth}"
    return _guard("set_reconcile_policy", go, cap=C.ADMIN, space=user_id)


@mcp.tool(annotations=_ro("Show a shared space's reconciliation policy"))
def get_reconcile_policy(user_id: str = "default", agent_id: str = "", run_id: str = "") -> str:
    """Show the source-aware reconciliation policy configured for a space (default RECENCY if unset)."""
    def go():
        p = _store.get_policy(user_id, agent_id, run_id)
        if p is None:
            return "Policy: RECENCY (default — newer assertion wins)."
        ranks = f"\n  authority ranks: {p.authority}" if p.authority else ""
        return f"Policy: {p.mode.value}{ranks}"
    return _guard("get_reconcile_policy", go, cap=C.READ, space=user_id)


# ── human-in-the-loop: changes to gated slots wait for a person ──────────────

@mcp.tool(annotations=_ro("List changes awaiting human approval", open_world=False))
def pending_changes(user_id: str = "default", agent_id: str = "", run_id: str = "") -> str:
    """List proposed changes to confirm-required slots that have NOT been applied.

    Slots declared with "confirm" in the slot schema do not auto-apply a replacement or removal —
    the current value stands until a person approves. This is the queue of what is waiting."""
    def go():
        m = _store.load(user_id, agent_id, run_id)
        rows = m.pending()
        if not rows:
            return "No changes awaiting approval."
        lines = []
        for s in rows:
            proposed = (s.metadata or {}).get("pending", "?")
            cur = next((t.value for t in m.active() if t.id == s.supersedes), "(unknown)")
            change = (f"REMOVE {s.subject}: {cur!r}" if proposed == "RETRACT"   # no new value to show
                      else f"REPLACE {s.subject}: {cur!r} -> {s.value!r}")
            lines.append(f"  #{s.id} {change}"
                         + (f"\n        said: {s.raw[:120]!r}" if s.raw else ""))
        return (f"{len(rows)} change(s) awaiting approval:\n" + "\n".join(lines)
                + "\n\nApprove with confirm_change(memory_id), discard with reject_change(memory_id).")
    return _guard("pending_changes", go, cap=C.READ, space=user_id)


def _may_write_as(source):
    """A trusted source (VAYL_TRUSTED_SOURCES) bypasses the confirmation gate, and `source` is a string
    the caller chooses — so claiming one is itself an approval. Allow it only for the key that IS that
    source (an integration key named e.g. 'fhir') or a caller who could approve the change anyway."""
    if not source or source.strip().lower() not in llm_memory._TRUSTED_SOURCES:
        return True
    p = _current_principal()
    return p is not None and (p.can(C.APPROVE) or p.name.strip().lower() == source.strip().lower())


def _approver(note=""):
    """Who decided a gated change: the authenticated caller, never caller-supplied text. The gate
    exists so a person signs off; a free-text name would let anyone record anyone. `decided_by` is
    kept, but only as a note beside the real identity."""
    p = _current_principal()
    who = f"{p.name} [{p.id}]" if p else "unknown"
    return f"{who} (note: {note})" if note else who


@mcp.tool(annotations=_write("Approve a pending change", open_world=False))
def confirm_change(memory_id: int, user_id: str = "default", agent_id: str = "", run_id: str = "",
                   decided_by: str = "") -> str:
    """Approve a proposed change to a confirm-required slot, applying it. Needs the `approve`
    capability (admin and member roles; agents lack it, so an agent can't approve its own proposal).
    The approver recorded is the authenticated caller; `decided_by` is an optional note."""
    def go():
        who = _approver(decided_by)
        with _store.db.space_lock(_space_key(user_id, agent_id, run_id)):
            m = _store.load(user_id, agent_id, run_id)
            res = m.confirm(memory_id, source=who)
            if res is None:
                return (f"#{memory_id} is not awaiting approval. It may have been decided already, "
                        f"or the value it would have replaced has since changed — in which case the "
                        f"proposal is stale and should be re-made against the current value.")
            _store.save(user_id, m, agent_id, run_id)
            action, subject, value = res
            _audit.record("confirm_change", user_id, agent_id, run_id,
                          f"#{memory_id} {action.value} {subject} by {who}")
        return f"Approved #{memory_id}: {action.value} {subject} = {value}"
    return _guard("confirm_change", go, cap=C.APPROVE, space=user_id)


@mcp.tool(annotations=_write("Discard a pending change", open_world=False))
def reject_change(memory_id: int, user_id: str = "default", agent_id: str = "", run_id: str = "",
                  decided_by: str = "") -> str:
    """Discard a proposed change. The current value stands. The proposal is kept as history —
    that someone proposed it is itself worth being able to audit. Needs `approve`, like
    confirm_change: an agent quietly discarding proposals would empty the review queue just as
    surely as approving them. `decided_by` is an optional note beside the authenticated caller."""
    def go():
        who = _approver(decided_by)
        with _store.db.space_lock(_space_key(user_id, agent_id, run_id)):
            m = _store.load(user_id, agent_id, run_id)
            res = m.reject(memory_id, source=who)
            if res is None:
                return f"#{memory_id} is not awaiting approval."
            _store.save(user_id, m, agent_id, run_id)
            _audit.record("reject_change", user_id, agent_id, run_id,
                          f"#{memory_id} rejected by {who}")
        return f"Discarded #{memory_id}. The current value is unchanged."
    return _guard("reject_change", go, cap=C.APPROVE, space=user_id)


# ── administration (M1): manage the principals (users/agents) of this deployment ──

@mcp.tool(annotations=_write("Create a principal (user/agent) + API key", open_world=False))
def create_principal(name: str, role: str = "member", kind: str = "agent",
                     scopes: str = "", tenant: str = "") -> str:
    """Admin only. Create a principal (a human user, agent, or service) and issue its API key. Roles:
    admin, member, agent, viewer, auditor. The key is shown ONCE — store it securely; it can't be
    recovered (reissue by creating a new principal).

    `scopes` is a comma-separated list of user_ids this principal may access, e.g.
    "cust_5521,cust_7788". Leave empty for unrestricted (the default, and correct for a
    single-tenant deployment). Set it for any multi-tenant deployment: without it, a valid
    key can read any space by passing that space's user_id. An admin role is unrestricted
    regardless, since org control implies reaching every space.

    `tenant` is the org partition this key belongs to (default: the caller's own tenant). It is a
    HARD boundary: every store query is filtered by it, so a key for one tenant cannot reach another
    tenant's memory even under the same user_id. Set it per org in a shared multi-tenant deployment.
    Only the deployment operator (an admin of the "default" tenant) may name a different tenant."""
    def go():
        caller = _current_principal()
        target = (tenant or "").strip() or caller.tenant
        if target != caller.tenant and not _is_operator(caller):
            return _deny("create_principal", f"admin of tenant '{caller.tenant}' tried to create a "
                         f"principal in tenant '{target}'",
                         "Access denied: you can only create principals in your own tenant.")
        try:
            role_enum = auth.Role(role.lower())
        except ValueError:
            return f"Unknown role '{role}'. Use: admin, member, agent, viewer, auditor."
        if _auth.count_active() >= _license.seat_cap:
            return (f"Seat limit reached: {_license.seat_cap} active principal(s) allowed on the "
                    f"{_license.edition} edition. Revoke an unused principal, or install a license "
                    f"with more seats (see mint_license.py / VAYL_LICENSE).")
        p, key = _auth.create(name, roles=role_enum, kind=kind, scopes=scopes or None, tenant=target)
        _audit.record("create_principal", getattr(_current_principal(), "id", "") or "", "", "",
                      f"{p.id} '{name}' role={role_enum.value} tenant={p.tenant} "
                      f"scopes={','.join(p.scopes) or '*'}")
        return (f"Created principal {p.id} '{name}' (role: {role_enum.value}, kind: {kind}, "
                f"tenant: {p.tenant}).\n  API key (shown once — save it now):\n  {key}")
    return _guard("create_principal", go, cap=C.ADMIN)


@mcp.tool(annotations=_ro("List principals of this deployment"))
def list_principals() -> str:
    """Admin only. List your tenant's principals — id, name, roles, and whether disabled (the
    deployment operator sees every tenant's). Never shows API keys (only their hashes are stored)."""
    def go():
        caller = _current_principal()
        rows = _auth.list(tenant=None if _is_operator(caller) else caller.tenant)
        if not rows:
            return "No principals yet. Create one with create_principal."
        return "\n".join(
            f"{'✗' if r['disabled'] else '•'} {r['id']}  {r['name']}  "
            f"[{', '.join(r['roles'])}]  {r['kind']}" + ("  (disabled)" if r['disabled'] else "")
            + (f"  tenant={r['tenant']}" if r['tenant'] != "default" else "")
            for r in rows)
    return _guard("list_principals", go, cap=C.ADMIN)


@mcp.tool(annotations=_ro("Show the license / edition status"))
def license_status() -> str:
    """Show the deployment's edition and entitlements — Community or a licensed edition, seats used vs
    allowed, expiry, and unlocked features. If a license was rejected (tampered/expired/wrong key),
    this says so and reports the Community fallback it's running under."""
    def go():
        # seats are counted across every tenant, so only the deployment operator sees the usage
        if not _is_operator(_current_principal()):
            return f"{_license.summary()}\n  (seat usage is visible to the deployment operator)"
        used = _auth.count_active()
        return f"{_license.summary()}\n  principals in use: {used} / {_license.seat_cap}"
    return _guard("license_status", go, cap=C.VERIFY)


@mcp.tool(annotations=_destructive("Revoke a principal's access"))
def revoke_principal(principal_id: str, erase: bool = False) -> str:
    """Admin only. Disable a principal — its API key stops working immediately. Irreversible
    (create a new principal to restore access). With `erase=True`, the principal row is HARD-deleted
    (Art. 17 for team members) instead of retained as disabled. A tenant admin can only revoke its
    own tenant's principals; another tenant's id reads as unknown."""
    def go():
        caller = _current_principal()
        tenant = None if _is_operator(caller) else caller.tenant
        if erase:
            ok = _auth.delete(principal_id, tenant=tenant)
            if ok:
                _audit.record("erase_principal", getattr(_current_principal(), "id", "") or "", "", "",
                              principal_id)
            return (f"Erased {principal_id} — key revoked and the principal record hard-deleted." if ok
                    else f"No principal {principal_id}.")
        ok = _auth.revoke(principal_id, tenant=tenant)
        if ok:
            _audit.record("revoke_principal", getattr(_current_principal(), "id", "") or "", "", "",
                          principal_id)
        return (f"Revoked {principal_id} — its API key no longer works." if ok
                else f"No active principal {principal_id} (unknown or already revoked).")
    return _guard("revoke_principal", go, cap=C.ADMIN)


@mcp.tool(annotations=_ro("List current memories"))
def list_memories(user_id: str = "default", agent_id: str = "", run_id: str = "") -> str:
    """List the current active facts (the reconciled current beliefs) for a user, plus a history
    block of what was superseded / retracted / archived. Active rows show the `#id` for get/update."""
    def go():
        act, flg, _sup, _hist = _store.load(user_id, agent_id, run_id).view()
        changed = _store.cold_tail(user_id, agent_id, run_id, limit=20)
        lines = [f"• {s.subject} = {s.value}  (#{s.id})" for s in act]
        lines += [f"⚠ {s.subject} = {s.value}  (#{s.id}, flagged — needs confirmation)" for s in flg]
        if changed:
            lines.append("")
            lines.append("— history (superseded / retracted / archived) —")
            lines += [f"  ◦ {s.subject} = {s.value}  [{s.status.value}]" for s in changed]
        return "\n".join(lines) or "(no memories yet)"
    return _guard("list_memories", go, cap=C.READ, space=user_id)


@mcp.tool(annotations=_ro("Export all memory as JSON (GDPR access/portability)"))
def export_memory(user_id: str = "default", agent_id: str = "", run_id: str = "") -> str:
    """Export everything held about a user/space as machine-readable JSON — data-subject access &
    portability (GDPR Art. 15/20). A complete DSAR answer: statements (active + full history), PLUS
    what the append-only records hold about them — decision snapshots, audit entries, and receipts."""
    def go():
        data = _store.export(user_id, agent_id, run_id)
        data["decisions"] = [d for d in
                             (_decisions.get(row["id"], user_id=user_id)
                              for row in _decisions.list(user_id=user_id, limit=1000)) if d]
        data["audit"] = _audit.tail(limit=1000, user_id=user_id)
        data["receipts"] = _receipts.for_user(user_id)
        _audit.record("export", user_id, agent_id, run_id,
                      f"{data['count']} records, {len(data['decisions'])} decisions, "
                      f"{len(data['audit'])} audit entries, {len(data['receipts'])} receipts")
        return json.dumps(data, indent=2)
    return _guard("export_memory", go, cap=C.READ, space=user_id)


@mcp.tool(annotations=_destructive("Purge records older than N days (retention)"))
def purge_expired(older_than_days: int, user_id: str = "default", agent_id: str = "", run_id: str = "",
                  include_audit: bool = False, include_decisions: bool = False,
                  include_receipts: bool = False) -> str:
    """Retention / storage-limitation (GDPR Art. 5(1)(e)): permanently delete records written more
    than `older_than_days` ago. Statements are scoped to the user/space. The optional flags extend
    the same window to the append-only tables — NOTE these are not per-user: `include_decisions` and
    `include_receipts` purge those tables for your whole tenant, and `include_audit` purges the audit
    log from the head of the chain (a signed retention anchor keeps the remaining chain verifiable).
    There is one chain per deployment, shared by every tenant, so `include_audit` is DEPLOYMENT-WIDE
    and only the deployment operator may use it."""
    def go():
        if include_audit and not _is_operator(_current_principal()):
            return _deny("purge_expired", "include_audit requested by a non-operator",
                         "Access denied: the audit log is shared by every tenant, so only the "
                         "deployment operator may purge it (include_audit).")
        n = _store.expire(user_id, older_than_days, agent_id or None, run_id or None)
        extra = []
        if include_decisions:
            extra.append(f"decisions={_decisions.purge(older_than_days)}")
        if include_receipts:
            extra.append(f"receipts={_receipts.purge(older_than_days)}")
        if include_audit:
            extra.append(f"audit={_audit.purge(older_than_days)}")
        _audit.record("purge_expired", user_id, agent_id, run_id,
                      f"older_than={older_than_days}d rows={n} " + " ".join(extra))
        tail = f" Also purged: {', '.join(extra)}." if extra else ""
        return f"Purged {n} record(s) older than {older_than_days} days.{tail}"
    return _guard("purge_expired", go, cap=C.ADMIN)


@mcp.tool(annotations=_ro("Audit log (accountability trail)"))
def audit_log(limit: int = 50, user_id: str = "") -> str:
    """The accountability trail (GDPR Art. 5(2)) — recent data operations: who / what / when.
    Optionally filter by user_id. Not affected by memory erasure."""
    def go():
        # The deployment-wide log (no user_id) crosses every tenant, so it requires ADMIN. A specific
        # user_id is scope-checked by _guard below, so a scoped caller can only read its own spaces —
        # without this the audit trail (which carries memory content in `detail`) leaked cross-tenant.
        if not user_id and not _current_principal().can(C.ADMIN):
            return ("Access denied: the deployment-wide audit log requires the 'admin' capability. "
                    "Pass a user_id within your scope to see that space's trail.")
        # Rows are confined to the caller's tenant; only the operator's unfiltered view spans them all.
        everyone = not user_id and _is_operator(_current_principal())
        rows = _audit.tail(limit=limit, user_id=user_id or None, all_tenants=everyone)
        if not rows:
            return "No audit entries."
        return "\n".join(
            f"{r['ts']}  {r['action']:<18} "
            + (f"tenant={r['tenant']} " if everyone and r['tenant'] != "default" else "")
            + f"user={r['user_id']}"
            + (f"/{r['agent_id']}" if r['agent_id'] else "") + f"  {r['detail']}" for r in rows)
    return _guard("audit_log", go, cap=C.VERIFY, space=user_id or None)


@mcp.tool(annotations=_ro("Usage stats & KPIs"))
def stats() -> str:
    """Operational KPIs for this Vayl instance — on-device, nothing leaves the machine:
    per-tool call counts, average latency and error counts, plus the distribution of
    reconciliation actions the engine has taken (SUPERSEDE / RETRACT / FLAG / SKIP / ADD / …)."""
    def go():
        # Metrics are deployment-wide counters with no tenant: a caller in any other tenant (a
        # customer org on a shared deployment) would be reading every tenant's activity.
        caller = _current_principal()
        if caller.tenant != "default":
            return _deny("stats", f"caller in tenant '{caller.tenant}' asked for deployment-wide stats",
                         "Access denied: stats are deployment-wide; ask the deployment operator.")
        snap = _metrics.snapshot()
        errors = _metrics.recent_errors(5)
        tools, actions = snap["tools"], snap["actions"]
        if not tools and not actions:
            return "No activity recorded yet."
        lines = ["Tools  (calls · avg ms · errors):"]
        for name in sorted(tools):
            d = tools[name]
            lines.append(f"  {name}: {d['calls']} · {d['avg_ms']} ms · {d['errors']} err")
        if actions:
            total = sum(actions.values())
            lines.append("")
            lines.append(f"Reconciliation actions  ({total} total):")
            for a in sorted(actions, key=lambda k: -actions[k]):
                pct = round(100 * actions[a] / total) if total else 0
                lines.append(f"  {a}: {actions[a]} ({pct}%)")
        if errors:
            lines.append("")
            lines.append("Recent errors (most recent first):")
            # Error text can embed memory content from ANY tenant, and metrics are deployment-wide —
            # every role holds VERIFY, so only an admin (of the default tenant, per the check above:
            # the deployment operator) may read the text.
            if caller.can(C.ADMIN):
                lines += [f"  {e['tool']}: {e['type']}: {e['msg']}" for e in errors]
            else:
                lines += [f"  {e['tool']}: {e['type']}" for e in errors]
                lines.append("  (error details are visible to admins only)")
        return "\n".join(lines)
    return _guard("stats", go, cap=C.VERIFY)


@mcp.tool(annotations=_ro("Health check", open_world=True))
def health() -> str:
    """Check that Vayl is configured and its dependencies are reachable — database, embedder,
    LLM, and graph (if enabled). Run this to diagnose setup before relying on memory; it makes
    one small LLM/embed call, so it costs a few tokens."""
    def go():
        from vayl.memory import llm_client
        from vayl.memory.llm_client import _embed
        from vayl.memory.llm_memory import llm_extract_classify
        report = [f"config: LLM_PROVIDER={os.environ.get('LLM_PROVIDER', '(unset)')}, "
                  f"model={os.environ.get('OPENAI_MODEL') or os.environ.get('GROQ_MODEL') or '(default)'}",
                  f"license: {_license.edition}" + ("" if _license.valid else f" (rejected: {_license.reason})"),
                  f"encryption: {'on' if _store.crypter else 'OFF (VAYL_ENCRYPT=off)'} · "
                  f"signing: {'on' if _signer else 'OFF (VAYL_SIGN=off)'}"]
        try:
            _store.db.execute("SELECT 1"); report.append("db: ok")
        except Exception as e:
            report.append(f"db: FAIL ({type(e).__name__})")   # type only — detail is in server logs
        # one attempt each: a diagnostic should report an unreachable endpoint now, not after minutes of
        # retry backoff — which is exactly the situation someone runs health() to diagnose
        with llm_client.single_attempt():
            try:
                _embed(["ping"]); report.append("embedder: ok")
            except Exception as e:   # fixed probe text, so the provider's reason carries no user data
                report.append(f"embedder: FAIL ({llm_client.http_error_reason(e) or type(e).__name__})")
            try:
                llm_extract_classify("health check", []); report.append("llm: ok")
            except Exception as e:
                report.append(f"llm: FAIL ({llm_client.http_error_reason(e) or type(e).__name__})")
        if _store.graph:
            try:
                _store.graph.all_edges(limit=1); report.append("graph: ok")
            except Exception as e:
                report.append(f"graph: FAIL ({type(e).__name__})")
        else:
            report.append("graph: disabled")
        return "\n".join(report)
    return _guard("health", go, cap=C.VERIFY)


# The HTTP request a log line belongs to (set by server.RequestIdMiddleware; "-" on stdio).
REQUEST_ID = contextvars.ContextVar("vayl_request_id", default="-")


class _RequestIdFilter(logging.Filter):
    def filter(self, record):
        record.request_id = REQUEST_ID.get()
        return True


class _JsonFormatter(logging.Formatter):
    """One JSON object per line, for log shippers. `extra={"fields": {...}}` adds structured keys."""
    def format(self, record):
        out = {"ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"), "level": record.levelname,
               "logger": record.name, "request_id": getattr(record, "request_id", "-"),
               "msg": record.getMessage(), **getattr(record, "fields", {})}
        if record.exc_info:
            out["exc"] = self.formatException(record.exc_info)
        return json.dumps(out, default=str)


def configure_logging():
    """Called by the entry points only — library code just gets loggers. Always stderr: on stdio the
    MCP protocol owns stdout. VAYL_LOG_LEVEL sets verbosity (default WARNING) and VAYL_LOG_FORMAT picks
    `text` (default) or `json`; an unknown value of either fails at startup rather than being ignored.
    Every line carries the request ID, so an error ref can be traced back to the HTTP request."""
    fmt = os.environ.get("VAYL_LOG_FORMAT", "text").lower()
    if fmt not in ("text", "json"):
        raise ValueError(f"VAYL_LOG_FORMAT must be 'text' or 'json', got {fmt!r}")
    handler = logging.StreamHandler(sys.stderr)
    handler.addFilter(_RequestIdFilter())
    handler.setFormatter(_JsonFormatter() if fmt == "json" else logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s [%(request_id)s]: %(message)s"))
    level = os.environ.get("VAYL_LOG_LEVEL", "WARNING").upper()
    if level not in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"):
        raise ValueError(f"VAYL_LOG_LEVEL must be DEBUG, INFO, WARNING, ERROR or CRITICAL, got {level!r}")
    logging.basicConfig(level=level, handlers=[handler])


def startup():
    """Entry-point setup: logging, then settings that are otherwise read lazily — so a typo fails at
    launch with a clear message instead of on the first tool call."""
    configure_logging()
    from vayl.memory.llm_client import _provider
    _provider()


def main():
    startup()
    # show_banner=False: on stdio the banner would print to the console; keep the transport clean.
    mcp.run(show_banner=False)


if __name__ == "__main__":
    main()
