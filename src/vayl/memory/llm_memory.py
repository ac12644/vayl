"""
The LLM-driven memory engine: extraction, reconciliation, retrieval, and answering.

One LLM call extracts a statement's canonical (subject, value, scope) AND classifies its relationship
to the facts already held; `LLMMemory._apply` then reconciles deterministically — the same-slot
invariant, declared slots, events vs state, confirmation gates — so the trust properties live in
code rather than in the prompt. Below the confidence threshold a resolving action is FLAGGED rather
than guessed.
"""
import itertools
import json
import logging
import os
import re
import time
import urllib.error
import urllib.request
from collections.abc import Callable

from vayl.config import env_bool, env_float, env_int
from vayl.memory import llm_client, retrieval
from vayl.memory.reconcile import (
    HYPOTHETICAL_MARKERS,
    RETRACT_MARKERS,
    SARCASM_MARKERS,
    Action,
    Statement,
    Status,
    canon,
    has,
)

log = logging.getLogger(__name__)

AUTO_THRESHOLD = 0.7

# Reconciliation context cap — how many active facts the extractor sees per write. A small space
# passes ALL active facts (embed_retrieve returns everything under the cap → no extra embedding,
# behaviour unchanged); a large space passes only the top-k RELEVANT facts, so write cost stays
# bounded as memory grows instead of scaling with the whole space. _apply still reconciles against
# the FULL active set (the same-slot invariant + target lookup), so a conflict outside the top-k is
# still caught — the cap bounds cost, it does not weaken reconciliation.
_RECONCILE_CONTEXT = env_int("VAYL_RECONCILE_CONTEXT", 40)

# Recall context cap — how many facts the synthesizer sees per read. When a space has this many facts
# or fewer, recall passes them ALL (embed_retrieve returns everything under the cap → no query
# embedding at read time, ~700 ms saved, and the model sees every active fact so it can't miss the
# answer). A larger space falls back to top-k semantic+lexical retrieval to keep the context bounded.
_RECALL_CONTEXT = env_int("VAYL_RECALL_CONTEXT", 40)

# Graph relations that hold MANY tails per head (a service depends on several; a team has several
# members) — these coexist. Everything NOT listed is treated as FUNCTIONAL (one tail per head), so a
# re-point (routes_to, reports_to, located_in…) retires the old edge even when the extractor labelled
# it ADD — the graph mirror of the slot store's one-active-per-slot invariant. Conservative denylist:
# when unsure we prefer retiring a stale edge (Vayl's anti-silently-wrong stance) to keeping a dead one.
MULTI_VALUED_RELS = {
    "DEPENDS_ON", "USES", "USED_BY", "MEMBER_OF", "HAS_MEMBER", "PART_OF", "CONTAINS", "HAS",
    "INCLUDES", "CALLS", "CALLED_BY", "SUPPLIES", "SUPPLIED_BY", "BUYS", "BUYS_FROM", "SELLS",
    "IMPORTS", "EXPORTS", "CONNECTS_TO", "CONNECTED_TO", "INTEGRATES_WITH", "RELATED_TO", "LINKS_TO",
    "REFERENCES", "KNOWS", "WORKS_WITH", "COLLABORATES_WITH", "TAGGED_WITH", "HAS_TAG", "OWNS",
    "MANAGES", "SUPPORTS", "PROVIDES", "OFFERS", "PARTICIPATES_IN", "RESPONSIBLE_FOR", "ASSIGNED_TO",
    "COMPRISES", "PRODUCES", "HANDLES",
}


def _norm_rel(rel):
    """Normalize a relation label for the multi-valued lookup: 'routes to' → 'ROUTES_TO'."""
    return re.sub(r"[\s\-]+", "_", str(rel).strip().upper())

SYS = """You are a memory extraction + reconciliation engine for an AI agent.
Given the CURRENT active facts and a NEW statement — which may contain ZERO, ONE, or SEVERAL distinct facts —
EXTRACT every distinct fact worth remembering — both STATE that holds and EVENTS that happened — and
CLASSIFY each one's relationship to the active facts. Return ONLY a JSON object
with a "facts" array (one element per fact):

{"facts":[
  {"subject":"<canonical snake_case key that uniquely identifies WHO/WHAT the fact is about — see ENTITY SCOPING>",
   "value":"<the specific value itself — NOT a paraphrase of the sentence. See CONCRETE VALUES>",
   "kind":"<state|event — see STATE vs EVENT>",
   "scope":"<global, or a qualifier like web/mobile/backend>",
   "head":"<SUBJECT entity of the triple — the NAMED entity from THIS statement; use 'Org' ONLY for the speaker's own unnamed org ('we'/'our')>",
   "relation":"<UPPER_SNAKE predicate, DIRECTED head->tail. See GRAPH TRIPLE DIRECTION>",
   "tail":"<OBJECT entity of the triple, taken from THIS statement>",
   "time_ref":"<current|past|future|unknown>",
   "action":"<ADD|DEDUP|REFINE|SUPERSEDE|COEXIST|RETRACT|FLAG|SKIP>",
   "confidence":<0.0-1.0 — see CONFIDENCE>, "reason":"<=8 words, why this action>",
   "target_id":<int id of the fact being superseded/refined, or null>}
]}

MULTIPLE FACTS: if one statement asserts several distinct facts (e.g. "we moved to postgres, alice is the new
lead, and we dropped redis" = 3 facts), return ONE array element per fact. If the statement records nothing
worth remembering (greeting, chatter, a bare question), return {"facts":[]}. If a statement contradicts ITSELF
("we use mysql, well actually postgres now"), return only the FINAL asserted value as a single fact.

STATE vs EVENT — set "kind" on every fact. Getting this wrong loses information:
- state: a value that HOLDS until something replaces it. "we use Postgres", "Alice is the lead",
    "the customer is on the Free plan". A new state on the same slot REPLACES the old one.
- event: something that HAPPENED at a point in time. "she ran a charity race", "the customer called
    to complain", "he painted a sunrise in 2022", "we shipped v2 on Tuesday".
    Events NEVER replace each other — two races are two races, not a correction. Give each event a
    subject that distinguishes it (alice_charity_race_may_2023, not alice_race).
Do NOT discard an event because it is not a lasting state. "I went to the support group yesterday"
is a fact worth keeping; dropping it loses the only record that it happened.

CONCRETE VALUES — the value must carry the specific detail, not a summary of it:
- "I went yesterday" said on 8 May -> value "2023-05-07", NOT "attended_recently".
- "I won first place with a piece called Nocturne" -> value "first_place_nocturne", NOT "did_well".
- Put names, dates, numbers and titles in the value. A value that reads as a paraphrase of the
  question rather than an answer to it ("exploring_options", "going_well") is not worth storing.
If the statement gives a date, or one can be resolved from the conversation date, put it in the value.
NEVER INVENT PRECISION THE SOURCE DOES NOT CONTAIN — record exactly as much as you can justify:
  "in 2022"           -> "2022"          (NOT "2022-01-01" — the day is unknown)
  "last May"          -> "2023-05"       (NOT a specific day)
  "yesterday", said on 2023-05-08 -> "2023-05-07"   (the day IS derivable, so give it)
A fabricated day looks like evidence and is worse than an honest "2022".

ENTITY SCOPING — the subject must uniquely identify WHOSE fact it is, or different entities collide in one slot:
- If a specific entity is NAMED (a person, company, product), PUT IT IN THE SUBJECT:
    "Alice works at Google" -> subject "alice_employer"; "Bob works at Fitbit" -> subject "bob_employer"
    (NOT a shared "work_employer" — that makes Bob overwrite Alice).
    "Google acquired Fitbit" -> "google_acquisitions"=Fitbit;  "Carol is Alice's manager" -> "alice_manager"=Carol.
- If the fact is about the USER / the org itself (implicit single subject — "we use X", "I prefer Y"),
    the predicate alone is enough — name the ATTRIBUTE this statement is about, with no entity prefix.
    (Any example subject printed in these instructions is illustrative only. NEVER reuse a subject
    from this prompt; derive it from the statement in front of you.)

ONE SUBJECT = ONE ATTRIBUTE. A subject names a single thing that can hold a single value. It is NOT a
bucket to file related facts under. This matters more than it looks: a second fact landing on the same
subject RETIRES the first one, so a subject broad enough to attract unrelated facts will silently
delete them.

THE REPLACEMENT TEST — before emitting a subject, ask: "if a later statement lands on this same
subject, would it be a CORRECTION of this fact?"
  "database" — a later value corrects it. Good subject.
  "recent_work" — a painting, a race and a work habit all land here and none corrects another.
    Bucket. It would destroy two true facts.
If the answer is no, the subject is too broad: split it into the specific attribute each fact is about.

Never emit a subject built from a vague grouping word — activity, work, update, options, details,
information, stuff, things, news, status (alone), notes. Name the actual attribute instead.

CRITICAL: REUSE an existing active fact's exact "subject" ONLY when the new fact is about the SAME entity AND the
same predicate (so they share a slot and reconcile). Facts about DIFFERENT entities must get DIFFERENT subjects.

GRAPH TRIPLE DIRECTION — head/relation/tail must read as a TRUE sentence spoken "head RELATION tail":
- "Carol is Alice's manager" -> head=Alice, relation=HAS_MANAGER, tail=Carol  (reads "Alice HAS_MANAGER Carol" ✓).
    Do NOT emit head=Alice, relation=MANAGER_OF, tail=Carol — that reads "Alice manages Carol", which is FALSE.
- "Carol reports to Dave" -> head=Carol, relation=REPORTS_TO, tail=Dave.
- Name the ACTUAL entity when given: "Acme's cloud provider is AWS" -> head=Acme (NOT 'Org').
- Before emitting each triple, read it back: "head relation tail" must be a true statement.

TIME_REF — THE TEST: is the value TRUE RIGHT NOW? Judge the value's validity, NOT when the change happened.
- current: the value holds at present. A change that happened in the past but STILL HOLDS is current.
    "we use X", "switched to X", "migrated to X last sprint", "I moved to Amsterdam last month", "now X", "is X".
    (The move/migration is past, but the resulting state is current — so time_ref=current.)
- past: names a FORMER state that is NO LONGER true — it has been ended or replaced.
    "used to X", "previously X", "historically X", "back in 2022 we used X", "we dropped X", "no longer X".
- future: not true yet. "will", "planning to", "next quarter".
Litmus: "Historically we billed monthly" → is monthly true now? No → past. "I moved to Amsterdam last month" →
is Amsterdam true now? Yes → current. Never mark a still-in-effect change as past just because it has a past date.

Actions:
- SKIP: hypothetical, question, or sarcasm — nothing asserted, so nothing to record.
- ADD: new topic; no related active fact.
- DEDUP: restates an existing fact, no new info.
- REFINE: same fact, adds detail. Set target_id.
- SUPERSEDE: replaces an existing fact with a NEW value. "switched to Redux", "flag is now disabled". Set target_id.
- COEXIST: differs but different scope (different app/service). Both stay true.
- RETRACT: removes/ends an existing fact with NO replacement value — the slot becomes empty.
    "we dropped Sentry", "we no longer use X", "we stopped supporting IE11", "X was removed". Set target_id.
    (If it removes AND names a new value, that's SUPERSEDE, not RETRACT.)
- FLAG: two STATE facts give different values for the SAME slot and nothing signals which is newer
    (no "now", "switched", "moved to", no date ordering them). Set target_id.
    Do NOT flag: an event (events never conflict), a fact that merely ADDS detail to another
    (that is REFINE), two facts about different things that happen to share a word, or a value
    that elaborates rather than contradicts. Flagging benign narrative makes the record read as
    contradictory when it is not, so flag only a genuine unresolved same-slot disagreement.

CONFIDENCE — this is not decorative: below 0.7 the system will FLAG the fact for human review
instead of applying it, so an uncalibrated guess creates false disputes on ordinary statements.
Score how firmly the STATEMENT asserts the value, not how important it seems:
  0.95  explicitly and unambiguously stated — "we use Postgres", "her name is Alice"
  0.85  clearly stated with ordinary wording — "I went to the group yesterday"
  0.70  stated but with mild imprecision — "we're on Postgres, I think 14"
  0.50  inferred rather than stated — the statement implies it without saying it
  0.30  hedged, second-hand or speculative — "someone mentioned maybe moving to Aurora"
Most plain declarative sentences are 0.85-0.95. Do not mark a clear statement low because the
topic is sensitive or the value surprises you.

HONESTY RULE: if not confident it is a real CHANGE to a state, choose FLAG, never SUPERSEDE/RETRACT.
Sarcasm/hypotheticals are SKIP. This rule is about changes to state — it never applies to events,
which stand alongside each other and need no adjudication."""

