"""
Slot schema — a declared vocabulary for the facts a deployment cares about.

Vayl normally lets the extractor name its own slots, which suits open-ended memory: nobody can
enumerate in advance every fact an agent might learn about a customer. It has one consequence,
measured on real conversational data: 626 distinct subjects across 638 facts, ~2% reuse. The
same-slot invariant resolves rivals by exact subject equality, so when the extractor names a
slot descriptively per utterance — `melanie_self_care_practices` on Monday and
`melanie_reports_self_care_enables_family_care` on Tuesday — the invariant never fires and two
statements about one thing sit side by side, unreconciled.

For open-ended memory that is a quality problem. For a domain with known critical fields it is a
correctness problem: `check("allergy")` cannot answer if the allergy was filed under
`patient_penicillin_allergy_reported_by_nurse`, and the critical-fact channel cannot inject a
category nobody tagged.

A schema fixes the vocabulary for the slots that matter and leaves everything else free-form.
It is deterministic — canonical names and declared aliases, no similarity threshold — because
guessing that two slots are "the same" is how a reconciling store silently destroys a fact it
should have kept. Embedding-based resolution for the open-ended case is a separate problem and
deliberately not solved here.

Declaring a slot also carries two properties the rest of the system reads:

    category  — tags every fact in the slot, feeding the critical-fact channel so allergies
                reach the answer regardless of ranking
    verbatim  — the value must be stored exactly as stated. Normalization is lossy by design
                ("500mg twice daily" -> "twice_daily" loses the dose), which is fine for
                `favourite_colour` and unacceptable for a prescription.
    confirm   — changes to this slot are gated. `true` or "review": replacing, removing or adding to
                a value is PROPOSED, not applied, and the current value stands until a human
                approves. "auto": a clear change applies at once and is marked as auto-applied;
                only an uncertain one (hedged, negated, future-tense, low confidence, or one that
                would leave two anticoagulants active) is proposed. VAYL_CONFIRM_MODE overrides
                the mode for every gated slot. Reconciliation is driven by an LLM reading
                conversational text, and "stop the warfarin" appearing in a sentence is not the
                same as an order to stop it. For most slots being wrong is recoverable; for some
                the write itself is the hazard, and those need a person in the loop.
    multi     — the slot holds a LIST, not a single value: a patient has many allergies and many
                active medications at once. A new distinct value COEXISTS with the others instead
                of superseding them. Without this, recording a second allergy silently deletes the
                first — the same-slot invariant (one current value) is correct for single-valued
                state and catastrophic for a list. A specific value is still individually removed
                by a retract that names it.

Example (VAYL_SLOT_SCHEMA=/etc/vayl/clinical.json):

    {"slots": [
      {"name": "allergy",           "category": "critical", "verbatim": true,
       "description": "a substance the patient reacts to",
       "aliases": ["allergies", "patient_allergy", "drug_allergy"]},
      {"name": "active_medication", "category": "critical", "verbatim": true,
       "description": "a medication the patient is currently taking"},
      {"name": "code_status",       "category": "critical",
       "description": "resuscitation preference (full code, DNR, DNI)"}
    ]}
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field

_NORM = re.compile(r"[^a-z0-9]+")


def _norm(name):
    """Fold a slot name for matching: lowercase, non-alphanumerics to underscore, trimmed.

    So `Patient Allergy`, `patient-allergy` and `patient_allergy` all reach the same slot. This
    is spelling tolerance, not semantic matching — `penicillin_reaction` still does NOT match
    `allergy`, because deciding that two differently-named things are the same slot is exactly
    the judgement that must be declared rather than inferred.
    """
    return _NORM.sub("_", str(name or "").lower()).strip("_")


@dataclass
class SlotSpec:
    name: str
    description: str = ""
    category: str = ""          # tags facts in this slot; feeds the critical-fact channel
    verbatim: bool = False      # store the value exactly as stated — no normalization
    confirm: bool = False       # changes to this slot are gated (see confirm_mode)
    confirm_mode: str = ""      # "review" (a person approves every change) or "auto" (clear changes apply)
    multi: bool = False         # holds MANY concurrent values (an allergy list, a med list)
    aliases: list = field(default_factory=list)

    @property
    def keys(self):
        """Every spelling that resolves to this slot, normalized."""
        return {_norm(self.name)} | {_norm(a) for a in self.aliases}


class SlotSchema:
    """A declared vocabulary. Empty by default — a general-purpose deployment declares nothing
    and behaves exactly as it did before this existed."""

    def __init__(self, slots=()):
        self.slots = list(slots)
        self._by_key = {}
        for spec in self.slots:
            for k in spec.keys:
                self._by_key[k] = spec

    def __bool__(self):
        return bool(self.slots)

    def __len__(self):
        return len(self.slots)

    # ── resolution ───────────────────────────────────────────────────────────
    def resolve(self, subject):
        """Canonical SlotSpec for a proposed subject, or None to leave it free-form.

        Returning None is the safe outcome and the common one: a fact outside the declared
        vocabulary keeps whatever the extractor called it. A schema constrains the slots it names
        and says nothing about the rest.
        """
        return self._by_key.get(_norm(subject))

    def canonical(self, subject):
        """The canonical slot name for `subject`, or `subject` unchanged if undeclared."""
        spec = self.resolve(subject)
        return spec.name if spec else subject

    # ── prompt fragment ──────────────────────────────────────────────────────
    def prompt_fragment(self):
        """The vocabulary as extractor instructions, or "" when nothing is declared.

        Deliberately says 'when it fits' rather than 'always': forcing a fact into the nearest
        declared slot would file a blood-pressure reading under `allergy` because that was the
        closest option, which is worse than leaving it free-form.
        """
        if not self.slots:
            return ""
        lines = []
        for s in self.slots:
            bits = [f'  "{s.name}"']
            if s.description:
                bits.append(f"— {s.description}")
            if s.verbatim:
                bits.append("[VERBATIM: copy the value exactly as stated; do not summarise, "
                            "round, abbreviate or reformat it]")
            if s.confirm:
                bits.append("[CONFIRMED: changes here are reviewed by a person before taking "
                            "effect — extract normally and let the review decide]")
            if s.multi:
                bits.append("[LIST: holds several values at once; a new one ADDS to the list and "
                            "does not replace the others]")
            lines.append(" ".join(bits))
        return (
            "\n\nDECLARED SLOTS — this deployment tracks the following facts under FIXED subject "
            "names. When a fact fits one of these, you MUST use the exact subject given here "
            "rather than inventing a descriptive name, so that a later statement about the same "
            "thing lands in the same slot and reconciles against it:\n"
            + "\n".join(lines)
            + "\nIf a fact does not fit any declared slot, name it freely as usual. Do NOT force "
              "an unrelated fact into a declared slot because it is the closest match.")


# ── loading ──────────────────────────────────────────────────────────────────

def _confirm(value, slot):
    """`confirm` as a mode: "" (no gate), "review" or "auto". `true` means review, so a schema that
    predates the modes keeps its meaning. Anything else is a schema error, not a silent default."""
    if value in (False, None, "", "off", "false"):
        return ""
    if value is True or value in ("review", "true"):
        return "review"
    if value == "auto":
        return "auto"
    raise ValueError(f"slot {slot!r}: confirm must be true, false, \"review\" or \"auto\", not {value!r}")


def from_dict(obj):
    slots = []
    for raw in (obj or {}).get("slots", []):
        name = str(raw.get("name") or "").strip()
        if not name:
            continue
        slots.append(SlotSpec(
            name=name,
            description=str(raw.get("description") or ""),
            category=str(raw.get("category") or ""),
            verbatim=bool(raw.get("verbatim", False)),
            confirm=_confirm(raw.get("confirm", False), name) != "",
            confirm_mode=_confirm(raw.get("confirm", False), name),
            multi=bool(raw.get("multi", False)),
            aliases=[str(a) for a in (raw.get("aliases") or [])],
        ))
    return SlotSchema(slots)


_PRESET_PREFIX = "preset:"


def list_presets():
    """Names of the built-in slot presets bundled with Vayl (e.g. 'clinical', 'finance', 'support')."""
    import importlib.resources as res
    try:
        return sorted(p.name[:-5] for p in res.files("vayl.presets").iterdir()
                      if p.name.endswith(".json"))
    except (ModuleNotFoundError, FileNotFoundError):
        return []


def _load_preset(name):
    """Load a bundled preset by name. Only enumerated presets load — no path traversal."""
    import importlib.resources as res
    name = name.strip().lower()
    if name not in list_presets():
        avail = ", ".join(list_presets()) or "(none)"
        raise ValueError(f"unknown slot preset '{name}'. Available presets: {avail}")
    text = res.files("vayl.presets").joinpath(name + ".json").read_text(encoding="utf-8")
    return from_dict(json.loads(text))


def load(path=None):
    """Load the schema from `path` or VAYL_SLOT_SCHEMA. Returns an empty schema if unset.

    `path` is either a file path, or `preset:<name>` to load one of the built-in presets bundled
    with Vayl (`preset:clinical`, `preset:finance`, `preset:support`; see list_presets()).

    A malformed schema raises rather than falling back to empty: silently ignoring it would mean
    a clinical deployment believing allergies are canonicalised and always-injected when they are
    not — failing open on exactly the guarantee the file exists to provide.
    """
    path = path or os.environ.get("VAYL_SLOT_SCHEMA", "")
    if not path:
        return SlotSchema()
    if path.startswith(_PRESET_PREFIX):
        return _load_preset(path[len(_PRESET_PREFIX):])
    with open(os.path.expanduser(path), encoding="utf-8") as f:
        return from_dict(json.load(f))
