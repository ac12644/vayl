"""Capture a real request/response example for every tool, for the OpenAPI spec.

Drives all 32 tools through vayl-server's REST routes (in process, real model) in one scenario: a
fictional patient on the clinical preset. Writes openapi/examples.json, which openapi/generate.py reads.
Run from the repo root with a model configured, e.g.:

    OPENAI_API_KEY=... LLM_PROVIDER=openai OPENAI_MODEL=gpt-6-luna python openapi/capture_examples.py
"""
import json
import os
import re
import sqlite3
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
os.environ.update(VAYL_DB=os.path.join(tempfile.mkdtemp(), "vayl.db"), VAYL_SLOT_SCHEMA="preset:clinical",
                  VAYL_CRITICAL_CATEGORIES="critical", VAYL_LOG_LEVEL="ERROR")

from starlette.testclient import TestClient  # noqa: E402

from vayl.api import mcp_server  # noqa: E402
from vayl.api import server as srv  # noqa: E402
from vayl.auth.auth import Auth, Role  # noqa: E402

P = "pt_4471"
KEY = re.compile(r"vayl_sk_[A-Za-z0-9_-]+")
examples = {}


NOT_GATED = 3   # exit code of a pass whose medication switch wasn't held for approval


def main():
    # The model doesn't always route "warfarin stopped, apixaban started" through approval (it can ADD
    # apixaban to the multi-valued slot and archive the stop instead). Each pass runs in a child
    # process on a fresh database; a pass that wasn't gated is retried and reported, because the
    # variance is a real finding, not noise.
    import subprocess
    for attempt in range(1, 6):
        code = subprocess.call([sys.executable, os.path.abspath(__file__), "--once"])
        if code == 0:
            return
        if code != NOT_GATED:
            sys.exit(code)
        print(f"attempt {attempt}: the switch was not held for approval; retrying on a fresh db", file=sys.stderr)
    sys.exit("the switch was never held for approval in 5 attempts")


def once():
    if not scenario():
        sys.exit(NOT_GATED)
    missing = sorted(set(t.name for t in _tools()) - set(examples))
    if missing:
        sys.exit(f"no example captured for: {missing}")
    with open(os.path.join(HERE, "examples.json"), "w") as f:
        json.dump(dict(sorted(examples.items())), f, indent=2, ensure_ascii=False)
        f.write("\n")
    print(f"wrote {len(examples)} examples", file=sys.stderr)


def scenario():
    """One pass of the scenario on a fresh database. False if the medication switch wasn't gated."""
    mcp_server._AUTH_REQUIRED = True
    auth = Auth(sqlite3.connect(":memory:", check_same_thread=False))
    _p, key = auth.create("ops", roles=Role.ADMIN)
    mcp_server._auth = auth            # create/list/revoke_principal act on the same store
    app = srv.build_app(mcp_server.mcp.http_app(transport="http", stateless_http=True,
                                                **mcp_server._transport_security()),
                        auth, mcp_server._store)
    with TestClient(app) as c:
        def call(tool, **args):
            r = c.post(f"/v1/tools/{tool}", json=args, headers={"Authorization": f"Bearer {key}"})
            body = r.json()
            text = body.get("result", "")
            if r.status_code != 200:
                sys.exit(f"{tool} returned {r.status_code}: {body}")
            if tool not in examples:      # the first call of a tool is its example
                examples[tool] = {"request": args, "status": r.status_code,
                                  "response": {"result": KEY.sub("vayl_sk_…", text)}}
            print(f"{tool}: {r.status_code} {text[:90]!r}", file=sys.stderr)
            return text

        def first_id(text, pattern):
            m = re.search(pattern, text)
            if not m:
                sys.exit(f"no id matching {pattern!r} in: {text}")
            return int(m.group(1))

        call("remember", text="Patient is on warfarin 5 mg daily for atrial fibrillation.", user_id=P)
        call("remember", text="Allergic to penicillin, causes hives.", user_id=P)
        call("remember", text="Warfarin stopped today. Started apixaban 5 mg twice daily.", user_id=P)
        pending = call("pending_changes", user_id=P)
        if not re.search(r"#\d+ REPLACE", pending):
            return False
        call("confirm_change", memory_id=first_id(pending, r"#(\d+) REPLACE"), user_id=P,
             decided_by="Dr. Okafor, verified with pharmacy")
        call("recall", question="What anticoagulant is the patient on?", user_id=P)
        call("recall", question="What anticoagulant was the patient on before?", user_id=P, include_history=True)
        call("safe_recall", question="Can we give amoxicillin?", user_id=P)
        call("check_before_act", subject="active_medication", user_id=P)
        call("history", subject="active_medication", user_id=P)
        listing = call("list_memories", user_id=P)
        call("get_memory", memory_id=first_id(listing, r"apixaban[^\n]*\(#(\d+)\)"), user_id=P)
        lead = call("remember", text="Dr. Okafor is the attending physician.", user_id=P)
        listing = call("list_memories", user_id=P)
        call("update_memory", memory_id=first_id(listing, r"care_team_lead = [^\n]*\(#(\d+)\)"),
             new_value="Dr. Lindqvist", user_id=P)
        call("forget", text="Dr. Lindqvist is no longer on this patient's care team.", user_id=P)
        stop = call("remember", text="Stop the apixaban.", user_id=P)
        pending = call("pending_changes", user_id=P)
        ids = re.findall(r"#(\d+) ", pending)
        if ids:
            call("reject_change", memory_id=int(ids[0]), user_id=P, decided_by="Dr. Okafor: keep apixaban")
        decision = call("record_decision", action_summary="Held amoxicillin and prescribed azithromycin.",
                        question="Can we give amoxicillin?", user_id=P)
        call("explain_decision", decision_id=first_id(decision, r"#(\d+)"), user_id=P)
        attest = call("attest", subject="allergy", user_id=P)
        call("verify_receipt", receipt_id=first_id(attest, r"#(\d+)"))
        call("audit_log", limit=5, user_id=P)
        call("verify_audit")
        call("export_public_key")
        call("recall_related", question="Who is responsible for this patient's care?", user_id=P)
        call("get_reconcile_policy", user_id=P)
        call("set_reconcile_policy", mode="AUTHORITY", authority={"pharmacy": 3, "clinician": 2, "ehr": 1}, user_id=P)
        call("export_memory", user_id=P)
        call("delete", subject="primary_diagnosis", user_id=P)
        call("purge_expired", older_than_days=3650, user_id=P)
        call("remember", text="Patient prefers morning appointments.", user_id="pt_9001")
        call("delete_all", user_id="pt_9001")
        created = call("create_principal", name="ward-7-assistant", role="agent", scopes=P)
        call("list_principals")
        call("revoke_principal", principal_id=re.search(r"(prin_[0-9a-f]+)", created).group(1))
        call("license_status")
        call("stats")
        call("health")
        del lead, stop
    return True


def _tools():
    import asyncio
    return asyncio.run(mcp_server.mcp.list_tools())


if __name__ == "__main__":
    once() if "--once" in sys.argv else main()