def _salvage_facts(raw):
    """Recover the fact objects a weak model got RIGHT when the overall array is malformed.

    A 3B model reliably drops a comma inside a big `{"facts":[...]}` array on complex input — the
    same chunk fails every retry, so re-rolling cannot help. But the other five facts in that array
    are usually well-formed. Dropping the whole observation over one broken element is a hole in
    memory; keeping the valid facts is strictly better and also helps a strong model that fumbles a
    single fact in a large batch.

    Scans for balanced top-level `{...}` objects and parses each independently, keeping those that
    look like a fact (carry a subject). Returns None if nothing usable is found, so the caller still
    raises rather than silently storing an empty extraction."""
    # Try every `{` as the start of a fact object. The fact objects are nested inside a
    # `{"facts":[...]}` wrapper that itself will not parse (that is why we are here), so a scan for
    # only top-level objects would find just the broken wrapper. Matching each brace to its close
    # and parsing that span recovers the inner objects that ARE well-formed.
    facts, seen = [], set()
    for start in (i for i, ch in enumerate(raw) if ch == "{"):
        depth = 0
        for j in range(start, len(raw)):
            if raw[j] == "{":
                depth += 1
            elif raw[j] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        obj = json.loads(raw[start:j + 1])
                    except ValueError:
                        pass
                    else:
                        if isinstance(obj, dict) and obj.get("subject") and start not in seen:
                            facts.append(obj)
                            seen.add(start)
                    break
    return {"facts": facts} if facts else None


def _first_json(raw):
    """Extract a JSON object from a model reply — tolerant of the ways free/reasoning
    models wrap it: <think> blocks, ```json fences, or prose around the object."""
    raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL)
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
    candidate = fence.group(1) if fence else None
    if candidate is None:
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        candidate = m.group(0) if m else None
    if candidate is None:
        raise ValueError("model returned no JSON object (it may be a reasoning-only model)")
    try:
        return json.loads(candidate)
    except ValueError:
        # The whole object did not parse. Before giving up (and dropping the observation), try to
        # keep the fact objects that ARE well-formed — a missing comma between two facts should not
        # discard the other five.
        salvaged = _salvage_facts(candidate)
        if salvaged is not None:
            return salvaged
        raise


def _call_anthropic(user):
    key = os.environ["ANTHROPIC_API_KEY"]
    payload = json.dumps({
        "model": os.environ.get("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001"),
        "max_tokens": 300, "system": SYS + SLOT_SCHEMA.prompt_fragment(),
        "messages": [{"role": "user", "content": user}, {"role": "assistant", "content": "{"}],
    }).encode()
    req = urllib.request.Request("https://api.anthropic.com/v1/messages", data=payload,
        headers={"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"})
    data = llm_client._http_json(req, timeout=30)
    return _first_json("{" + data["content"][0]["text"])

def _call_groq(user):
    # Groq is OpenAI-compatible; JSON mode requires the word "json" in the prompt (SYS has it).
    key = os.environ["GROQ_API_KEY"]
    payload = json.dumps({
        "model": os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile"),
        "max_tokens": 400, "temperature": env_float("GROQ_TEMP", 0.0),
        "response_format": {"type": "json_object"},
        "messages": [{"role": "system", "content": SYS + SLOT_SCHEMA.prompt_fragment()},
                     {"role": "user", "content": user}],
    }).encode()
    req = urllib.request.Request("https://api.groq.com/openai/v1/chat/completions", data=payload,
        headers={"Authorization": f"Bearer {key}", "content-type": "application/json",
                 "User-Agent": "vayl-eval/1.0"})   # Cloudflare 403s the default urllib UA
    data = llm_client._http_json(req, timeout=env_float("LLM_TIMEOUT", 60.0))
    return _first_json(data["choices"][0]["message"]["content"])


def _call_openai(user):
    # Works with ANY OpenAI-compatible endpoint via OPENAI_BASE_URL — including the FREE ones:
    #   Gemini    : OPENAI_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai  OPENAI_MODEL=gemini-2.0-flash
    #   OpenRouter: OPENAI_BASE_URL=https://openrouter.ai/api/v1  OPENAI_MODEL=meta-llama/llama-3.3-70b-instruct:free
    #   Cerebras  : OPENAI_BASE_URL=https://api.cerebras.ai/v1    OPENAI_MODEL=llama-3.3-70b
    #   Ollama    : OPENAI_BASE_URL=http://localhost:11434/v1     OPENAI_MODEL=llama3.1  OPENAI_API_KEY=ollama
    base, key, model, _local = llm_client._openai_config()
    body = {
        "model": model,
        "messages": [{"role": "system",
                      "content": os.environ.get("OPENAI_SYSTEM_PREFIX", "") + SYS
                                 + SLOT_SCHEMA.prompt_fragment()},
                     {"role": "user", "content": user}],
        **llm_client._openai_gen_params(model, 400),
    }
    if env_bool("OPENAI_JSON", True):     # some local models don't support json mode
        body["response_format"] = {"type": "json_object"}
    req = urllib.request.Request(base + "/chat/completions", data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {key}", "content-type": "application/json",
                 "User-Agent": "vayl/0.1"})
    data = llm_client._http_json(req, timeout=env_float("LLM_TIMEOUT", 60.0))
    return _first_json(data["choices"][0]["message"]["content"])


