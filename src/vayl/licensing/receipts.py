#!/usr/bin/env python3
"""
Verifiable memory (Feature 3) — cryptographic proof of what was erased and what was known.

Two signed artifacts, both Ed25519-signed over a canonical JSON so a THIRD PARTY can verify with only
the public key — no access to the database or the secret:

  • erasure receipt      — proves a delete happened: {action, scope, subject, count, chain_hash, ts}.
                           The GDPR "prove you erased my data" artifact.
  • knowledge attestation — proves a fact was held at a time: {subject, value, chain_hash, ts}.
                           The "prove what was known, and when" artifact.

Each artifact carries `chain_hash` — the tamper-evident audit-log head at the moment it was issued —
so it's anchored to the history that recorded the event, not floating free.

`verify()` RECOMPUTES the canonical body from the payload before checking the signature, so editing
any field (a count, a subject) invalidates the proof. Never trust a stored `body`; always recompute.

Persisted receipts are encrypted at rest (they can reference erased subjects) but returned in the
clear so they stay third-party-verifiable — the signature commits to the plaintext either way.
"""
import datetime
import json


def _canonical(payload):
    """Stable serialization the signature commits to (sorted keys, no incidental whitespace)."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _artifact(signer, payload):
    body = _canonical(payload)
    return {"payload": payload, "signature": signer.sign(body) if signer else None,
            "public_key": signer.public_key_hex() if signer else None}


def make_receipt(signer, action, scope, subject, count, chain_hash, ts=None):
    ts = ts or datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds")
    return _artifact(signer, {"kind": "erasure_receipt", "action": action, "scope": scope,
                              "subject": subject, "count": count, "chain_hash": chain_hash, "ts": ts})


def make_attestation(signer, subject, value, chain_hash, ts=None, scope=""):
    ts = ts or datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds")
    # `scope` (the owning "user_id/agent_id/run_id") is part of the SIGNED body and lets the server
    # confine verify_receipt to the receipt's tenant — an attestation with no scope is unscoped.
    return _artifact(signer, {"kind": "knowledge_attestation", "subject": subject, "value": value,
                              "scope": scope, "chain_hash": chain_hash, "ts": ts})


def verify(receipt, public_key=None):
    """Third-party verification. Recomputes the canonical body from the payload and checks the Ed25519
    signature against `public_key` (or the receipt's own). Returns True only if the payload is
    untouched and the signature is valid. Any edit to the payload → False."""
    from vayl.security.crypto import Signer
    if not receipt or not receipt.get("signature"):
        return False
    pk = public_key or receipt.get("public_key")
    if not pk:
        return False
    return Signer.verify(pk, receipt["signature"], _canonical(receipt["payload"]))


def _tenant():
    # lazy, like the storage imports in Receipts.__init__
    from vayl.storage.store import current_tenant
    return current_tenant()


class Receipts:
    """Persistence for issued receipts/attestations. Payload encrypted at rest; returned decrypted so
    it stays verifiable. Not wiped by memory erasure — it IS the proof that erasure happened. Rows are
    stamped with the request's tenant; lookups, listings and purges only see that tenant's."""
    def __init__(self, db, crypter=None, signer=None):
        from vayl.storage.db import ensure
        from vayl.storage.migrations import migrate
        self.db = ensure(db)
        self.crypter = crypter
        self.signer = signer
        migrate(self.db)

    def _enc(self, s):
        return self.crypter.enc(s) if self.crypter else s

    def _dec(self, s):
        if self.crypter and s:
            try:
                return self.crypter.dec(s)
            except Exception:
                return None
        return s

    def save(self, receipt):
        p = receipt["payload"]
        rid = self.db.insert_returning(
            "INSERT INTO receipts(ts, kind, payload, signature, public_key, tenant_id) VALUES (?,?,?,?,?,?)",
            (p.get("ts"), p.get("kind"), self._enc(_canonical(p)), receipt["signature"], receipt["public_key"],
             _tenant()))
        self.db.commit()
        return rid

    def get(self, receipt_id):
        row = self.db.execute(
            "SELECT id, payload, signature, public_key FROM receipts WHERE id=? AND tenant_id=?",
            (int(receipt_id), _tenant())).fetchone()
        if not row:
            return None
        payload = self._dec(row[1])
        try:
            payload = json.loads(payload) if payload else {}
        except Exception:
            payload = {}
        return {"id": row[0], "payload": payload, "signature": row[2], "public_key": row[3]}

    def list(self, limit=50):
        rows = self.db.execute(
            "SELECT id, ts, kind FROM receipts WHERE tenant_id=? ORDER BY id DESC LIMIT ?",
            (_tenant(), int(limit))).fetchall()
        return [{"id": r[0], "ts": r[1], "kind": r[2]} for r in rows]

    def for_user(self, user_id, limit=1000):
        """All receipts whose scope belongs to `user_id` — the data-subject-access (Art. 15) view.
        Scope is inside the encrypted payload, so this decrypts and filters."""
        out = []
        for r in self.db.execute("SELECT id FROM receipts WHERE tenant_id=? ORDER BY id DESC LIMIT ?",
                                 (_tenant(), int(limit))).fetchall():
            rec = self.get(r[0])
            scope = ((rec or {}).get("payload") or {}).get("scope") or ""
            if scope == user_id or scope.startswith(user_id + "/"):
                out.append(rec)
        return out

    def purge(self, older_than_days):
        """Retention (Art. 5(1)(e)): hard-delete this tenant's receipts older than N days."""
        cutoff = (datetime.datetime.now(datetime.UTC)
                  - datetime.timedelta(days=float(older_than_days))).isoformat(timespec="seconds")
        cur = self.db.execute("DELETE FROM receipts WHERE ts <= ? AND tenant_id=?", (cutoff, _tenant()))
        self.db.commit()
        return cur.rowcount
