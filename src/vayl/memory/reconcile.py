#!/usr/bin/env python3
"""
Vayl — reconciliation data model
================================
The shared domain model the reconciler runs on: the event log's `Statement`, the
`Status` a statement can hold, and the `Action` the engine takes on a new fact.
The live reconciler is LLM-driven and lives in `llm_memory.py`; this module holds
only what that path (and the store and demo) import.

The thesis it serves: the product's job is not perfect auto-resolution
(impossible) — it's to NEVER be *silently wrong*. Clear cases auto-resolve;
ambiguous cases get FLAGGED, not guessed.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from enum import Enum

# Domain model  (event log → statements → current view)

class Status(str, Enum):
    ACTIVE = "ACTIVE"
    SUPERSEDED = "SUPERSEDED"
    FLAGGED = "FLAGGED_CONFLICT"   # honest uncertainty: surfaced, not resolved
    HISTORICAL = "HISTORICAL"      # recorded past fact — never the current belief (valid-time)

class Action(str, Enum):
    ADD = "ADD"                # new slot, no conflict
    DEDUP = "DEDUP"            # same fact already known
    REFINE = "REFINE"          # more detail on an existing fact
    SUPERSEDE = "SUPERSEDE"    # contradicts an active fact -> retire old
    COEXIST = "COEXIST"        # differs but different SCOPE -> both true
    FLAG = "FLAG"              # ambiguous -> surface both, don't guess
    SKIP = "SKIP"              # not a durable fact (hypothetical/sarcasm)
    ARCHIVE = "ARCHIVE"        # valid-time gate: a PAST statement — record as history, never active
    RETRACT = "RETRACT"        # removal with NO replacement: retire the fact, leave the slot empty

_counter = itertools.count(1)

@dataclass
class Statement:
    slot: str                  # subject + scope -> the contradiction key
    subject: str
    value: str
    scope: str
    status: Status = Status.ACTIVE
    confidence: float = 1.0
    supersedes: int | None = None
    raw: str = ""
    id: int = field(default_factory=lambda: next(_counter))
    metadata: dict | None = None
    source: str = ""                  # who/what asserted this fact — belief provenance
    # Graph triple this fact projects to. Persisted so the Neo4j projection can be REBUILT from the
    # store; without them the graph is an unrecoverable side-store rather than a projection.
    head: str = ""
    relation: str = ""
    tail: str = ""
    # Set by the store on load. Declared so the state is explicit and type-checked; kept out of equality
    # and repr, as when these were ad-hoc attributes.
    created_at: float | None = field(default=None, compare=False, repr=False)
    _emb: list[float] | None = field(default=None, init=False, compare=False, repr=False)
    _has_emb: bool = field(default=False, init=False, compare=False, repr=False)

# Value normalization + marker helpers used by the LLM reconciler (llm_memory.py)

# Toy stand-in for the real entity/value normalization layer — the actual hard work.
SYNONYMS = {"postgres": "postgresql", "redux": "redux toolkit", "mongo": "mongodb"}

HYPOTHETICAL_MARKERS = ["what if", "should we", "considering", "thinking about", "maybe",
                        "might ", "could ", "hypothetically", "not sure", "wondering", "proposal",
                        # clinical hedges and conditionals ("probably stop the warfarin if her INR
                        # stays high" is a plan, not an order)
                        "probably", " if ", "consider ", "plan to", "planning to", "may need",
                        "would "]
# DEFINITE removal language — the fact ends and nothing takes its place. Every phrase asserts the
# removal as done, so a hedge ("considering dropping X") is excluded by HYPOTHETICAL_MARKERS and
# never reaches a RETRACT. Over-deleting a still-true fact is the failure this list has to avoid.
RETRACT_MARKERS = ["no longer", "dropped", "stopped using", "stopped supporting", "got rid of",
                   "removed", "is gone", "are gone", "discontinued", "decommissioned", "sunset",
                   "retired ", "we don't use", "we do not use", "no more", "deprecated",
                   "shut down", "turned off", "cancelled", "canceled"]
SARCASM_MARKERS = ["🙄", "oh sure", "yeah right", "just kidding", "/s", "lol", "as if"]

def _norm(t: str) -> str:
    return " " + t.lower().strip() + " "

def canon(v: str | None) -> str | None:
    return SYNONYMS.get(v, v) if v else v

def has(text: str, markers) -> bool:
    t = _norm(text)
    return any(m in t for m in markers)