def _qa(context, question):
    """Read layer: answer a question (incl. multi-hop) over the stored facts. Free text, not JSON.

    The READ is synthesis over facts we already retrieved — simpler than the write-path extraction —
    so VAYL_READ_MODEL can point it at a cheaper model (e.g. a nano tier) than OPENAI_MODEL. Note this
    is a COST lever, not a latency one: for a short one-sentence answer, latency is network + output
    generation bound, so a smaller model is not measurably faster (a reasoning nano is slower). It cuts
    per-recall token cost, which matters at scale. Unset → the extraction model."""
    read_model = os.environ.get("VAYL_READ_MODEL")
    system = ("You answer questions about a user/organization using ONLY the FACTS provided, chaining across "
              "them for multi-hop questions. Facts tagged (history) are FORMER/superseded values, not current. "
              "If the facts don't support an answer, say you don't know. Answer in one short sentence.")
    user = f"FACTS:\n{context}\n\nQUESTION: {question}"
    system = os.environ.get("OPENAI_SYSTEM_PREFIX", "") + system   # e.g. "/no_think" for reasoning models
    provider = llm_client._provider()
    if provider == "anthropic":
        key = os.environ["ANTHROPIC_API_KEY"]
        payload = json.dumps({"model": read_model or os.environ.get("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001"),
            "max_tokens": 160, "system": system, "messages": [{"role": "user", "content": user}]}).encode()
        req = urllib.request.Request("https://api.anthropic.com/v1/messages", data=payload,
            headers={"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"})
        return llm_client._http_json(req, 30)["content"][0]["text"].strip()
    if provider == "openai":   # local Ollama by default; respects OPENAI_BASE_URL
        base, key, model, _local = llm_client._openai_config()
        model = read_model or model
        url = base + "/chat/completions"
    else:
        url, key = "https://api.groq.com/openai/v1/chat/completions", os.environ["GROQ_API_KEY"]
        model = read_model or os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile")
    payload = json.dumps({"model": model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        **llm_client._openai_gen_params(model, 300)}).encode()
    hdr = {"Authorization": f"Bearer {key}", "content-type": "application/json", "User-Agent": "vayl/0.1"}
    req = urllib.request.Request(url, data=payload, headers=hdr)
    return llm_client._http_json(req, 30)["choices"][0]["message"]["content"].strip()

_EXTRACT_JSON_RETRIES = max(0, env_int("VAYL_EXTRACT_RETRIES", 2))   # <0 would skip the call entirely

# ── PRE-LLM DEDUP ──
# The write path spends one LLM call per message even when the message is a verbatim restatement of
# facts already current. When the exact utterance previously produced fact(s) that are ALL still the
# active value in their slot, re-extraction is guaranteed to reconcile to DEDUP (see the RESTATEMENT
# check in `_apply`), so the call can be skipped. This ONLY fires on an exact match after light
# normalization — never fuzzy similarity — so it can't skip a subtly different value ("5 mg" vs
# "7 mg") that ought to supersede. On by default; VAYL_DEDUP_PREFILTER=off restores always-extract.
_DEDUP_PREFILTER = env_bool("VAYL_DEDUP_PREFILTER", True)
_WS_RE = re.compile(r"\s+")


def _norm_utterance(text):
    """Case-fold and collapse whitespace for the pre-LLM dedup match. Deliberately conservative:
    exact-after-normalization only, so a match means a literal restatement, nothing looser."""
    return _WS_RE.sub(" ", (text or "").strip().lower())


def llm_extract_classify(text, active):
    facts = [{"id": s.id, "subject": s.subject, "value": s.value, "scope": s.scope} for s in active]
    user = f'CURRENT active facts:\n{json.dumps(facts)}\n\nNEW statement:\n"{text}"\n\nJSON:'
    call = {"groq": _call_groq, "openai": _call_openai}.get(llm_client._provider(), _call_anthropic)
    # A model that returns malformed JSON is a re-roll away from valid JSON — the call is stochastic,
    # so retrying the SAME request usually succeeds. `_http_json` already retries transport errors
    # (429/5xx), but a parse failure happens on the returned body and was not retried at all, so one
    # bad roll dropped the whole observation. A weak/local model does this a few percent of the time.
    obj = None
    for attempt in range(_EXTRACT_JSON_RETRIES + 1):
        try:
            obj = call(user)
            break
        except (ValueError, KeyError):        # unparseable JSON / missing field
            if attempt == _EXTRACT_JSON_RETRIES:
                raise
    assert obj is not None   # the loop runs at least once: it either set obj or raised
    out = obj.get("facts")
    if out is None:                       # fallback: model returned a single-fact object
        out = [obj] if obj.get("subject") else []
    return out if isinstance(out, list) else []


# ── critical categories ──────────────────────────────────────────────────────
# Facts a caller cannot afford to have ranked out of context. Ordinary recall is semantic
# top-k, which is probabilistic: an allergy that does not make the cut is not merely ranked
# low, it is INVISIBLE, and the answer comes back confident and incomplete. Safety gates
# (safe_recall, check) can only judge facts they were given, so a retrieval miss defeats them
# silently. Facts in these categories bypass ranking entirely.
#
# Off unless configured — a general-purpose deployment has no such categories, and inventing
# some would be worse than none.
# The declared slot vocabulary, if this deployment has one. Loaded once; a malformed file raises
# at import rather than degrading to "no schema", because failing open here would mean believing
# critical slots are canonicalised when they are not.
try:
    from vayl.memory.schema import load as _load_schema
    SLOT_SCHEMA = _load_schema()
except FileNotFoundError:
    raise
except Exception:                       # unreadable/malformed JSON — surface it, don't swallow it
    raise

_CRITICAL_CATEGORIES = tuple(
    c.strip().lower() for c in os.environ.get("VAYL_CRITICAL_CATEGORIES", "").split(",") if c.strip())
_CRITICAL_BUDGET = env_int("VAYL_CRITICAL_BUDGET", 200)

# Fragment resolution. A weak or verbose extractor names the same slot several ways —
# `caroline_self_care_realization`, then `..._8_may_2023`, then `..._14_may_2023` — with the SAME
# value each time. Measured: every fragmentation case shared an identical value across near-
# identical subjects. Folding those is dedup-only and cannot destroy data: it turns an ADD into a
# DEDUP, never a supersede. OFF by default; it changes stored subjects, which some callers key on.
_SLOT_RESOLVE = env_bool("VAYL_SLOT_RESOLVE", False)

# Sources whose changes are already authorized and therefore bypass the confirmation gate. The gate
# protects against a change inferred by an LLM reading conversational text ("stop the warfarin" in a
# note is not an order). A structured feed — a FHIR MedicationRequest with status=stopped, an HL7
# order — IS an authorized order; the clinical sign-off already happened upstream, and re-queuing it
# for approval is friction, not safety. Empty by default (everything is gated, unchanged); a clinical
# deployment sets VAYL_TRUSTED_SOURCES=fhir,hl7.
_TRUSTED_SOURCES = tuple(
    x.strip().lower() for x in os.environ.get("VAYL_TRUSTED_SOURCES", "").split(",") if x.strip())
_SLOT_RESOLVE_SIM = env_float("VAYL_SLOT_RESOLVE_SIM", 0.6)
_DATE_TOK = re.compile(r"^(\d+|jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec|"
                       r"january|february|march|april|june|july|august|september|october|"
                       r"november|december|st|nd|rd|th)$")


def _subject_tokens(subject):
    """Content tokens of a subject, with date/ordinal noise dropped.

    The observed fragmentation was a model appending a date to a stable stem
    (`..._realization` -> `..._realization_8_may_2023`), so stripping date tokens makes the two
    match near-exactly instead of relying on a fragile similarity threshold."""
    return {t for t in re.split(r"[^a-z0-9]+", str(subject or "").lower())
            if t and not _DATE_TOK.match(t)}


def _subject_similar(a, b, threshold=None):
    """True if two subjects are the same slot named differently — Jaccard over content tokens.

    Deterministic and offline: no embedding call, no data-tuned threshold on the hot path, and the
    result is explainable ('they share these tokens'). Semantic similarity is deliberately NOT used
    here — two subjects can be about the same topic yet hold different, both-true facts, and merging
    those would be the data loss this is meant to avoid. Same-value is required by the caller, which
    is what makes even a loose token match safe."""
    ta, tb = _subject_tokens(a), _subject_tokens(b)
    if not ta or not tb:
        return False
    if ta == tb:
        return True
    thr = _SLOT_RESOLVE_SIM if threshold is None else threshold
    return len(ta & tb) / len(ta | tb) >= thr


class CriticalOverflow(RuntimeError):
    """More critical facts than the context budget allows.

    Raised rather than truncated. Dropping the tail of an always-include set would reintroduce
    exactly the silent miss the set exists to prevent — and would do it in the categories where
    a miss matters most. A caller that sees this must raise the budget or narrow the categories;
    it must not be allowed to proceed on a quietly incomplete answer.
    """


def _category(s):
    return str((getattr(s, "metadata", None) or {}).get("category") or "").lower()


def is_critical(s, categories=None):
    """True if this fact belongs to a category that must never be ranked out of context."""
    cats = _CRITICAL_CATEGORIES if categories is None else tuple(c.lower() for c in categories)
    return bool(cats) and _category(s) in cats


def _ctx_line(s):
    """One fact as the answering model sees it: the reconciled slot, plus the sentence it came from.

    A statement is two things — a normalized slot, which is what makes reconciliation possible, and
    the utterance it was extracted from. Normalizing is lossy by design: "I went to the group
    yesterday" becomes `attended_recently`, which is true and cannot answer "when?". The slot
    carries the verdict (this value is current, that one is retired); the raw sentence carries the
    detail the slot dropped. Sending only the slot throws away half of what is stored.
    """
    line = f"{s.subject}={s.value}"
    raw = (getattr(s, "raw", "") or "").strip()
    return f'{line} ("{raw[:300]}")' if raw else line


_LIST_ID_CUT = re.compile(r"\s+[—–-]\s+|\s\d")
# Clinical stop language. Not "hold": a hold is a pause expected to resume (FHIR "on-hold").
_STOP_WORDS = re.compile(r"(?<![a-z])(?:stop|stopped|stopping|discontinue|discontinued|d/c(?:'?d)?|dc'?(?:e?d)"
                         r"|cease|ceased|taken off|came off|come off|(?:been|is|was|went|now) off|off since"
                         r"|no longer (?:taking|on))(?![a-z])")


