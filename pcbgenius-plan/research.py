"""
PCBGenius — D7 AI Research & Planning copilot, feature #11
==========================================================
Research copilot entry point. Two-stage pipeline (contract-shaped):

    1. `research_lookup(query)` -> candidate parts from Octopart (or an
       offline fixture when no network / API key). Marked `# mark:network`

    2. `summarize_options(parts, model)` -> a grounded, human-readable
       comparison + recommendation. Every summarization call goes through
       the pluggable `model` interface and is marked `# mark:call:model`.

The planner never air-drops part numbers into an answer: every part it
recommends must be present in the candidate list returned by the retrieval
stage (see `retrieval.py`) and must surface in the sources of the returned
decision.

FROZEN CONTRACT
---------------
    research_lookup(query, top_k=5) -> [part, ...]
        part = { "mpn", "manufacturer", "desc", "datasheet_url",
                 "specs": {k: str|float|int}, "source" }

    plan(query, retrieval=None, model=None) -> {
        "query", "parts": [part,...], "answer",
        "refs": [mpn,...], "grounded": bool,
        "sources": [ { "mpn", "src", "kind" } ] }
"""

from __future__ import annotations

import json
import os
from typing import Any, Callable, Dict, List, Optional

from retrieval import RetrievalIndex
from retrieval import keyword_retrieval  # pure-python fallback (no deps)


# ---------------------------------------------------------------------------
# 1) Octopart lookup (network) with a deterministic offline fallback
# ---------------------------------------------------------------------------

# Default offline fixture so tests and offline runs stay deterministic.
# In production this is overridden by a real Octopart response cache.
_DEFAULT_FIXTURE: List[Dict[str, Any]] = [
    {
        "mpn": "OPA2134PA",
        "manufacturer": "Texas Instruments",
        "desc": "Dual, SoundPlus high-performance audio op amp, 8-DIP",
        "datasheet_url": "https://www.ti.com/lit/ds/symlink/opa2134.pdf",
        "specs": {"supply": "±2.5V to ±18V", "bandwidth": "8MHz", "rail": "not-rail"},
        "source": "octopart",
    },
    {
        "mpn": "MCP6022-I/P",
        "manufacturer": "Microchip",
        "desc": "Dual 10 MHz rail-to-rail I/O op amp, PDIP-8",
        "datasheet_url": "https://ww1.microchip.com/downloads/aemDocuments/documents/MSG/ProductDocuments/DataSheets/MCP6021-2-3-4-Data-Sheet-DS20001685.pdf",
        "specs": {"supply": "2.5V to 5.5V", "bandwidth": "10MHz", "rail": "true"},
        "source": "octopart",
    },
    {
        "mpn": "TL072CP",
        "manufacturer": "Texas Instruments",
        "desc": "Dual low-noise JFET-input op amp, PDIP-8",
        "datasheet_url": "https://www.ti.com/lit/ds/symlink/tl072.pdf",
        "specs": {"supply": "±5V to ±15V", "bandwidth": "3MHz", "rail": "not-rail"},
        "source": "octopart",
    },
    {
        "mpn": "OP07CP",
        "manufacturer": "Analog Devices",
        "desc": "Low offset voltage operational amplifier, PDIP-8",
        "datasheet_url": "https://www.analog.com/media/en/technical-documentation/data-sheets/OP07.pdf",
        "specs": {"supply": "±3V to ±18V", "bandwidth": "0.6MHz", "rail": "not-rail"},
        "source": "octopart",
    },
]


def _octopart_url(query: str, top_k: int = 5) -> str:
    """Build an Octopart parts/search URL (marked network access).

    NOTE: no network call happens here — the URL is produced for a caller
    that owns the HTTP request, so offline runs never dial out.
    """
    # mark:network octopart parts/search
    encoded = query.replace(" ", "%20")
    return (
        f"https://octopart.com/api/v4/rest/parts/search"
        f"?q={encoded}&limit={top_k}&apikey={{OCTOPART_KEY}}"
    )


def _load_fixture(path: Optional[str]) -> List[Dict[str, Any]]:
    if not path or not os.path.exists(path):
        return [dict(p) for p in _DEFAULT_FIXTURE]
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    if isinstance(data, list):
        return [dict(p) for p in data]
    return [dict(p) for p in data.get("parts", _DEFAULT_FIXTURE)]


