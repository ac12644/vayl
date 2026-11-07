#!/usr/bin/env python3
"""
SQLite persistence for Vayl — memory must survive restarts to be a product.
Namespaced per (user_id, agent_id, run_id) — each combination is its own memory space.

HOT-PATH SEPARATION (so a long-lived, history-heavy user stays fast):
Vayl is event-sourced — supersede/retract/update keep the old rows — so history grows
unbounded. But reconciliation and recall only need the ACTIVE working set, which stays
small. So:
  • load() pulls only ACTIVE + FLAGGED rows (the hot set), never the full history →  O(active).
  • save() is INCREMENTAL — inserts new rows, updates changed ones — never a rewrite  →  O(changes).
  • only ACTIVE facts are embedded (recall ranks the active set); history is fetched by
    id/subject, so tombstones/superseded rows carry no embedding                       →  smaller + faster.
History stays on disk and is read lazily via get_row() / history_rows() / cold_tail().
"""
import array
import base64
import contextvars
import hashlib
import itertools
import json
import logging
import math
import os
import threading
import time

from vayl.config import env_bool, env_int
from vayl.memory.llm_client import _embed
from vayl.memory.llm_memory import LLMMemory
from vayl.memory.reconcile import Statement, Status
from vayl.security import crypto
from vayl.storage.db import Database
from vayl.storage.migrations import migrate

log = logging.getLogger(__name__)

# Decoded-vector cache size (entries, ~6 KB each at 1536 dims -> ~50 MB at the default).
# ponytail: FIFO eviction; a space with more embedded facts than this re-decrypts the overflow on each
# recall. Raise VAYL_VECTOR_CACHE for spaces that large.
_VECTOR_CACHE = env_int("VAYL_VECTOR_CACHE", 8192)
_HOT = ("ACTIVE", "FLAGGED_CONFLICT")


class _Vec(array.array):
    """A decoded embedding that carries its own norm, computed once. Cached vectors are ranked on
    every recall; recomputing |v| each time was a third of ranking cost. Riding on the vector (not a
    side table) means a re-embedded fact can never be ranked with its old norm."""
    norm: float   # statuses kept in the in-memory working set

# The active tenant for the current request. The remote server binds it from the caller's principal
# (see api.mcp_server.set_principal); unset (stdio / single-tenant) falls back to the Store's default.
# Every query filters by Store.tenant, so binding this hard-partitions one org's memory from another's.
_TENANT = contextvars.ContextVar("vayl_tenant", default=None)


def bind_tenant(tenant):
    """Bind the active tenant for the current request context. Returns a token for reset_tenant()."""
    return _TENANT.set(tenant or None)


def reset_tenant(token):
    """Unbind the request's tenant (paired with bind_tenant in the transport middleware)."""
    _TENANT.reset(token)


def current_tenant():
    """The tenant bound for this request, else 'default'. For the accountability tables (decisions,
    receipts, audit), which share the store's database but not a Store instance."""
    return _TENANT.get() or "default"
_EMBED_RAW_CHARS = 300                  # how much of the source sentence enters the vector


def _embed_text(s):
    """The text a fact is embedded from: its slot AND the sentence it came from.

    Embedding the slot alone ("support_group attended_recently") makes semantic retrieval blind to
    every detail normalization stripped — a question about the sunrise she painted cannot match a
    vector built from `melanie_hobby=painting`. The sentence is already stored on every statement
    and already feeds the lexical half of retrieval; leaving it out of the semantic half meant one
    of the two rankings could not see most of what was written down.

    Truncated so a single verbose turn cannot dominate a vector.
    """
    raw = (getattr(s, "raw", "") or "").strip()[:_EMBED_RAW_CHARS]
    return f"{s.subject} {s.value} {raw}".strip()
_COLS = ("id,slot,subject,value,scope,status,supersedes,confidence,raw,embedding,metadata,source,"
         "created_at,head,relation,tail")