# Synonyms a model has returned for an action (gpt-5-mini: "RETIRE"). An unknown action used to raise
# and lose every fact in the message; a removal or replacement word maps to its action, anything else
# is FLAGged for a person instead of guessed.
_ACTION_SYNONYMS = {"RETIRE": "RETRACT", "REMOVE": "RETRACT", "DELETE": "RETRACT", "STOP": "RETRACT",
                    "DISCONTINUE": "RETRACT", "REPLACE": "SUPERSEDE", "UPDATE": "SUPERSEDE",
                    "CHANGE": "SUPERSEDE", "SWITCH": "SUPERSEDE"}


def _action(raw):
    """(Action, exact): exact is False when the action was mapped from a synonym or unknown."""
    name = str(raw or "ADD").strip().upper()
    if name in Action.__members__:
        return Action(name), True
    try:
        return Action(_ACTION_SYNONYMS[name]), False
    except KeyError:
        log.warning("extractor returned an unknown action %r; flagging the fact for review", name)
        return Action.FLAG, False


_SWITCH_FROM = r"(?:switch(?:ed|ing)?|chang(?:e|ed|ing)|transition(?:ed|ing)?|convert(?:ed|ing)?)\s+(?:[a-z']+\s+){0,2}from\s+"
_STOP_PHRASE = (r"(?:stop|stopped|stopping|discontinue|discontinued|d/c(?:'?d)?|dc'?(?:e?d)|ceased|taken off"
                r"|came off|come off|(?:been|is|was|went|now|stays?) off|no longer (?:taking|on)|instead of)")


def _stop_named(text, ident):
    """True if `text` says to stop the drug `ident`: stop language right next to it, within a few words
    and with no punctuation between, so "stop warfarin, continue metformin" names only warfarin."""
    t = str(text).lower()
    d = "(?:" + "|".join(re.escape(n) for n in sorted(_names_for(ident))) + ")"
    return re.search(rf"(?<![a-z]){_STOP_PHRASE}\s+(?:[a-z']+\s+){{0,3}}{d}(?![a-z])"
                     rf"|(?<![a-z]){d}\s+(?:[a-z']+\s+){{0,2}}{_STOP_PHRASE}(?![a-z])"
                     rf"|{_SWITCH_FROM}{d}(?![a-z])", t) is not None


# Negated stop language: "do not stop the warfarin", "don't switch her yet". In review mode a missed
# negation only costs a wrong proposal; in auto mode it would be a wrong removal, so it is checked.
_NEGATED = re.compile(r"(?<![a-z])(?:do not|don't|dont|never|not to|avoid|no need to|hold off on|without)"
                      r"\s+(?:[a-z']+\s+){0,2}(?:stop|discontinu|d/c|cease|chang|switch|remov)")
_CONFIRM_MODE = os.environ.get("VAYL_CONFIRM_MODE", "").strip().lower()
if _CONFIRM_MODE not in ("", "auto", "review"):
    raise ValueError(f"VAYL_CONFIRM_MODE must be 'auto' or 'review', not {_CONFIRM_MODE!r}")


def _gate_mode(spec):
    """'auto' or 'review' for a gated slot; VAYL_CONFIRM_MODE overrides the slot's own mode."""
    return _CONFIRM_MODE or getattr(spec, "confirm_mode", "") or "review"


_SLUG = re.compile(r"[a-z0-9]+(?:[_-][a-z0-9]+)*_[a-z0-9-]+")   # snake_case, no spaces


def _names_item(text, item_value):
    """True if `text` names the list item `item_value` by its identity (the drug or substance name)."""
    ident = _list_identity(item_value)
    t = str(text).lower()
    return bool(ident) and any(re.search(rf"(?<![a-z]){re.escape(n)}(?![a-z])", t) for n in _names_for(ident))


def _list_identity(value):
    """The stable identity of a list item — the substance or drug name, before its reaction or dose.

    A list item is identified by WHAT it is, not its full description. "Penicillin — Anaphylaxis
    (severe)" and a later "Penicillin — Anaphylaxis" (a refute carrying less reaction detail) are the
    same allergy; "Warfarin 5 mg PO daily" and "Warfarin 3 mg" are the same drug at different doses.
    Cutting at the first separator or dose number yields "penicillin" / "warfarin", so a removal or
    change finds the right list member even when the description text differs.
    """
    v = str(value or "")
    m = _LIST_ID_CUT.search(v)
    ident = (v[:m.start()] if m else v).strip().lower()
    return _GENERIC.get(ident, ident)


# Brand names for the drugs where a missed match is most dangerous: oral anticoagulants, where an
# unrecognised "Coumadin stopped, now on Eliquis" leaves warfarin current beside apixaban. Not a drug
# dictionary; a deployment that needs one should map names upstream (e.g. RxNorm) before Vayl.
_GENERIC = {"coumadin": "warfarin", "jantoven": "warfarin", "eliquis": "apixaban", "xarelto": "rivaroxaban",
            "pradaxa": "dabigatran", "savaysa": "edoxaban", "lixiana": "edoxaban", "lovenox": "enoxaparin"}
ANTICOAGULANTS = {"warfarin", "apixaban", "rivaroxaban", "dabigatran", "edoxaban", "enoxaparin", "heparin"}


def _names_for(ident):
    """The identity and every brand name that folds onto it."""
    return {ident} | {b for b, g in _GENERIC.items() if g == ident}


def duplicate_anticoagulant(m, proposal):
    """Active anticoagulants that approving `proposal` would leave beside the one it adds, unless they
    are themselves proposed for removal. A reviewer approving "add Eliquis" while warfarin is current
    and its stop was never captured creates exactly the duplication this is here to catch."""
    new = _list_identity(proposal.value)
    if new not in ANTICOAGULANTS:
        return []
    leaving = {p.supersedes for p in m.pending()} | ({proposal.supersedes} if proposal.supersedes else set())
    return [s.value for s in m.active() if s.subject == proposal.subject and s.id not in leaving
            and _list_identity(s.value) in ANTICOAGULANTS and _list_identity(s.value) != new]


def _slot_target(active, subject, value, is_multi):
    """Which active fact a change/removal applies to. On a single-valued slot it is the one fact on
    the subject. On a LIST slot it must be the matching item — otherwise removing one list member
    (stop warfarin) would hit an arbitrary other member (metformin)."""
    on_slot = [s for s in active if s.subject == subject]
    if is_multi:
        exact = next((s for s in on_slot if canon(s.value) == canon(value)), None)
        if exact is not None:
            return exact
        # fall back to clinical identity: a refuted allergy or a stop order may carry different
        # description text than what was charted, but the substance/drug name still identifies it.
        vid = _list_identity(value)
        return next((s for s in on_slot if vid and _list_identity(s.value) == vid), None)
    return on_slot[0] if on_slot else None


def _is_event(s):
    """True if this statement records something that HAPPENED rather than a value that HOLDS.

    Events are exempt from the same-slot invariant: two races, two phone calls, two paintings are
    all true at once, and superseding one with another destroys the record. State facts keep the
    invariant — exactly one active value per (subject, scope) — because that is what makes "which
    is current?" answerable. Stored in metadata so no schema migration is needed.
    """
    return ((getattr(s, "metadata", None) or {}).get("kind") == "event")


