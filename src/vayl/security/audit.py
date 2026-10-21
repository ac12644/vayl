#!/usr/bin/env python3
"""
Append-only, tamper-evident audit log for Vayl — accountability (GDPR Art. 5(2)) and breach
investigation, upgraded into a cryptographic record of what the system did.

Records every data operation (who / what / when): the space (user/agent/run), the action, and a
short detail. Stored in the same SQLite DB in its own table; it is NOT wiped by memory erasure
(`delete_all` clears facts, not the accountability trail) — retain/rotate it per your own policy.

TAMPER-EVIDENCE (F0.2 — the substrate for verifiable memory):
Each row is hash-chained to its predecessor — `entry_hash = sha256(prev_hash | canonical(row))` —
so editing, deleting, or reordering any row breaks the chain and `verify_chain()` pinpoints where.
When a signer is attached, each entry_hash is also Ed25519-signed, so a third party can verify the
log's integrity WITHOUT the secret key. This is what lets Vayl prove "this is what was known, and
when" rather than merely assert it.

TENANTS: there is ONE chain per deployment, interleaved across tenants. Each row is stamped with the
request's tenant (`tenant_id`) so listings can be confined to it, but tenant_id is a FILTER column only:
it is not part of the chain hash (adding it would break verification of every chain written before),
so verify_chain() and purge() stay deployment-wide.
"""
import datetime
import hashlib
import json

from vayl.storage.db import ensure
from vayl.storage.migrations import migrate
from vayl.storage.store import current_tenant

GENESIS = "0" * 64   # prev_hash of the first chained row (no predecessor)
_SIG_TAG = "vayl.audit.v1|"        # domain tag on each entry signature (no cross-artifact replay)
_HEAD_TAG = "vayl.audit.head.v1|"  # domain tag on the signed head checkpoint (truncation detection)
_AUDIT_LOCK_KEY = "__vayl_audit_chain__"   # advisory-lock key serializing the append (cross-process)


