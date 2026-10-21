#!/usr/bin/env python3
"""
Decision audit for Vayl (Feature 1) — the queryable record of WHY an agent acted.

When an agent takes an action, it records a decision: the action summary plus a SNAPSHOT of the exact
beliefs it consulted — each fact's value, who asserted it, its confidence, and what it superseded.

Because facts change, the snapshot is IMMUTABLE: it records what was believed at decision time, so
"why did the agent do X?" is answerable even after those facts are later superseded, retracted, or
erased. Each decision is content-hashed and Ed25519-signed (when a signer is attached), so the record
itself is tamper-evident — a decision receipt, not just a log line.

Stored in the same SQLite DB; summary + snapshot are encrypted at rest when encryption is on. Rows are
stamped with the request's tenant and every read, redaction and purge is confined to it.
"""
import datetime
import hashlib
import json

from vayl.storage.db import ensure
from vayl.storage.migrations import migrate
from vayl.storage.store import current_tenant

REDACTED = "[redacted — Art. 17 erasure]"


class Decisions:
    def __init__(self, db, crypter=None, signer=None):
        self.db = ensure(db)
        self.crypter = crypter
        self.signer = signer
        migrate(self.db)

    @staticmethod
    def _digest(ts, user_id, agent_id, run_id, summary, snap, anchor):
        """The content hash a decision's signature commits to — recomputed on read so any edit to
        the stored summary/snapshot/anchor is detected (the digest no longer matches, and the
        signature no longer verifies)."""
        return hashlib.sha256(
            "|".join([ts, user_id or "", agent_id or "", run_id or "",
                      summary or "", snap or "", anchor or ""]).encode()).hexdigest()

    def _enc(self, s):
        return self.crypter.enc(s) if self.crypter else s

    def _dec(self, s):
        if self.crypter and s:
            try:
                return self.crypter.dec(s)
            except Exception:
                return None
        return s

    def record(self, summary, beliefs, user_id="", agent_id="", run_id="", anchor=""):
        """Snapshot `beliefs` (a list of provenance dicts, e.g. from query(with_provenance=True))
        behind a decision `summary`. `anchor` optionally binds this decision to the audit-chain head
        at decision time. Returns (decision_id, entry_hash — the signed receipt digest)."""
        ts = datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds")
        summary = str(summary)[:500]
        snap = json.dumps(beliefs, sort_keys=True, default=str)
        digest = self._digest(ts, user_id, agent_id, run_id, summary, snap, anchor)
        signature = self.signer.sign(digest) if self.signer else None
        did = self.db.insert_returning(
            "INSERT INTO decisions(ts, user_id, agent_id, run_id, summary, snapshot, anchor, entry_hash, "
            "signature, tenant_id) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (ts, user_id, agent_id, run_id, self._enc(summary), self._enc(snap), anchor, digest, signature,
             current_tenant()))
        self.db.commit()
        return did, digest

    def get(self, decision_id, user_id=None):
        """Reconstruct one decision: its summary, the immutable belief snapshot, and whether the
        signed receipt still verifies. Returns None if not found (or not in this user's scope)."""
        q = ("SELECT id, ts, user_id, agent_id, run_id, summary, snapshot, anchor, entry_hash, signature "
             "FROM decisions WHERE id=? AND tenant_id=?")
        p = [int(decision_id), current_tenant()]
        if user_id is not None:
            q += " AND user_id=?"; p.append(user_id)
        row = self.db.execute(q, p).fetchone()
        if not row:
            return None
        did, ts, uid, aid, rid, summary, snapshot, anchor, entry_hash, signature = row
        summary_pt, snapshot_pt = self._dec(summary), self._dec(snapshot)
        try:
            beliefs = json.loads(snapshot_pt) if snapshot_pt else []
        except Exception:
            beliefs = []
        # Recompute the digest from decrypted content, then confirm the signature commits to it —
        # both must hold. Catches any edit to summary/snapshot/anchor.
        recomputed = self._digest(ts, uid, aid, rid, summary_pt or "",
                                  json.dumps(beliefs, sort_keys=True, default=str), anchor)
        content_ok = (recomputed == entry_hash)
        if self.signer and signature:
            verified = content_ok and self.signer.verify_own(signature, entry_hash)
        else:
            verified = None   # unsigned: hash lives in the same DB, so it's not cryptographic proof
        return {"id": did, "ts": ts, "user_id": uid, "agent_id": aid, "run_id": rid,
                "summary": summary_pt, "beliefs": beliefs, "entry_hash": entry_hash, "anchor": anchor,
                "signed": signature is not None, "verified": verified}

    def redact(self, user_id, subject=None, agent_id=None, run_id=None):
        """Erasure follow-through (GDPR Art. 17): scrub belief VALUES from this user's decision
        snapshots — all of them, or only those for `subject`. The decision rows and summaries stay
        (the accountability record of WHAT was decided), but the erased personal value is gone.
        Each modified decision is re-signed over its redacted content, so verification still passes
        and the belief carries an explicit `redacted` marker. Returns decisions modified."""
        q = ("SELECT id, ts, user_id, agent_id, run_id, summary, snapshot, anchor "
             "FROM decisions WHERE user_id=? AND tenant_id=?")
        p = [user_id, current_tenant()]
        if agent_id is not None:
            q += " AND agent_id=?"; p.append(agent_id)
        if run_id is not None:
            q += " AND run_id=?"; p.append(run_id)
        n = 0
        for did, ts, uid, aid, rid, summary, snapshot, anchor in self.db.execute(q, p).fetchall():
            snap_pt = self._dec(snapshot)
            try:
                beliefs = json.loads(snap_pt) if snap_pt else []
            except Exception:
                continue
            changed = False
            for b in beliefs:
                if not isinstance(b, dict):
                    continue
                if subject is not None and b.get("subject") != subject:
                    continue
                if b.get("value") != REDACTED:
                    b["value"] = REDACTED
                    b["redacted"] = True
                    changed = True
            if not changed:
                continue
            summary_pt = self._dec(summary) or ""
            snap_new = json.dumps(beliefs, sort_keys=True, default=str)
            digest = self._digest(ts, uid, aid, rid, summary_pt, snap_new, anchor)
            sig = self.signer.sign(digest) if self.signer else None
            self.db.execute("UPDATE decisions SET snapshot=?, entry_hash=?, signature=? WHERE id=?",
                            (self._enc(snap_new), digest, sig, did))
            n += 1
        self.db.commit()
        return n

    def purge(self, older_than_days):
        """Retention (Art. 5(1)(e)): hard-delete this tenant's decisions older than N days."""
        cutoff = (datetime.datetime.now(datetime.UTC)
                  - datetime.timedelta(days=float(older_than_days))).isoformat(timespec="seconds")
        cur = self.db.execute("DELETE FROM decisions WHERE ts <= ? AND tenant_id=?", (cutoff, current_tenant()))
        self.db.commit()
        return cur.rowcount

    def list(self, user_id=None, agent_id=None, run_id=None, limit=50):
        """Recent decisions for a space (newest first) — id, timestamp, summary."""
        conds, p = ["tenant_id=?"], [current_tenant()]
        for col, val in (("user_id", user_id), ("agent_id", agent_id), ("run_id", run_id)):
            if val is not None:
                conds.append(f"{col}=?"); p.append(val)
        q = "SELECT id, ts, summary FROM decisions WHERE " + " AND ".join(conds)
        q += " ORDER BY id DESC LIMIT ?"; p.append(int(limit))
        return [{"id": r[0], "ts": r[1], "summary": self._dec(r[2])}
                for r in self.db.execute(q, p).fetchall()]