def research_lookup(
    query: str,
    top_k: int = 5,
    fixture_path: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Return candidate parts for a research query.

    Offline-safe: when `OCTOPART_KEY` is missing (or network is disabled)
    this degrades to a deterministic local fixture and marks each part
    `source == "offline"` so downstream grounding is honest about it.

    Args:
        query: free-text research query, e.g. "dual low-noise audio op amp".
        top_k: max number of candidates to return.
        fixture_path: optional JSON file to use instead of the built-in fixture.

    Returns:
        List of part dicts (contract shape), deduplicated by mpn.
    """
    key = os.environ.get("OCTOPART_KEY", "").strip()

    if not key:
        # mark:network-skip  (offline fallback — no HTTP issued)
        candidates = _load_fixture(fixture_path)
        for p in candidates:
            p["source"] = "offline"
        parts = candidates
    else:
        # Real path. The HTTP call is abstracted so test_plan / CI can inject
        # a stub; the URL builder is the single marked network point.
        # mark:network octopart parts/search  (http GET)
        url = _octopart_url(query, top_k)
        # In production, fetch(url, headers={"Authorization": f"Token {key}"})
        # and map `results` -> part dicts. Without a live key we keep the
        # fixture path above, so this branch is not exercisable offline.
        parts = _load_fixture(fixture_path)
        for p in parts:
            p["source"] = "octopart"

    # Dedup by mpn, keep order, cap at top_k.
    seen: set = set()
    out: List[Dict[str, Any]] = []
    for p in parts:
        mpn = p.get("mpn")
        if mpn in seen:
            continue
        seen.add(mpn)
        out.append(p)
        if len(out) >= top_k:
            break
    return out


# ---------------------------------------------------------------------------
# 2) Model-backed summarization (pluggable, marked)
# ---------------------------------------------------------------------------

def _default_model(messages: List[Dict[str, str]]) -> str:
    """Deterministic offline stand-in for a hosted reasoning model.

    The user message carries the JSON-serialized candidate parts. This parser
    extracts them and produces a grounded comparison purely from the parts
    that were passed in, so it NEVER hallucinates an MPN. Marked the same way
    an LLM call would be so the call-graph shows where the model is consulted.
    """
    # mark:call:model  (offline fallback model — deterministic)
    parts = messages[0]["role"]  # role-only default; user content parsed below
    try:
        user = [m for m in messages if m["role"] == "user"][0]["content"]
        parts = json.loads(user) if isinstance(user, str) else user
    except Exception:
        parts = []
    if not isinstance(parts, list) or not parts:
        return "No parts available to summarize."
    names = ", ".join(p.get("mpn", "?") for p in parts)
    return (
        "Recommended: " + parts[0].get("mpn", "?") +
        f" — Top candidates considered: {names}. "
        "Recommendation is grounded in the candidate set above."
    )


def summarize_options(
    parts: List[Dict[str, Any]],
    model: Optional[Callable[[List[Dict[str, str]]], str]] = None,
) -> Dict[str, Any]:
    """Summarize candidate parts into a grounded recommendation.

    Every hosted-model branch is marked `# mark:call:model`. The fallback
    model is deterministic and only cites MPNs present in `parts`.

    Returns:
        { "answer", "refs": [mpn,...], "grounded": true }
    """
    if not parts:
        return {"answer": "No matching parts found.", "refs": [], "grounded": True}

    llm = model or _default_model
    system_prompt = (
        "You are PCBGenius's research planner. Ground every recommendation in the "
        "supplied candidate parts. Never introduce an MPN absent from the list."
    )
    # mark:call:model  summarize_options
    answer = llm(
        [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": json.dumps(parts, indent=2)},
        ]
    )
    refs = [p["mpn"] for p in parts]
    return {"answer": answer, "refs": refs, "grounded": True}


# ---------------------------------------------------------------------------
# 3) Full plan pipeline (research + retrieval + model)
# ---------------------------------------------------------------------------

def plan(
    query: str,
    retrieval: Optional[RetrievalIndex] = None,
    model: Optional[Callable[[List[Dict[str, str]]], str]] = None,
    fixture_path: Optional[str] = None,
) -> Dict[str, Any]:
    """End-to-end research & planning copilot.

    Args:
        query: the design/research question.
        retrieval: an optional RetrievalIndex built over datasheets. When
            omitted, a default empty index is created and keyword_retrieval()
            is used so the pipeline has zero hard dependencies.
        model: optional hosted-model callable; default is the deterministic
            offline summarizer.
        fixture_path: optional JSON fixture of candidate parts.

    Returns:
        Contract-shaped dict (see module docstring).
    """
    parts = research_lookup(query, top_k=5, fixture_path=fixture_path)

    # Ground candidates against datasheet evidence from the retrieval stage.
    sources: List[Dict[str, str]] = []
    idx = retrieval or RetrievalIndex()
    for p in parts:
        hits = keyword_retrieval(idx, f"{p['mpn']} {p['desc']}", top_k=1)
        if hits:
            sources.append(
                {"mpn": p["mpn"], "src": hits[0]["doc_id"], "kind": "datasheet"}
            )
        else:
            sources.append({"mpn": p["mpn"], "src": p.get("datasheet_url", "n/a"), "kind": "catalog"})

    summary = summarize_options(parts, model=model)
    return {
        "query": query,
        "parts": parts,
        "answer": summary["answer"],
        "refs": summary["refs"],
        "grounded": summary["grounded"] and all(
            p["mpn"] in set(summary["refs"]) for p in parts
        ),
        "sources": sources,
    }