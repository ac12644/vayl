"""The README demo: a fictional patient's anticoagulant changes, the change waits for a clinician,
and recall answers from the current value. Runs against a real model, so it needs the same env as
vayl-mcp (OPENAI_API_KEY, LLM_PROVIDER=openai, OPENAI_MODEL). Recorded with `vhs assets/demo/demo.tape`."""
import os
import re
import sys
import tempfile
import time

from vayl import Vayl

DIM, BOLD, CYAN, GREEN, RESET = "\033[2m", "\033[1m", "\033[36m", "\033[32m", "\033[0m"


def step(label, call):
    print(f"{CYAN}›{RESET} {BOLD}{label}{RESET}", flush=True)
    out = call()
    for line in out.splitlines():
        print(f"  {GREEN}{line}{RESET}" if line.strip() else "", flush=True)
    print(flush=True)
    time.sleep(1.2)
    return out


def main():
    env = dict(os.environ, VAYL_DB=os.path.join(tempfile.mkdtemp(), "demo.db"),
               VAYL_SLOT_SCHEMA="preset:clinical", VAYL_CRITICAL_CATEGORIES="critical",
               VAYL_LOG_LEVEL="ERROR", FASTMCP_LOG_LEVEL="ERROR")
    print(f"{DIM}# fictional patient · clinical preset · model {env.get('OPENAI_MODEL', 'default')}{RESET}\n")
    with Vayl(env=env, user_id="pt_4471") as m:
        step('remember("Patient is on warfarin 5 mg daily for atrial fibrillation.")',
             lambda: m.remember("Patient is on warfarin 5 mg daily for atrial fibrillation."))
        step('remember("Allergic to penicillin, causes hives.")',
             lambda: m.remember("Allergic to penicillin, causes hives."))
        step('remember("Warfarin stopped today. Started apixaban 5 mg twice daily.")',
             lambda: m.remember("Warfarin stopped today. Started apixaban 5 mg twice daily."))
        pending = step("pending_changes()", m.pending_changes)
        ids = re.findall(r"#(\d+) REPLACE", pending)
        if not ids:
            sys.exit("no pending change: the model did not route the switch through approval")
        step(f'confirm_change({ids[0]}, decided_by="Dr. Okafor")',
             lambda: m.confirm_change(memory_id=int(ids[0]), decided_by="Dr. Okafor"))
        step('recall("What anticoagulant is the patient on?")',
             lambda: m.recall("What anticoagulant is the patient on?"))
        step('recall("What was the patient on before?", include_history=True)',
             lambda: m.recall("What anticoagulant was the patient on before?", include_history=True))
        step('recall("Can we give amoxicillin?")',
             lambda: m.recall("Can we give amoxicillin?"))
    print(f"{DIM}# end of demo{RESET}", flush=True)


if __name__ == "__main__":
    main()