class LLMMemory:
    def __init__(self, graph=None, ns="", policy=None):
        self.statements = []
        # Per-instance id allocation. Ids only need to be unique WITHIN a memory space, and a fresh
        # memory is a fresh space, so a per-instance counter starting at 1 is correct and — unlike a
        # process-global counter reassigned on every load() — carries NO shared mutable state across
        # spaces. That reassignment was the race the server's global lock existed to hide; removing
        # it is the prerequisite for per-space locking. Store.load() seeds this from the space's MAX.
        self._id_counter = itertools.count(1)
        self.graph = graph
        self.ns = ns                # (user/agent/run) namespace stamped on graph edges for scoped erasure
        self.policy = policy        # optional source-aware ReconcilePolicy for a shared space (Feature 4)
        # Wired by Store.load(): lazy loaders for embeddings and retired history, and a snapshot of the
        # loaded rows so save() writes only what changed. A fresh memory has none of them.
        self._hydrate: Callable[[], object] | None = None
        self._hydrate_history: Callable[[], list[Statement]] | None = None
        self._loaded: dict = {}

    def active(self):
        return [s for s in self.statements if s.status == Status.ACTIVE]

    def _mk(self, *args, **kwargs):
        """Construct a Statement with an id minted from THIS memory's counter, so id allocation
        never touches process-global state (the choke point every _apply site routes through)."""
        kwargs.setdefault("id", next(self._id_counter))
        return Statement(*args, **kwargs)

    def _gretire(self, subject):
        """Retire a superseded/retracted slot's projected edges by subject (head-agnostic), so the
        graph never serves an edge whose slot fact is no longer active — even when the superseding
        fact named a different head entity or carried no graph triple at all."""
        if self.graph and subject:
            try:
                self.graph.retire_subject_edges(str(subject), ns=self.ns)
            except Exception:
                log.warning("graph: could not retire a subject's edges; the graph may serve a stale "
                            "edge until it is rebuilt", exc_info=True)

    def _gwrite(self, o, act):
        """Mirror a fact into the Neo4j projection as an entity triple, if a graph is attached."""
        if not self.graph:
            return
        head, rel = o.get("head"), o.get("relation")
        tail = o.get("tail") or o.get("value")
        if not (head and rel and tail):
            return
        subj = str(o.get("subject") or "")
        # Entity resolution: keep a slot's edges on ONE head entity. If this subject already has
        # edges, reuse that head rather than the model's fresh naming of the same thing — otherwise
        # the update lands on a new node and a query about the original entity can never reach it.
        try:
            anchored = self.graph.head_for_subject(subj, ns=self.ns)
            if anchored and anchored != str(head):
                head = anchored
        except Exception:
            log.debug("graph: head lookup failed; using the extracted head", exc_info=True)
        if act in (Action.SUPERSEDE, Action.REFINE):
            self.graph.supersede_edge(str(head), str(rel), str(tail), ns=self.ns, subject=subj)
        elif act == Action.RETRACT:
            self.graph.retract_edge(str(head), str(rel), ns=self.ns)
        elif act == Action.COEXIST:
            self.graph.add_edge(str(head), str(rel), str(tail), ns=self.ns, subject=subj)  # both true
        elif act == Action.ADD:
            # Graph-edge invariant: for a FUNCTIONAL relation (one tail per head — routes_to,
            # reports_to, located_in…) a new tail retires the old edge, so a re-point the extractor
            # mislabelled ADD can't leave two live edges. Additive relations (MULTI_VALUED) coexist.
            self.graph.add_edge(str(head), str(rel), str(tail), ns=self.ns, subject=subj,
                                functional=_norm_rel(rel) not in MULTI_VALUED_RELS)

    def add(self, text, source=""):
        # ── PRE-LLM DEDUP ──
        # Skip extraction when this exact utterance already produced fact(s) that are ALL still
        # active: re-extraction would only DEDUP them (the RESTATEMENT branch in _apply), so the
        # outcome is identical and one LLM call is saved. If ANY fact from that utterance is no
        # longer active — the slot changed since — we fall through and extract, so a restatement
        # that should now supersede is never skipped. No staleness regression by construction.
        if _DEDUP_PREFILTER and text and text.strip():
            key = _norm_utterance(text)
            group = [s for s in self.statements if _norm_utterance(s.raw) == key]
            if group and all(s.status is Status.ACTIVE for s in group):
                return [(Action.DEDUP, s.subject, s.value) for s in group]
        # A message may carry several facts; extract all, reconcile each in order.
        # `source` is stamped on every fact created — belief provenance. Show the extractor only the
        # top-k RELEVANT active facts (all of them when the space is small) so write cost stays
        # bounded as memory grows; _apply still reconciles against the full active set.
        candidates = retrieval.embed_retrieve(text, self.active(), k=_RECONCILE_CONTEXT)
        before = {s.id for s in self.statements}
        results = [self._apply(o, text, source) for o in llm_extract_classify(text, candidates)]
        self._hold_duplicate_anticoagulants(before)
        return results + self._stop_sweep(text, source)

    def _hold_duplicate_anticoagulants(self, before):
        """Backstop for confirm "auto": an anticoagulant auto-added by this message while another
        stays current (a switch whose stop half the model missed) goes back to the review queue, so a
        switch applies whole or not at all. Runs after every fact in the message is applied, so a
        stop and a start in one sentence are judged together."""
        for s in [x for x in self.active() if x.id not in before and (x.metadata or {}).get("auto_applied")]:
            ident = _list_identity(s.value)
            others = [x for x in self.active() if x.subject == s.subject and x.id != s.id
                      and _list_identity(x.value) in ANTICOAGULANTS and _list_identity(x.value) != ident]
            if ident in ANTICOAGULANTS and others:
                rival = next((x for x in self.statements if x.id == s.supersedes), None)
                if rival is not None and rival.status is Status.SUPERSEDED:
                    rival.status = Status.ACTIVE          # undo the replacement it made
                s.status = Status.FLAGGED
                s.metadata = {**(s.metadata or {}), "auto_applied": None,
                              "pending": "SUPERSEDE" if rival is not None else "ADD",
                              "reason": "would leave two anticoagulants active"}

    def _stop_sweep(self, text, source=""):
        """The last safety net for a gated list: if the sentence itself says to stop a current item
        ("Warfarin was discontinued yesterday", "she stopped taking her warfarin", "switched from
        warfarin to apixaban") and no extracted fact proposed that, propose the removal now. The
        model sometimes files the stop under an invented subject or archives the drug's own value as
        history, and then the stop is simply lost. Only ever adds a proposal for a person to decide."""
        if (not SLOT_SCHEMA or str(source or "").lower() in _TRUSTED_SOURCES
                or has(text, HYPOTHETICAL_MARKERS) or has(text, SARCASM_MARKERS)
                or _NEGATED.search(str(text).lower())):
            return []
        proposed = {p.supersedes for p in self.pending()}
        out = []
        for s in list(self.active()):
            spec = SLOT_SCHEMA.resolve(s.subject)
            if (spec is None or not (spec.confirm and spec.multi) or s.id in proposed
                    or not _stop_named(text, _list_identity(s.value))):
                continue
            pend = self._mk(f"{s.subject}@{s.scope}", s.subject, s.value, s.scope, status=Status.FLAGGED,
                            confidence=0.9, raw=text, source=source, supersedes=s.id)
            pend.metadata = {"pending": Action.RETRACT.value, "category": spec.category or ""}
            self.statements.append(pend)
            out.append((Action.FLAG, s.subject, s.value))
        return out

    def forget(self, text, source=""):
        """An explicit 'forget X' must RETRACT — retire the fact — rather than depend on the
        extractor's own action guess (which might come back ADD/SUPERSEDE). We reuse the extractor
        only to resolve WHICH fact is meant, then force removal at high confidence."""
        out = []
        for o in llm_extract_classify(text, retrieval.embed_retrieve(text, self.active(), k=_RECONCILE_CONTEXT)):
            o["action"] = "RETRACT"
            o["confidence"] = max(float(o.get("confidence") or 0), 0.9)
            out.append(self._apply(o, text, source))
        return out

    def get(self, mid):
        """Fetch a single statement by id (or None)."""
        return next((s for s in self.statements if s.id == mid), None)

    def update(self, mid, new_value, source=""):
        """Audit-preserving edit by id: retire the old value (SUPERSEDED) and add a new ACTIVE
        statement carrying `new_value`. The old value stays visible in history() — unlike a silent
        overwrite. Returns the new statement, or None if the id isn't found."""
        old = self.get(mid)
        if old is None:
            return None
        old.status = Status.SUPERSEDED
        new = self._mk(old.slot, old.subject, new_value, old.scope,
                        confidence=old.confidence, supersedes=old.id, raw=f"(edit) {new_value}",
                        metadata=old.metadata, source=source or getattr(old, "source", ""))
        self.statements.append(new)
        return new

    def history(self, subject):
        """The full change-log for a subject (active + superseded + historical), oldest first."""
        return sorted([s for s in self.statements if s.subject == subject], key=lambda s: s.id)

    def _apply(self, o, text, source=""):
        act, exact = _action(o.get("action", "ADD")); conf = float(o.get("confidence", 0.5))
        # In a confirm "auto" slot only a CLEAR change applies by itself; anything that sets this
        # (a guessed action, a rerouted subject, a future-tense order, hedging, negation, low
        # confidence) still goes to the approval queue.
        uncertain = not exact
        auto_applied = False
        # coerce: the LLM occasionally emits null / list / number for a field
        subj = str(o.get("subject") or "unknown")
        val = str(o.get("value") or "(unspecified)")
        scope = str(o.get("scope") or "global")
        time_ref = str(o.get("time_ref") or "unknown").lower()
        kind = str(o.get("kind") or "state").lower()
        is_event = kind == "event"
        # Fold the extractor's chosen subject onto a declared slot when one matches, so a later
        # statement about the same thing reconciles against this one instead of opening a second
        # slot beside it. Undeclared subjects pass through untouched.
        spec = SLOT_SCHEMA.resolve(subj) if SLOT_SCHEMA else None
        if spec is not None:
            subj = spec.name
        is_multi = spec is not None and spec.multi   # slot holds a LIST (allergies, med list)
        target = next((s for s in self.active() if s.id == o.get("target_id")), None)
        if target is not None and target.subject != subj and act in (Action.SUPERSEDE, Action.REFINE, Action.RETRACT):
            # The model pointed at a fact but named a different subject ("metformin_therapy_status"
            # targeting metformin's row). The change lands on the target, so the target's slot rules
            # apply: otherwise a gated medication is edited or retired through an ungated name.
            tspec = SLOT_SCHEMA.resolve(target.subject) if SLOT_SCHEMA else None
            if tspec is not None and tspec.confirm:
                spec, subj = tspec, tspec.name
                is_multi = tspec.multi
                uncertain = True
        # ── STOP ON A GATED LIST ──
        # "Warfarin stopped today" / "D/C warfarin" / a whole "Stop the warfarin." often comes back as
        # an ADD or an ARCHIVE (the model filing it as history) whose VALUE names an active list item
        # with stop language. Taken literally that archives a history note or adds the sentence as a
        # new drug, and the drug stays active. On a confirm-required list it is a removal of the named item, so it goes to the
        # approval queue below. Runs before the valid-time gate, which would otherwise swallow it. A
        # hold is not a stop (FHIR "on-hold" vs "stopped"), and hedged or sarcastic text never counts.
        # It also runs before the event rule: a model that tags a stop kind="event" would otherwise
        # have it turned into an ADD (events never retract) and archived.
        if (spec is not None and spec.confirm and is_multi
                and act in (Action.ADD, Action.COEXIST, Action.REFINE, Action.ARCHIVE, Action.RETRACT)
                and _STOP_WORDS.search(val.lower()) and not _NEGATED.search(f"{text} {val}".lower())
                and not has(text, HYPOTHETICAL_MARKERS) and not has(text, SARCASM_MARKERS)):
            named = [s for s in self.active() if s.subject == subj and _names_item(val, s.value)]
            if len(named) == 1:
                act, target, is_event = Action.RETRACT, named[0], False
            elif not named and act != Action.RETRACT:
                # "off since 2026-03", "reason_discontinued: AKI": a stop statement that names no current
                # item is not a list entry. The stop itself is caught by name in _stop_sweep.
                return Action.SKIP, subj, val
        if (spec is not None and spec.confirm and is_multi and act == Action.RETRACT
                and target is not None and target.subject == subj):
            is_event = False   # the model named the item it removes; an "event" tag can't undo that

        if is_event:
            # An event never replaces anything: it is a new thing that happened, not a correction.
            # Forcing this here rather than trusting the extractor means a mislabelled SUPERSEDE on
            # an event cannot silently delete a previous event.
            if act in (Action.SUPERSEDE, Action.REFINE, Action.RETRACT):
                act = Action.ADD
            target = None

        # ── VALID-TIME GATE ──
        # A PAST statement is history, not present truth: record it, but never let it become
        # active or retire the current fact. Closes the out-of-order failure ("Historically we
        # billed monthly" must not overwrite "we now bill annually"). A FUTURE statement isn't true yet.
        # (RETRACT is exempt: "datadog is gone" is past-framed but means retire the CURRENT slot,
        #  not archive a historical value — otherwise the gate swallows the retraction.)
        if time_ref == "past" and act != Action.RETRACT:
            # The same past fact stated twice is still one fact. This gate returns before the
            # RESTATEMENT check below, so without its own guard every repetition appended another
            # HISTORICAL row — additive growth in the one place nothing ever prunes, since history
            # is never superseded. Observed: one subject accumulating five identical archived rows.
            if any(x.subject == subj and x.scope == scope and canon(x.value) == canon(val)
                   and x.status is Status.HISTORICAL for x in self.statements):
                return Action.DEDUP, subj, val
            st = self._mk(f"{subj}@{scope}", subj, val, scope,
                           status=Status.HISTORICAL, confidence=conf, raw=text, source=source)
            self.statements.append(st)
            return Action.ARCHIVE, subj, val
        if time_ref == "future" and act in (Action.SUPERSEDE, Action.COEXIST, Action.ADD):
            # "Increase metformin to 1000 mg" / "Start apixaban" read as future. On a gated slot that is
            # an order, so it goes to the approval queue below instead of a flag outside it, as long
            # as the gate will catch it (an ADD to an empty gated slot would otherwise apply now).
            gated = (spec is not None and spec.confirm and str(source or "").lower() not in _TRUSTED_SOURCES
                     and (act == Action.SUPERSEDE or any(s.subject == subj for s in self.active())))
            if not gated:
                act = Action.FLAG   # pending — surface it, don't assert it as current
            uncertain = True        # an order for later: a person decides when it takes effect

        # ── DETERMINISTIC RETRACT UPGRADE ──
        # A removal the extractor mislabelled ("ServiceA no longer calls ServiceB" coming back as
        # ADD). Like the same-slot invariant, this does not ask the model to be smarter — it fires
        # only when the language is DEFINITE (hedges and sarcasm are excluded) *and* the extracted
        # value names the fact being removed rather than a replacement, i.e. an active fact on this
        # slot already holds that value. That second condition is what separates "we dropped Sentry"
        # (RETRACT) from "we moved from Sentry to Rollbar" (SUPERSEDE, which names a new value), and
        # is why a hesitant "considering dropping X" can never delete a still-true fact.
        if (not is_event
                and act in (Action.ADD, Action.SUPERSEDE, Action.COEXIST, Action.REFINE)
                and has(text, RETRACT_MARKERS)
                and not has(text, HYPOTHETICAL_MARKERS) and not has(text, SARCASM_MARKERS)
                and any(s.subject == subj
                        and (canon(s.value) == canon(val)
                             or (val == "(unspecified)" and not is_multi))
                        for s in self.active())):
            act = Action.RETRACT

        # ── RESTATEMENT ──
        # The same fact said again carries no new information. Without this, a repeated assertion
        # appends a second identical ACTIVE row — additive behaviour leaking into a reconciling
        # store, and a slow drift away from "one active value per slot".
        if (act in (Action.ADD, Action.SUPERSEDE, Action.COEXIST, Action.REFINE)
                and any(s.subject == subj and s.scope == scope and canon(s.value) == canon(val)
                        for s in self.active())):
            return Action.DEDUP, subj, val

        # ── FRAGMENT RESOLUTION ──
        # Same value, near-identical subject, but the subject STRING differs — a fragmented
        # restatement the exact check above missed. Fold it into the existing slot. Guarded three
        # ways so it can only ever be a dedup: same canonical value (so nothing is overwritten),
        # state only (an event must never fold into another event), and ADD only (a real supersede
        # or retract names a target and is not touched here).
        if (_SLOT_RESOLVE and not is_event and act == Action.ADD and spec is None):
            twin = next((s for s in self.active()
                         if s.scope == scope and not _is_event(s)
                         and canon(s.value) == canon(val)
                         and s.subject != subj and _subject_similar(s.subject, subj)), None)
            if twin is not None:
                return Action.DEDUP, twin.subject, val

        # ── CONFIRMATION GATE ──
        # Some slots are ones where the WRITE is the hazard, not just a wrong read. Reconciliation
        # is driven by an LLM reading conversational text, and "we should stop the warfarin"
        # appearing in a sentence is not an order to stop it. For a confirm-required slot a
        # replacement or removal is PROPOSED: it is recorded as pending, the current value stands
        # untouched, and a person decides. Placed before RETRACT and before the same-slot invariant
        # so it catches both paths.
        # A verbatim gated list stores values as stated ("apixaban 5 mg twice daily"). A machine slug
        # there ("held_for_contrast", "acute_kidney_injury") is a status or a reason the model filed
        # in the wrong slot, not an entry; proposing it put "REPLACE metformin -> held_for_contrast"
        # in front of a reviewer. The real change, if any, is captured separately.
        if (spec is not None and spec.confirm and is_multi and getattr(spec, "verbatim", False)
                and act in (Action.ADD, Action.COEXIST, Action.SUPERSEDE, Action.REFINE)
                and _SLUG.fullmatch(val.strip().lower())):
            return Action.SKIP, subj, val

        # An ADD to a gated slot that already holds a value is a change too: on a list it adds a drug
        # (and, beside a pending stop, can leave two anticoagulants active); on a single slot the
        # same-slot invariant below would quietly supersede the current value. Only the first write
        # to an empty gated slot goes through unreviewed (there is nothing to lose yet).
        gate_add = (act in (Action.ADD, Action.COEXIST)
                    and any(s.subject == subj for s in self.active()))
        if (spec is not None and spec.confirm
                and (act in (Action.SUPERSEDE, Action.RETRACT, Action.REFINE) or gate_add)
                and str(source or "").lower() not in _TRUSTED_SOURCES):
            tgt = target or _slot_target(self.active(), subj, val, is_multi)
            # a list item that names no current entry is a NEW item, even when the model called it a
            # replacement; otherwise a SUPERSEDE with no identifiable target skipped the gate entirely
            new_item = tgt is None and is_multi and act in (Action.ADD, Action.COEXIST, Action.SUPERSEDE, Action.REFINE)
            if gate_add and tgt is not None:
                act = Action.SUPERSEDE   # names an existing item (a dose change) or a single slot's value
            uncertain = (uncertain or conf < AUTO_THRESHOLD or has(text, HYPOTHETICAL_MARKERS)
                         or has(text, SARCASM_MARKERS) or _NEGATED.search(text.lower()) is not None)
            if _gate_mode(spec) == "auto" and not uncertain and (tgt is not None or new_item):
                # A clear change on an "auto" slot applies now, below, and is marked auto-applied.
                target, auto_applied = tgt, True
                if new_item:
                    act = Action.ADD
            elif new_item:
                # a new item for the list: propose adding it, beside (not instead of) what is there
                if any(x.subject == subj and (x.metadata or {}).get("pending") == "ADD"
                       and canon(x.value) == canon(val) for x in self.pending()):
                    return Action.DEDUP, subj, val
                pend = self._mk(f"{subj}@{scope}", subj, val, scope,
                                status=Status.FLAGGED, confidence=conf, raw=text, source=source)
                pend.metadata = {"pending": "ADD", "category": spec.category or ""}
                self.statements.append(pend)
                return Action.FLAG, subj, val
            elif tgt is not None:
                # The same change proposed twice is one decision, not two. Without this, repeating
                # "switch to apixaban" stacked identical rows in the review queue — the same
                # early-return-without-dedup shape as the valid-time gate. A RETRACT proposal is
                # keyed on the target alone (it has no distinct new value to compare).
                dup = next((x for x in self.pending()
                            if x.supersedes == tgt.id and (x.metadata or {}).get("pending") == act.value
                            and (act == Action.RETRACT or canon(x.value) == canon(val))), None)
                if dup is not None:
                    return Action.DEDUP, subj, val
                pend = self._mk(f"{subj}@{scope}", subj, val, scope,
                                 status=Status.FLAGGED, confidence=conf, raw=text, source=source,
                                 supersedes=tgt.id)
                pend.metadata = {"pending": act.value, "category": spec.category or ""}
                self.statements.append(pend)
                return Action.FLAG, subj, val

        # ── RETRACT: removal with no replacement — retire the fact, leave slot empty ──
        if act == Action.RETRACT:
            tgt = target or _slot_target(self.active(), subj, val, is_multi)
            if tgt is None:
                return Action.SKIP, subj, val
            if conf < AUTO_THRESHOLD:                          # unsure — don't delete a possibly-true fact
                self.statements.append(self._mk(f"{subj}@{scope}", subj, tgt.value, scope,
                                                 status=Status.FLAGGED, confidence=conf, raw=text, source=source))
                return Action.FLAG, subj, tgt.value
            tgt.status = Status.SUPERSEDED
            tomb = self._mk(f"{subj}@{scope}", subj, f"(retracted: {tgt.value})", scope,
                            status=Status.HISTORICAL, confidence=conf,
                            supersedes=tgt.id, raw=text, source=source)   # tombstone for provenance
            if auto_applied:
                tomb.metadata = {"auto_applied": True}
            self.statements.append(tomb)
            self._gretire(getattr(tgt, "subject", subj))   # drop the edge by subject (head-agnostic)
            self._gwrite(o, Action.RETRACT)
            return Action.RETRACT, subj, tgt.value

        # honest-uncertainty gate
        if act in (Action.SUPERSEDE, Action.COEXIST, Action.REFINE) and conf < AUTO_THRESHOLD:
            act = Action.FLAG
        if act in (Action.SKIP, Action.DEDUP):
            return act, subj, val
        if act == Action.REFINE and target:
            # Source-aware guard (Feature 4): a cross-source REFINE that CHANGES the value is an
            # overwrite path, so gate it by the same policy as SUPERSEDE — else a low-authority
            # source could sneak a change in as a "refinement".
            from vayl.memory import orgmemory
            if (val != target.value and self.policy
                    and orgmemory.resolve(source, getattr(target, "source", ""), self.policy) == "flag"):
                st = self._mk(f"{subj}@{scope}", subj, val, scope,
                               status=Status.FLAGGED, confidence=conf, raw=text, source=source)
                self.statements.append(st)
                return Action.FLAG, subj, val
            target.value = val; target._emb = None   # value changed -> re-embed on save
            if auto_applied:
                target.metadata = {**(target.metadata or {}), "auto_applied": True}
            self._gwrite(o, Action.REFINE); return act, subj, val
        st = self._mk(f"{subj}@{scope}", subj, val, scope, confidence=conf, raw=text, source=source)
        if is_event:
            st.metadata = {**(st.metadata or {}), "kind": "event"}
        if act == Action.FLAG and o.get("reason"):
            # A flagged fact lands in a human review queue, and "why" is the first thing that reader
            # needs. The extractor already writes this; discarding it paid for the tokens and kept
            # none of the value.
            st.metadata = {**(st.metadata or {}), "reason": str(o.get("reason"))[:200]}
        if auto_applied:
            st.metadata = {**(st.metadata or {}), "auto_applied": True}
        if spec is not None and spec.category:
            # Declaring a slot's category is what makes the critical-fact channel usable: an
            # operator names the slots once instead of every caller remembering to tag each write.
            st.metadata = {**(st.metadata or {}), "category": spec.category}
        # Keep the graph triple with the fact, so the projection can be rebuilt from the store.
        st.head = str(o.get("head") or "")
        st.relation = str(o.get("relation") or "")
        st.tail = str(o.get("tail") or o.get("value") or "")
        if act == Action.FLAG:
            st.status = Status.FLAGGED
        else:
            # SAME-SLOT INVARIANT: at most one ACTIVE value per (subject, scope). Resolve against the
            # LLM-named target when it gave one; otherwise — the deterministic guard — against any
            # active fact on the SAME slot this one contradicts. This catches the weak-model failure
            # where a real change comes back as ADD (or SUPERSEDE with an unlinked target_id) and both
            # values are left active, which is exactly what makes a store answer with a stale value.
            # Two contradictory actives on one slot never survive: recency wins the slot, unless a
            # source-authority policy forbids the overwrite (then the current value stands and the
            # incoming one is flagged). A real COEXIST differs by SCOPE and so never matches the
            # same-(subject, scope) rival below; one labelled COEXIST on the SAME scope is a change
            # the model misnamed, and is checked like one rather than left beside the old value.
            # An event has no rival: it does not occupy a single-valued slot — EXCEPT when the operator
            # declared the slot single-valued AND a different value is already active there. Then the
            # incoming fact contradicts the current value, so it is a state change and must supersede,
            # even if a weak extractor mislabelled it kind="event" (the failure that otherwise leaves
            # "Redux" active beside "Zustand"). An event on an as-yet-empty declared slot has no rival
            # and stays an event; free-form and list slots keep the plain event exemption.
            declared_single = spec is not None and not is_multi
            if is_event and not declared_single:
                rival = None
            elif act == Action.SUPERSEDE and target:
                rival = target                            # the extractor/feed named the item
            elif is_multi:
                # A list slot: a bare ADD joins the list (no rival). An explicit SUPERSEDE replaces
                # the matching item, found by drug/substance identity — that is how a dose or route
                # change reconciles ("atorvastatin 20" -> "atorvastatin 80") without an id. A new
                # drug with no identity match has no rival and simply joins the list.
                rival = (_slot_target([s for s in self.active() if not _is_event(s)], subj, val, True)
                         if act == Action.SUPERSEDE else None)
            elif act in (Action.ADD, Action.SUPERSEDE, Action.REFINE, Action.COEXIST):
                # A declared single-valued slot admits an active event as a rival too, so a mislabelled
                # state-change retires the incumbent (and later switches keep reconciling cleanly).
                rival = next((s for s in self.active()
                              if s.subject == subj and s.scope == scope and s.value != val
                              and (declared_single or not _is_event(s))), None)
            else:
                rival = None
            if rival is not None:
                # Source-aware reconciliation (Feature 4): whether a new source may overwrite
                # another's active fact is a policy decision; on 'flag' the current value STANDS.
                from vayl.memory import orgmemory
                if orgmemory.resolve(source, getattr(rival, "source", ""), self.policy) == "flag":
                    st.status = Status.FLAGGED
                    act = Action.FLAG
                else:
                    rival.status = Status.SUPERSEDED; st.supersedes = rival.id
                    act = Action.SUPERSEDE
                    self._gretire(rival.subject)   # retire the old edge by subject before writing the new
        self.statements.append(st)
        self._gwrite(o, act)
        return act, subj, val

    def view(self):
        act = self.active()
        flg = [s for s in self.statements if s.status == Status.FLAGGED]
        sup = [s for s in self.statements if s.status == Status.SUPERSEDED]
        hist = [s for s in self.statements if s.status == Status.HISTORICAL]
        return act, flg, sup, hist

    @staticmethod
    def provenance(s):
        """The belief-lineage record of one fact the agent consulted — the truthful basis for
        'why did the agent do X?' (Feature 1) and the safety gate (Feature 2). Exactly the fields
        needed to explain and to judge: value, who asserted it, when, confidence, what it superseded."""
        return {"id": s.id, "subject": s.subject, "value": s.value, "status": s.status.value,
                "confidence": round(float(s.confidence), 4), "supersedes": s.supersedes,
                "source": getattr(s, "source", "") or "", "set_at": getattr(s, "created_at", None)}

    def query(self, question, retrieve=None, k=None, with_provenance=False,
              include_history=False, critical_categories=None):
        """Answer a question over the facts — including multi-hop and history ('what did we use first?').
        Uses embedding retrieval by default (top-k relevant) so it scales; falls back to all facts when
        the memory is small or un-embedded. Pass a custom `retrieve(question, statements, k)` to override.

        With `with_provenance=True`, returns (answer, used_facts) where used_facts is the EXACT set of
        statements placed into the model's context — not a re-derivation. That exactness is what makes
        the decision audit trustworthy: it cites what was actually believed, not what looks plausible now.

        `include_history` is OFF by default, and deliberately so. With it off, retired facts are not
        merely filtered out of the answer — they are never loaded, so a superseded value CANNOT be
        returned as current no matter how the model behaves. That structural guarantee is the whole
        product, and it should not be weakened into a tag the model is trusted to respect.

        Turn it on for questions that are explicitly about the past — "what did we use before", "when
        did we switch" — where retired facts ARE the answer. They arrive tagged `(history)`, and the
        answer prompt is instructed to treat them as former values.

        `critical_categories` overrides VAYL_CRITICAL_CATEGORIES for this call. Facts in those
        categories skip ranking and are always present in the context — for domains where a fact
        being ranked out is a safety failure rather than a quality one."""
        ret = retrieve or retrieval.embed_retrieve
        k = k if k is not None else _RECALL_CONTEXT
        # Vectors are left on disk by load() because they are large and reconciliation never reads
        # them. A ranking pass does — and only happens when the space exceeds the cap — so pull them
        # back at exactly that point, and only then.
        if len(self.statements) > k:
            hydrate = getattr(self, "_hydrate", None)
            if hydrate:
                try:
                    hydrate()
                except Exception:
                    pass                     # ranking degrades to lexical; never fail a read for it
        # Retired facts are the answer to any question about the past, and load() leaves them on
        # disk because the write path never wants them. Pull them in here — the read path — so the
        # (history) branch below can actually fire. Ranked together with active facts so a history
        # row only surfaces when it is genuinely relevant, and held in a separate pool so save()
        # never mistakes a loaded history row for a new statement.
        pool = self.statements + (self._history() if include_history else [])
        # Critical facts are separated BEFORE ranking and rejoined after, so they occupy no
        # ranked slots and can never be crowded out by a better-matching but less important fact.
        critical = [s for s in pool if is_critical(s, critical_categories)]
        if len(critical) > _CRITICAL_BUDGET:
            raise CriticalOverflow(
                f"{len(critical)} facts in critical categories exceeds the context budget of "
                f"{_CRITICAL_BUDGET}. Raise VAYL_CRITICAL_BUDGET or narrow "
                f"VAYL_CRITICAL_CATEGORIES — truncating them would hide exactly the facts that "
                f"must not be hidden.")
        rankable = [s for s in pool if not is_critical(s, critical_categories)]
        try:
            stmts = ret(question, rankable, max(k - len(critical), 1)) if rankable else []
        except Exception:
            stmts = rankable                 # if the embedder is unavailable, degrade to all-facts
        stmts = critical + [s for s in stmts if s not in critical]
        used = [s for s in stmts if s.status in (Status.ACTIVE, Status.SUPERSEDED, Status.HISTORICAL)]
        cur = [_ctx_line(s) for s in stmts if s.status == Status.ACTIVE]
        old = [f"(history) {_ctx_line(s)}" for s in stmts
               if s.status in (Status.SUPERSEDED, Status.HISTORICAL)]
        context = "; ".join(cur + old) or "(no facts stored)"
        answer = _qa(context, question)
        if with_provenance:
            return answer, [self.provenance(s) for s in used]
        return answer

    def pending(self):
        """Proposed changes awaiting a human decision, oldest first."""
        return [s for s in self.statements
                if s.status is Status.FLAGGED and (s.metadata or {}).get("pending")]

    def confirm(self, mid, source=""):
        """Apply a pending change. Returns (Action, subject, value), or None if `mid` isn't pending.

        Only now does the proposed write actually happen — the whole point of the gate is that
        nothing was applied when the sentence was read.
        """
        st = next((s for s in self.pending() if s.id == mid), None)
        if st is None:
            return None
        proposed = (st.metadata or {}).get("pending")
        if proposed == "ADD":
            # A new list item: nothing is replaced. If the same value became active meanwhile, adding
            # it again would duplicate it, so the proposal is stale instead.
            if any(s.subject == st.subject and canon(s.value) == canon(st.value) for s in self.active()):
                st.metadata = {**(st.metadata or {}), "pending": None, "resolved": "stale"}
                return None
            st.status = Status.ACTIVE
            st.metadata = {**(st.metadata or {}), "pending": None,
                           "resolved": "confirmed", "confirmed_by": source or "unknown"}
            return Action.ADD, st.subject, st.value
        target = next((s for s in self.statements if s.id == st.supersedes), None)
        if target is None or target.status is not Status.ACTIVE:
            # The value it was going to replace is already gone; applying now would resurrect a
            # decision made against state that no longer holds.
            st.metadata = {**(st.metadata or {}), "pending": None, "resolved": "stale"}
            return None

        target.status = Status.SUPERSEDED
        st.metadata = {**(st.metadata or {}), "pending": None,
                       "resolved": "confirmed", "confirmed_by": source or "unknown"}
        self._gretire(target.subject)
        if proposed == Action.RETRACT.value:
            st.status = Status.HISTORICAL
            st.value = f"(retracted: {target.value})"
            return Action.RETRACT, st.subject, target.value
        st.status = Status.ACTIVE
        return Action.SUPERSEDE, st.subject, st.value

    def reject(self, mid, source=""):
        """Discard a pending change. The current value stands and the proposal is kept as history —
        that someone proposed stopping a medication is itself worth being able to audit."""
        st = next((s for s in self.pending() if s.id == mid), None)
        if st is None:
            return None
        st.status = Status.HISTORICAL
        st.metadata = {**(st.metadata or {}), "pending": None,
                       "resolved": "rejected", "rejected_by": source or "unknown"}
        return Action.SKIP, st.subject, st.value

    def _history(self):
        """Retired statements for this space, fetched once and cached.

        Empty for a memory that was never persisted (nothing has been retired to disk yet) and for
        any caller that did not come through Store.load — both of which already hold everything
        they know in `self.statements`.
        """
        cached = getattr(self, "_history_pool", None)
        if cached is not None:
            return cached
        fetch = getattr(self, "_hydrate_history", None)
        pool: list[Statement] = []
        if fetch:
            try:
                pool = fetch() or []
            except Exception:
                pool = []                    # a read must never fail for want of history
        self._history_pool = pool
        return pool

    def check(self, subject, policy=None, now=None):
        """Safety verdict (Feature 2): is it safe to ACT on what we currently know about `subject`?
        Deterministic — evaluates the active/flagged facts for the subject against the policy and
        returns {ok, subject, reasons, facts}. A FLAGGED conflict, low confidence, staleness, or a
        just-changed value blocks the action, with an explicit reason for each."""
        from vayl.security.safety import SafetyPolicy, evaluate_fact
        policy = policy or SafetyPolicy()
        now = now if now is not None else time.time()
        facts = [s for s in self.statements
                 if s.subject == subject and s.status in (Status.ACTIVE, Status.FLAGGED)]
        if not facts:
            return {"ok": False, "subject": subject,
                    "reasons": ["no active fact for this subject — nothing safe to act on"], "facts": []}
        reasons, checked = [], []
        for s in facts:
            p = self.provenance(s)
            ok, rs = evaluate_fact(p, policy, now)
            checked.append({**p, "ok": ok, "reasons": rs})
            reasons += rs
        # one line per distinct reason: two pending proposals are one "disputed", not two
        reasons = list(dict.fromkeys(reasons))
        return {"ok": len(reasons) == 0, "subject": subject, "reasons": reasons, "facts": checked}

    def safe_recall(self, question, policy=None, retrieve=None, k=None, now=None,
                    critical_categories=None):
        """Gated recall (Feature 2): answer only if every current fact behind the answer is safe to
        act on. Reuses Phase-1 provenance to find the facts the answer rests on, checks each against
        the policy, and also catches an unresolved conflict on any of those subjects. Returns
        {ok, answer|None, reasons, used}. When ok is False, `answer` is withheld and `reasons` says why."""
        from vayl.security.safety import SafetyPolicy, evaluate_fact
        policy = policy or SafetyPolicy()
        now = now if now is not None else time.time()
        # Pass the critical categories down: this gate can only judge facts it was handed, so a
        # critical fact ranked out of context would be invisible to it — the gate would pass on a
        # quietly incomplete answer, which is the failure it exists to prevent.
        answer, used = self.query(question, retrieve=retrieve, k=k, with_provenance=True,
                                  critical_categories=critical_categories)
        active_used = [p for p in used if p["status"] == Status.ACTIVE.value]
        reasons = []
        if not active_used:
            reasons.append("no current fact supports this — nothing safe to act on")
        subjects = set()
        for p in active_used:
            _ok, rs = evaluate_fact(p, policy, now)
            reasons += rs
            subjects.add(p["subject"])
        if policy.block_on_flagged:
            for s in self.statements:
                if s.status == Status.FLAGGED and s.subject in subjects:
                    reasons.append(f"unresolved conflict on '{s.subject}' — the value is disputed")
        reasons = list(dict.fromkeys(reasons))
        ok = len(reasons) == 0
        return {"ok": ok, "answer": answer if ok else None, "reasons": reasons, "used": active_used}

    def graph_query(self, question, hops=2, k=15):
        """Deep/relational query via the Neo4j projection. Scales three ways: seed entities via the
        full-text index (O(log N)), pull a CAPPED neighborhood (hubs can't explode), then
        relevance-rank the edges to top-k so the LLM context stays bounded regardless of graph size.
        Returns (answer, seed_entities, retrieved_edges)."""
        if not self.graph:
            return self.query(question), [], []
        ns = self.ns   # scope every graph read to this (user/agent/run) tenant — no cross-tenant leak
        # Primary: in-DB vector index over edge embeddings — sub-second, scales, no hub cap.
        try:
            triples = self.graph.vector_search(llm_client._embed([question])[0], k, ns=ns)
            if triples:
                context = "; ".join(f"{h} {rel} {t}" for h, rel, t in triples)
                return _qa(context, question), ["(vector)"], triples
        except Exception:
            log.debug("graph: vector search unavailable; falling back to entity search", exc_info=True)
        # Fallback (no embeddings/index): index-seed -> capped neighborhood -> python rank.
        seeds = self.graph.search_entities(question, ns=ns)
        if not seeds:
            sample = self.graph.all_edges(valid_only=False, limit=2000, ns=ns)
            names = {h for h, _, _, _ in sample} | {t for _, _, t, _ in sample}
            ql = question.lower(); seeds = [n for n in names if n.lower() in ql]
        edges = (self.graph.neighborhood(seeds, hops=hops, limit=800, ns=ns) if seeds
                 else self.graph.all_edges(limit=800, ns=ns))
        triples = retrieval._rank_triples(question, [(h, rel, t) for h, rel, t, vv in edges if vv], k)
        context = "; ".join(f"{h} {rel} {t}" for h, rel, t in triples) or "(no facts)"
        return _qa(context, question), seeds, triples