class Audit:
    def __init__(self, db, crypter=None, signer=None):
        self.db = ensure(db)
        self.crypter = crypter   # when set, detail (may hold personal data) is encrypted at rest
        self.signer = signer     # when set, each entry_hash is Ed25519-signed
        # The chain append (read the head hash -> compute -> insert) MUST be serialized: two
        # concurrent appends would both chain to the same head and FORK the chain, which verify_chain
        # then flags as tampering. We serialize on db.space_lock(_AUDIT_LOCK_KEY) — an in-process lock
        # on SQLite, and a cross-process pg_advisory_xact_lock on Postgres, so the chain stays intact
        # even with many vayl-server processes sharing one database (a plain threading.Lock could not).
        migrate(self.db)

    def _anchor(self):
        row = self.db.execute("SELECT value, signature FROM audit_meta WHERE key='anchor'").fetchone()
        return (row[0], row[1]) if row and row[0] else (None, None)

    @staticmethod
    def _canonical(ts, user_id, agent_id, run_id, action, stored_detail):
        """Deterministic, UNAMBIGUOUS serialization of a row's content for hashing. Uses the STORED
        detail (ciphertext when encryption is on), so the hash covers exactly what sits on disk — any
        edit to the stored bytes is detected, encrypted or not.

        Encoded as a JSON array rather than a `|`-join: a delimiter join over unescaped fields is
        ambiguous (`"a|b","c"` and `"a","b|c"` join to the same string), which would let an attacker
        with DB write shift a field boundary and keep the hash — and signature — valid while changing
        who/what a row attributes. JSON escapes the separators, so each field's bounds are exact."""
        return json.dumps([ts, user_id or "", agent_id or "", run_id or "",
                           action or "", stored_detail or ""], separators=(",", ":"))

    def _head_hash(self):
        """entry_hash of the most recent chained row; falls back to the retention anchor (if rows
        were purged) and only then to GENESIS — so the chain stays continuous across purges."""
        row = self.db.execute(
            "SELECT entry_hash FROM audit WHERE entry_hash IS NOT NULL ORDER BY seq DESC LIMIT 1").fetchone()
        if row and row[0]:
            return row[0]
        anchor, _sig = self._anchor()
        return anchor or GENESIS

    def record(self, action, user_id="", agent_id="", run_id="", detail=""):
        ts = datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds")
        detail = str(detail)[:200]
        stored = self.crypter.enc(detail) if self.crypter else detail   # detail may hold personal data
        with self.db.space_lock(_AUDIT_LOCK_KEY):    # serialize read-head -> compute -> insert (cross-process)
            prev = self._head_hash()
            entry = hashlib.sha256(
                (prev + "|" + self._canonical(ts, user_id, agent_id, run_id, action, stored)).encode()).hexdigest()
            signature = self.signer.sign(_SIG_TAG + entry) if self.signer else None
            # tenant_id is stored beside the row, outside the hashed content (see the module docstring)
            self.db.execute(
                "INSERT INTO audit(ts, user_id, agent_id, run_id, action, detail, prev_hash, entry_hash, "
                "signature, tenant_id) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (ts, user_id, agent_id, run_id, action, stored, prev, entry, signature, current_tenant()))
            self._write_head_checkpoint(entry)       # signed (count-free) head → detects tail truncation
            self.db.commit()
        return entry   # the chain head after this write — callers can anchor a receipt to it

    def _write_head_checkpoint(self, head):
        """Persist a SIGNED commitment to the current chain head. verify_chain() later asserts the
        actual head still matches it, so deleting the newest rows (tail truncation) — which leaves a
        shorter but internally-consistent chain — is detected. Only meaningful with a signer: an
        attacker without the key can't forge a new checkpoint, only delete it (itself an anomaly on a
        signed deployment). No-op when unsigned."""
        if not self.signer:
            return
        self.db.execute(
            "INSERT INTO audit_meta(key, value, signature) VALUES('head',?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, signature=excluded.signature",
            (head, self.signer.sign(_HEAD_TAG + head)))

    def _detail(self, d):
        if self.crypter and d:
            try:
                return self.crypter.dec(d)
            except Exception:
                return "(undecryptable — key changed?)"
        return d

    def tail(self, limit=50, user_id=None, all_tenants=False):
        """Newest entries of the current tenant (optionally one user_id's). `all_tenants` lists across
        every tenant — for the deployment operator only; the caller decides who that is."""
        conds, p = [], []
        if not all_tenants:
            conds.append("tenant_id=?"); p.append(current_tenant())
        if user_id is not None:
            conds.append("user_id=?"); p.append(user_id)
        q = "SELECT ts, user_id, agent_id, run_id, action, detail, tenant_id FROM audit"
        if conds:
            q += " WHERE " + " AND ".join(conds)
        q += " ORDER BY seq DESC LIMIT ?"; p.append(int(limit))
        return [{"ts": r[0], "user_id": r[1], "agent_id": r[2], "run_id": r[3],
                 "action": r[4], "detail": self._detail(r[5]), "tenant": r[6]}
                for r in self.db.execute(q, p).fetchall()]

    def head_hash(self):
        """Public accessor for the current chain head — the tamper-evident 'state hash' of the log."""
        return self._head_hash()

    def purge(self, older_than_days):
        """Retention (Art. 5(1)(e)) without giving up tamper-evidence: hard-delete rows older than
        N days from the HEAD of the chain, and checkpoint the newest purged entry_hash as a SIGNED
        anchor that verify_chain() and new records resume from. Contiguity is required for the chain
        to stay verifiable, so everything up to the newest qualifying row is purged. Deployment-wide.
        Returns rows purged."""
        cutoff = (datetime.datetime.now(datetime.UTC)
                  - datetime.timedelta(days=float(older_than_days))).isoformat(timespec="seconds")
        rows = self.db.execute(
            "SELECT seq, entry_hash FROM audit WHERE ts <= ? ORDER BY seq", (cutoff,)).fetchall()
        if not rows:
            return 0
        last_hash = next((h for _seq, h in reversed(rows) if h), None)
        self.db.execute("DELETE FROM audit WHERE seq <= ?", (rows[-1][0],))
        if last_hash:
            sig = self.signer.sign("anchor|" + last_hash) if self.signer else None
            self.db.execute(
                "INSERT INTO audit_meta(key, value, signature) VALUES('anchor',?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value, signature=excluded.signature",
                (last_hash, sig))
        # a purge that removed the newest rows changes the head; re-sign the checkpoint to the new
        # head so a legitimate retention purge isn't mistaken for truncation.
        self._write_head_checkpoint(self._head_hash())
        self.db.commit()
        return len(rows)

    def verify_chain(self):
        """Recompute the hash chain over stored rows. Any content edit, deletion, or reorder of a
        chained row breaks the recomputed hash (and, if signed, the signature); on a signed log,
        deleting the newest rows (tail truncation) is caught by the signed head checkpoint. Returns a
        report: {ok, broken_at, reason, verified, legacy, total, head_checkpoint}. `ok` is True only
        if every chained row verifies, links to its predecessor, and the head matches the checkpoint.
        Pre-chain (legacy) rows are counted, not verified."""
        rows = self.db.execute(
            "SELECT seq, ts, user_id, agent_id, run_id, action, detail, prev_hash, entry_hash, signature "
            "FROM audit ORDER BY seq").fetchall()
        chained = [r for r in rows if r[8]]           # rows carrying an entry_hash
        legacy = len(rows) - len(chained)
        base = {"verified": 0, "legacy": legacy, "total": len(rows), "head_checkpoint": "n/a"}
        prev = GENESIS
        anchor, anchor_sig = self._anchor()
        if anchor:
            if self.signer and not (anchor_sig and self.signer.verify_own(anchor_sig, "anchor|" + anchor)):
                return {"ok": False, "broken_at": 0, "reason": "retention anchor signature invalid", **base}
            prev = anchor
        for seq, ts, uid, aid, rid, action, detail, prev_hash, entry_hash, signature in chained:
            if prev_hash != prev:
                return {"ok": False, "broken_at": seq, "reason": "chain link mismatch "
                        "(a row was deleted, reordered, or inserted)", **base}
            recomputed = hashlib.sha256(
                (prev_hash + "|" + self._canonical(ts, uid, aid, rid, action, detail)).encode()).hexdigest()
            if recomputed != entry_hash:
                return {"ok": False, "broken_at": seq, "reason": "content tampered "
                        "(a row's stored bytes were altered)", **base}
            if self.signer and signature and not self.signer.verify_own(signature, _SIG_TAG + entry_hash):
                return {"ok": False, "broken_at": seq, "reason": "invalid signature", **base}
            prev = entry_hash
        # Tail-truncation check: on a signed log, `prev` is now the actual head; it must match the
        # signed checkpoint. A shorter, internally-consistent chain (newest rows deleted) fails here.
        cp_status = "n/a"
        if self.signer:
            cp = self.db.execute("SELECT value, signature FROM audit_meta WHERE key='head'").fetchone()
            if cp and cp[0]:
                if not (cp[1] and self.signer.verify_own(cp[1], _HEAD_TAG + cp[0])):
                    return {"ok": False, "broken_at": None, "reason": "head checkpoint signature invalid",
                            **{**base, "head_checkpoint": "invalid"}}
                if cp[0] != prev:
                    return {"ok": False, "broken_at": None, "reason": "log truncated or rolled back "
                            "(head is behind the last signed checkpoint)",
                            **{**base, "head_checkpoint": "mismatch"}}
                cp_status = "verified"
            else:
                cp_status = "missing"   # signed deployment with no checkpoint — legacy, or one was deleted
        return {"ok": True, "broken_at": None, "reason": "intact",
                **{**base, "verified": len(chained), "head_checkpoint": cp_status}}