# Hot-path variant: report only WHETHER an embedding exists, never its contents. A 1536-dim vector
# stored as JSON is ~15 KB before encryption, so materialising every one on every load() dominated
# read cost (measured: 5.3 s of a 5.9 s recall at 5k facts). Reconciliation never needs the vectors;
# only ranking does, and only when the space exceeds the recall cap — so they are fetched on demand.
_COLS_LIGHT = _COLS.replace("embedding", "(embedding IS NOT NULL)")


class Store:
    def __init__(self, path="vayl.db", graph=None, tenant_id="default"):
        self.graph = graph          # optional shared Neo4jGraph (single-tenant in the MVP)
        self._default_tenant = tenant_id   # tenant used when no request has bound one (stdio / single-tenant)
        # At-rest encryption (ON by default): content columns (slot/subject/value/raw/metadata/embedding)
        # become ciphertext; `subject_hmac` is a blind index so subject-equality queries still work.
        # Structural columns (ids/status/confidence/scope) stay plaintext.
        self.crypter = crypto.resolve(path)
        self._vectors = {}          # sha256(stored embedding) -> decoded vector; see _vector()
        self._vector_cap = _VECTOR_CACHE if env_bool("VAYL_DECRYPT_CACHE", True) else 0
        self._vectors_lock = threading.Lock()
        # Backend: SQLite by default (the `path`); Postgres when VAYL_DATABASE_URL is set (M6).
        self.db = Database(os.environ.get("VAYL_DATABASE_URL") or path)
        migrate(self.db)            # schema lives in storage/migrations.py (versioned, one ordered list)

    @property
    def tenant(self):
        """The ISOLATION SEAM: every row is stamped with it and every query filters by it, so two
        tenants on one DB never see each other's memory — even under the SAME user_id. The remote
        server binds it per request from the caller's principal (bind_tenant); unbound requests
        (stdio / single-tenant) use the deployment default."""
        return _TENANT.get() or self._default_tenant

    def _ns(self, user_id, agent_id="", run_id=""):
        """Graph namespace for a space. The tenant comes first: every tenant shares one Neo4j, so the
        namespace has to partition by tenant exactly as every SQL query does. Without it, two tenants
        with the same user_id read and erased each other's edges."""
        return f"{self.tenant}\x1f{user_id}\x1f{agent_id}\x1f{run_id}"

    def _enc(self, s):
        return self.crypter.enc(s) if self.crypter else s

    def _dec(self, s):
        return self.crypter.dec(s) if self.crypter else s

    @staticmethod
    def _emb_encode(vec):
        """Pack a vector as base64 float32 rather than JSON.

        A 1536-dim embedding is ~15 KB of JSON text but 6 KB of float32, and parsing bytes is far
        cheaper than parsing 1536 decimal literals — which matters because every hydrated vector pays
        that cost. float32 is the precision the embedding APIs return anyway, and is ample for cosine
        ranking."""
        if vec is None:
            return None
        return base64.b64encode(array.array("f", vec).tobytes()).decode()

    @staticmethod
    def _emb_decode(raw):
        """Read either encoding — rows written before this change are JSON and stay readable."""
        if not raw or raw.strip() == "null":
            # older rows stored json.dumps(None) — the literal string "null" — for a missing vector,
            # which reads as a present embedding. Treat it as absent.
            return None
        if raw.lstrip().startswith("["):          # legacy JSON vector
            return json.loads(raw)
        a = _Vec("f")   # float32: an eighth of the memory of a list of floats
        a.frombytes(base64.b64decode(raw))
        a.norm = math.hypot(*a)
        return a

    def _vector(self, stored):
        """Decrypt + decode a stored embedding, cached by the SHA-256 of what is on disk.

        Every recall reloads the space and ranks every fact, so without this each call re-decrypted and
        re-parsed every vector (~180 us each). The key is the exact stored text, so an entry can never go
        stale: a re-embedded fact has a new ciphertext. Vectors are only ever replaced, never mutated in
        place, so sharing one across loads is safe."""
        if not stored:
            return None
        if self._vector_cap <= 0:
            return self._emb_decode(self._dec(stored))
        key = hashlib.sha256(stored.encode()).digest()
        vec = self._vectors.get(key)
        if vec is None:
            vec = self._emb_decode(self._dec(stored))
            with self._vectors_lock:      # tool calls run on worker threads
                if len(self._vectors) >= self._vector_cap:
                    self._vectors.pop(next(iter(self._vectors)))   # drop the oldest insert
                self._vectors[key] = vec
        return vec

    def _forget_plaintext(self):
        with self._vectors_lock:
            self._vectors.clear()  # embeddings are derived from the erased text too
        if self.crypter:
            self.crypter.forget()

    def _bi(self, subject):
        return self.crypter.blind(subject) if self.crypter else subject   # blind index for equality

    def _row_to_statement(self, row, light=False):
        (sid, slot, subj, val, scope, status, sup, conf, raw, emb, meta, source,
         created_at, head, relation, tail) = row
        slot, subj, val, raw, meta, source = (
            self._dec(slot), self._dec(subj), self._dec(val), self._dec(raw),
            self._dec(meta), self._dec(source))
        # entity names are personal data → encrypted at rest like subject/value
        head, relation, tail = self._dec(head), self._dec(relation), self._dec(tail)
        st = Statement(slot, subj, val, scope, status=Status(status),
                       confidence=conf or 1.0, supersedes=sup, raw=raw or "", source=source or "")
        st.id = sid
        if light:
            # `emb` here is a 0/1 existence flag from SQL, not ciphertext — never decrypt it
            st._emb = None
            st._has_emb = bool(emb)          # on disk, not loaded — hydrate only if we must rank
        else:
            st._emb = self._vector(emb)
            st._has_emb = st._emb is not None
        st.metadata = json.loads(meta) if meta else None
        st.created_at = created_at        # provenance / staleness
        st.head, st.relation, st.tail = head or "", relation or "", tail or ""
        return st

    def set_policy(self, user_id, policy, agent_id="", run_id=""):
        """Configure the source-aware reconciliation policy for a (shared) space. `policy` is a
        ReconcilePolicy or None (None clears it → default RECENCY)."""
        pj = json.dumps(policy.to_dict()) if policy else None
        self.db.execute(
            "INSERT INTO space_config(user_id, agent_id, run_id, policy, tenant_id) VALUES (?,?,?,?,?) "
            "ON CONFLICT(tenant_id, user_id, agent_id, run_id) DO UPDATE SET policy=excluded.policy",
            (user_id, agent_id, run_id, pj, self.tenant))
        self.db.commit()

    def get_policy(self, user_id, agent_id="", run_id=""):
        """Return the space's ReconcilePolicy (or None if unset → default RECENCY)."""
        from vayl.memory import orgmemory
        row = self.db.execute(
            "SELECT policy FROM space_config WHERE user_id=? AND agent_id=? AND run_id=? AND tenant_id=?",
            (user_id, agent_id, run_id, self.tenant)).fetchone()
        return orgmemory.ReconcilePolicy.from_dict(json.loads(row[0])) if row and row[0] else None

    def load(self, user_id, agent_id="", run_id=""):
        """HOT PATH: rebuild an LLMMemory from only the ACTIVE + FLAGGED rows (bounded). History
        stays on disk. Cost is O(active), not O(total history)."""
        m = LLMMemory(graph=self.graph, ns=self._ns(user_id, agent_id, run_id),
                      policy=self.get_policy(user_id, agent_id, run_id))
        rows = self.db.execute(
            f"SELECT {_COLS_LIGHT} FROM statements WHERE tenant_id=? AND user_id=? AND agent_id=? AND run_id=? "
            f"AND status IN ({','.join('?' * len(_HOT))}) ORDER BY id",
            (self.tenant, user_id, agent_id, run_id, *_HOT)).fetchall()
        for row in rows:
            m.statements.append(self._row_to_statement(row, light=True))
        # ranking (and only ranking) needs the vectors; hand the memory a way to fetch them lazily
        m._hydrate = lambda: self.hydrate_embeddings(user_id, m.statements, agent_id, run_id)
        # Reads — and only reads — may ask about the past ("what did we use before"). Retired rows
        # stay off the hot path, but query() can now pull them in on demand. Kept in a SEPARATE
        # pool rather than appended to m.statements: save() diffs against what load() returned, so
        # a history row in that list would look new and be re-INSERTed as a duplicate.
        m._hydrate_history = lambda: self.history_statements(user_id, agent_id, run_id)
        # id -> persisted state, so save() can diff instead of rewrite
        m._loaded = {s.id: (s.status.value, s.value) for s in m.statements}
        # seed the id counter from MAX(id) across ALL rows (incl. unloaded history) so new ids
        # never collide with a higher-id history row.
        maxid = self.db.execute(
            "SELECT COALESCE(MAX(id), 0) FROM statements WHERE tenant_id=? AND user_id=? AND agent_id=? AND run_id=?",
            (self.tenant, user_id, agent_id, run_id)).fetchone()[0]
        m._id_counter = itertools.count(maxid + 1)   # per-space, on THIS memory — no process-global
        return m

    def save(self, user_id, m, agent_id="", run_id=""):
        """INCREMENTAL: insert new statements, update the few that changed. Never rewrites history.

        Every statement is embedded, not only the active ones. That was not always true: while
        retired facts were unreachable on a read there was no point paying to embed them. Now that
        `query(include_history=True)` can reach them, an unembedded history row is retrievable only
        by keyword — second-class in exactly the searches ("what did we use before?") it exists to
        answer. Same for FLAGGED, which is in the hot set and was never embedded either.
        """
        loaded = getattr(m, "_loaded", {})   # {} for a fresh (never-loaded) LLMMemory → all inserts
        need = [s for s in m.statements
                if getattr(s, "_emb", None) is None and not getattr(s, "_has_emb", False)]
        if need:
            try:
                for s, v in zip(need, _embed([_embed_text(s) for s in need]), strict=True):
                    s._emb = v
            except Exception as e:
                log.warning("embedding unavailable (%s); recall degrades to lexical ranking", type(e).__name__)
        to_insert = [s for s in m.statements if s.id not in loaded]
        to_update = [s for s in m.statements if s.id in loaded and (s.status.value, s.value) != loaded[s.id]]
        # When a fact stopped being current, so the record can answer "what was true on date X".
        # Kept in metadata: no column, no migration. A row retired before its first save gets the save time.
        now = time.time()
        for s in to_insert + to_update:
            was_current = (loaded[s.id][0] == Status.ACTIVE.value if s.id in loaded
                           else s.status is Status.SUPERSEDED)    # an archived or rejected row never was
            if s.status in (Status.SUPERSEDED, Status.HISTORICAL) and was_current \
                    and not (s.metadata or {}).get("retired_at"):
                s.metadata = {**(s.metadata or {}), "retired_at": now}
        with self.db.transaction():   # inserts + updates land atomically, or not at all
            if to_insert:
                self.db.executemany(
                    "INSERT INTO statements "
                    "(user_id,id,slot,subject,value,scope,status,supersedes,confidence,raw,seq,embedding,agent_id,run_id,metadata,subject_hmac,created_at,source,tenant_id,head,relation,tail) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    [(user_id, s.id, self._enc(s.slot), self._enc(s.subject), self._enc(s.value), s.scope,
                      s.status.value, s.supersedes, s.confidence, self._enc(s.raw), s.id,
                      self._enc(self._emb_encode(getattr(s, "_emb", None))), agent_id, run_id,
                      self._enc(json.dumps(getattr(s, "metadata", None))), self._bi(s.subject), now,
                      self._enc(getattr(s, "source", "") or ""), self.tenant,
                      self._enc(getattr(s, "head", "") or ""), self._enc(getattr(s, "relation", "") or ""),
                      self._enc(getattr(s, "tail", "") or "")) for s in to_insert])
            for s in to_update:
                # only write the embedding when we actually hold one — otherwise an unloaded vector
                # would be overwritten with NULL and silently re-embedded on the next save
                sets = "status=?, value=?, supersedes=?, raw=?, metadata=?"
                params = [s.status.value, self._enc(s.value), s.supersedes, self._enc(s.raw),
                          self._enc(json.dumps(getattr(s, "metadata", None)))]
                if getattr(s, "_emb", None) is not None:
                    sets += ", embedding=?"
                    params.append(self._enc(self._emb_encode(s._emb)))
                self.db.execute(
                    f"UPDATE statements SET {sets} "
                    "WHERE tenant_id=? AND user_id=? AND agent_id=? AND run_id=? AND id=?",
                    (*params, self.tenant, user_id, agent_id, run_id, s.id))
        # refresh the snapshot so a second save() in the same session diffs correctly (no dup inserts)
        m._loaded = {s.id: (s.status.value, s.value) for s in m.statements}

    # ── cold reads: history is not in the hot set, so fetch it lazily and scoped ──────────────

    def get_row(self, user_id, memory_id, agent_id="", run_id=""):
        """One statement by id — active or historical (get_memory)."""
        row = self.db.execute(
            f"SELECT {_COLS} FROM statements WHERE tenant_id=? AND user_id=? AND agent_id=? AND run_id=? AND id=?",
            (self.tenant, user_id, agent_id, run_id, memory_id)).fetchone()
        return self._row_to_statement(row) if row else None

    def history_rows(self, user_id, subject, agent_id="", run_id=""):
        """Full change-log for a subject, oldest → newest (the history tool)."""
        rows = self.db.execute(
            f"SELECT {_COLS} FROM statements WHERE tenant_id=? AND user_id=? AND agent_id=? AND run_id=? AND subject_hmac=? ORDER BY id",
            (self.tenant, user_id, agent_id, run_id, self._bi(subject))).fetchall()
        return [self._row_to_statement(r) for r in rows]

    def subjects(self, user_id, agent_id="", run_id=""):
        """Distinct subjects in a space with record counts (for the compliance/erasure view).
        Groups by the blind index so it works whether or not the subject is encrypted."""
        rows = self.db.execute(
            "SELECT MAX(subject), COUNT(*), SUM(CASE WHEN status='ACTIVE' THEN 1 ELSE 0 END) "
            "FROM statements WHERE tenant_id=? AND user_id=? AND agent_id=? AND run_id=? GROUP BY subject_hmac",
            (self.tenant, user_id, agent_id, run_id)).fetchall()
        out = [{"subject": self._dec(subj), "records": n, "active": bool(a)} for subj, n, a in rows]
        return sorted(out, key=lambda r: r["subject"] or "")

    def history_statements(self, user_id, agent_id="", run_id=""):
        """The retired rows `load()` deliberately skips — SUPERSEDED and HISTORICAL — as Statements.

        `load()` reads only ACTIVE + FLAGGED so its cost is O(active) rather than O(all history ever
        written), which is right for the write path: reconciliation never consults history. It is
        wrong for a read that asks about the past, and until this existed those rows could not reach
        an answer at all — `query()` had a branch for them that could never fire on a persisted
        memory. Full columns (including the vector) so these rank alongside active facts.
        """
        rows = self.db.execute(
            f"SELECT {_COLS} FROM statements WHERE tenant_id=? AND user_id=? AND agent_id=? "
            f"AND run_id=? AND status NOT IN ({','.join('?' * len(_HOT))}) ORDER BY id",
            (self.tenant, user_id, agent_id, run_id, *_HOT)).fetchall()
        return [self._row_to_statement(r) for r in rows]

    def export(self, user_id, agent_id="", run_id=""):
        """Machine-readable dump of everything held for a space (active + full history) — GDPR
        portability / access (Art. 15/20). Decrypted, structured, oldest → newest."""
        rows = self.db.execute(
            f"SELECT {_COLS} FROM statements WHERE tenant_id=? AND user_id=? AND agent_id=? AND run_id=? ORDER BY id",
            (self.tenant, user_id, agent_id, run_id)).fetchall()
        out = []
        for r in rows:
            s = self._row_to_statement(r)
            out.append({"id": s.id, "subject": s.subject, "value": s.value, "scope": s.scope,
                        "status": s.status.value, "supersedes": s.supersedes,
                        "confidence": s.confidence, "metadata": s.metadata})
        return {"space": {"user_id": user_id, "agent_id": agent_id, "run_id": run_id},
                "count": len(out), "records": out}

    def expire(self, user_id, older_than_days, agent_id=None, run_id=None):
        """Retention / storage-limitation (Art. 5(1)(e)): HARD-delete rows written more than
        `older_than_days` ago. Scope to a space with agent_id/run_id, or a whole user without.
        Returns rows erased."""
        cutoff = time.time() - float(older_than_days) * 86400
        q = "DELETE FROM statements WHERE tenant_id=? AND user_id=? AND created_at IS NOT NULL AND created_at < ?"
        p = [self.tenant, user_id, cutoff]
        if agent_id is not None:
            q += " AND agent_id=?"; p.append(agent_id)
        if run_id is not None:
            q += " AND run_id=?"; p.append(run_id)
        cur = self.db.execute(q, p)
        self.db.commit()
        self._forget_plaintext()
        return cur.rowcount

    def cold_tail(self, user_id, agent_id="", run_id="", limit=20):
        """The most recent non-active rows (superseded / retracted / archived), newest first —
        a BOUNDED history slice for list_memories and recall context."""
        rows = self.db.execute(
            f"SELECT {_COLS} FROM statements WHERE tenant_id=? AND user_id=? AND agent_id=? AND run_id=? "
            f"AND status NOT IN ({','.join('?' * len(_HOT))}) ORDER BY id DESC LIMIT ?",
            (self.tenant, user_id, agent_id, run_id, *_HOT, int(limit))).fetchall()
        return [self._row_to_statement(r) for r in rows]

    def delete(self, user_id, subject, agent_id="", run_id=""):
        """HARD-erase every record of one subject in a memory space — including history/tombstones.
        This is real removal (GDPR erasure), not a RETRACT: nothing is retained. Returns rows erased."""
        self._purge_graph(ns=self._ns(user_id, agent_id, run_id), subject=subject)
        cur = self.db.execute(
            "DELETE FROM statements WHERE tenant_id=? AND user_id=? AND agent_id=? AND run_id=? AND subject_hmac=?",
            (self.tenant, user_id, agent_id, run_id, self._bi(subject)))
        self.db.commit()
        self._forget_plaintext()
        return cur.rowcount

    def delete_all(self, user_id, agent_id=None, run_id=None):
        """HARD-erase memory. With no agent_id/run_id, erases the ENTIRE user across all spaces
        (the account-deletion / GDPR semantic); pass them to erase just one space. Returns rows erased.
        (The optional graph is a single-tenant MVP projection, not scoped per-user here — see README.)"""
        q, p = "DELETE FROM statements WHERE tenant_id=? AND user_id=?", [self.tenant, user_id]
        if agent_id is not None:
            q += " AND agent_id=?"; p.append(agent_id)
        if run_id is not None:
            q += " AND run_id=?"; p.append(run_id)
        # graph first — prefix on user for a whole-user wipe, else the exact space
        if agent_id is None and run_id is None:
            self._purge_graph(ns_prefix=f"{self.tenant}\x1f{user_id}\x1f")
        else:
            self._purge_graph(ns=self._ns(user_id, agent_id or "", run_id or ""))
        cur = self.db.execute(q, p)
        self.db.commit()
        self._forget_plaintext()
        return cur.rowcount

    def _purge_graph(self, **where):
        """Erase the matching graph edges BEFORE the store rows, and fail closed. Swallowing a failure
        here would let an erasure "succeed" (and get a signed receipt) while the subject's data lived on
        in the graph. Purging first means a failure leaves nothing erased, so a retry is clean; the graph
        is a projection of the store, so edges removed ahead of a failed store delete can be rebuilt."""
        if not self.graph:
            return
        try:
            self.graph.delete_edges(**where)
        except Exception as e:
            raise RuntimeError("erasure aborted: graph purge failed and nothing was erased — "
                               "retry once the graph is reachable") from e

    def users(self):
        return [r[0] for r in self.db.execute(
            "SELECT DISTINCT user_id FROM statements WHERE tenant_id=?", (self.tenant,))]

    def hydrate_embeddings(self, user_id, statements, agent_id="", run_id=""):
        """Fetch vectors for statements that have one on disk but not in memory.

        load() deliberately leaves embeddings behind; this pulls back only what a ranking pass needs.
        Returns the number hydrated."""
        want = [s for s in statements
                if getattr(s, "_emb", None) is None and getattr(s, "_has_emb", False)]
        if not want:
            return 0
        ids = [s.id for s in want]
        rows = self.db.execute(
            f"SELECT id, embedding FROM statements WHERE tenant_id=? AND user_id=? AND agent_id=? "
            f"AND run_id=? AND id IN ({','.join('?' * len(ids))})",
            (self.tenant, user_id, agent_id, run_id, *ids)).fetchall()
        by_id = {r[0]: r[1] for r in rows}
        for s in want:
            s._emb = self._vector(by_id.get(s.id))
        return len(want)

    def reproject_graph(self, wipe=True):
        """Rebuild the Neo4j projection from the store, which is the source of truth.

        This is what makes the graph a genuine *projection* rather than an unrecoverable side-store:
        the triple (head, relation, tail) is persisted alongside each fact, so the graph can be
        dropped, migrated, or lost and rebuilt without re-running the extractor. Only ACTIVE facts
        are replayed — superseded and retracted ones are precisely what must NOT come back.

        Covers the CURRENT tenant only: `wipe` clears that tenant's edges, never another tenant's (it
        used to wipe the whole graph and replay one tenant). reproject_all_graphs() rebuilds everyone.

        Returns the number of edges written. No-op (0) when no graph is attached."""
        if not self.graph:
            return 0
        if wipe:
            self.graph.delete_edges(ns_prefix=f"{self.tenant}\x1f")
        rows = self.db.execute(
            "SELECT user_id, agent_id, run_id, head, relation, tail, subject FROM statements "
            "WHERE tenant_id=? AND status=? ORDER BY id", (self.tenant, Status.ACTIVE.value))
        written = 0
        for user_id, agent_id, run_id, head, relation, tail, subject in rows:
            head, relation, tail = self._dec(head), self._dec(relation), self._dec(tail)
            if not (head and relation and tail):
                continue                      # fact carries no triple (pre-migration or non-relational)
            self.graph.add_edge(head, relation, tail,
                                ns=self._ns(user_id, agent_id or "", run_id or ""),
                                subject=self._dec(subject) or "")
            written += 1
        return written

    def reproject_all_graphs(self):
        """Wipe the whole graph and replay every tenant's active facts. The upgrade path when the
        namespace format changes (0.7 put the tenant in it), and the way to clear edges left behind
        under an old namespace. Returns edges written; 0 without a graph."""
        if not self.graph:
            return 0
        self.graph.wipe()
        written = 0
        for (tenant,) in self.db.execute("SELECT DISTINCT tenant_id FROM statements").fetchall():
            token = bind_tenant(tenant or self._default_tenant)
            try:
                written += self.reproject_graph(wipe=False)
            finally:
                reset_tenant(token)
        return written
