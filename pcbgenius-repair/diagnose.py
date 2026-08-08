#!/usr/bin/env python3
"""PCBGenius — C4 REPAIR-MY-BOARD (feature #27): diagnose.

Given a user's free-text description of a behaviour problem plus a (possibly
faulty) netlist, retrieve the top-k most similar fault signatures from the fault
DB and synthesize a diagnosis + suggested repair.

Output shape (single source of truth):

    {
      "diagnosis": str,          # human-readable diagnosis sentence
      "confidence": float,       # 0.0 .. 1.0 aggregate similarity
      "evidence": [ {           # one entry per matched signature (top-k)
          "fault": str,
          "symptom": str,
          "score": float,
          "rank": int,
          "changed_refs": [str],
          "changed_nets": [str],
      }, ... ],
      "suggested_fix": str,      # concrete repair step
      "refuse": bool | str       # False, or a reason string when we refuse
    }

`diagnose` NEVER calls an API/LLM — it is a deterministic, offline, pure
function (embedding via db.feature_vector, similarity via db.retrieve).
Refusal happens when the inputs are unusable (unparseable/empty netlist,
missing power+ground nets, empty DB) or when the top match is too weak / out of
scope.  Pure stdlib, no network.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from db import (feature_vector, load_jsonl, retrieve, text_similarity)
# fault_injector is imported lazily so diagnose can run with a caller-supplied
# netlist-feature function (keeps this module network-free and dependency-light).

CONFIDENCE_MIN = 0.25   # below this the top match is too weak -> refuse


def _netlist_feats(netlist: Dict[str, Any]) -> Dict[str, float]:
    """Derive a structural feature vector from a netlist (same scheme as the
    fault injector's describe_netlist)."""
    try:
        from fault_injector import describe_netlist
        return describe_netlist(netlist)
    except Exception:
        # Fallback minimal features so diagnose never hard-fails on import.
        return _minimal_feats(netlist)


def _minimal_feats(nl: Dict[str, Any]) -> Dict[str, float]:
    feats: Dict[str, float] = {}
    for c in nl.get("components", []):
        t = c.get("type")
        feats[f"comp:{t}"] = feats.get(f"comp:{t}", 0.0) + 1.0
    for n in nl.get("nets", []):
        feats[f"net_class:{n.get('class', 'signal')}"] = feats.get(
            f"net_class:{n.get('class', 'signal')}", 0.0) + 1.0
    feats["n_comps"] = float(len(nl.get("components", [])))
    feats["n_nets"] = float(len(nl.get("nets", [])))
    return feats


# ─────────────────────────────────────────────────────────────────────────────
# Input validation / refusal
# ─────────────────────────────────────────────────────────────────────────────
def _check_inputs(description: str, netlist: Any) -> Optional[str]:
    """Return a refusal reason string, or None when inputs are usable."""
    if not description or not description.strip():
        return "no problem description provided; cannot diagnose."
    if not isinstance(netlist, dict):
        return "netlist could not be parsed (not an object)."
    comps = netlist.get("components", [])
    nets = netlist.get("nets", [])
    if not isinstance(comps, list) or not comps:
        return "netlist has no components; nothing to diagnose."
    if not isinstance(nets, list) or not nets:
        return "netlist has no nets; cannot reason about connectivity."
    classes = {n.get("class") for n in nets}
    if "ground" not in classes or "power" not in classes:
        return "netlist is missing power and/or ground nets; refusing to guess."
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Diagnosis synthesis
# ─────────────────────────────────────────────────────────────────────────────
def _evidence(matches: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    ev = []
    for m in matches:
        ev.append({
            "fault": m.get("fault", "?"),
            "symptom": m.get("symptom", ""),
            "score": m.get("_score", 0.0),
            "rank": m.get("_rank", 0),
            "changed_refs": m.get("changed_refs", []),
            "changed_nets": m.get("changed_nets", []),
        })
    return ev


def _synthesize(matches: List[Dict[str, Any]], description: str, top: Dict[str, Any]) -> Dict[str, Any]:
    """Blend the top-k signatures into a diagnosis + fix statement."""
    shared_refs: List[str] = []
    seen = set()
    for m in matches:
        for r in m.get("changed_refs", []):
            if r not in seen:
                seen.add(r)
                shared_refs.append(r)
    ref_txt = (", ".join(shared_refs[:4]) + " ") if shared_refs else ""
    diagnosis = top.get("diagnosis", "")
    if len(matches) > 1:
        alts = " / ".join(m.get("fault", "") for m in matches)
        diagnosis = f"{diagnosis} (closest signatures: {alts})."
    suggested_fix = top.get("fix", "")
    # surface user-described symptom back into the fix if the top match agrees
    if shared_refs:
        suggested_fix = suggested_fix + f" Verify around {ref_txt.strip()}."
    return diagnosis, suggested_fix


def diagnose(description: str, netlist: Any,
             db_path: Optional[str] = None,
             records: Optional[List[Dict[str, Any]]] = None,
             k: int = 3) -> Dict[str, Any]:
    """Diagnose a reported fault from a description + netlist.

    Arguments
    ---------
    description : str       user report (e.g. 'output voltage is 1.5V not 3.3V')
    netlist     : dict/str  the (faulty) netlist as dict or JSON string
    db_path     : str       path to a fault DB JSONL (used when records is None)
    records     : list      preloaded fault records (takes precedence over path)
    k           : int       number of top signatures to retrieve

    Returns the output dict described in the module docstring.
    """
    # netlist may arrive as a JSON string
    if isinstance(netlist, str):
        try:
            netlist = json.loads(netlist)
        except Exception:
            netlist = None

    refuse_reason = _check_inputs(description, netlist)
    if refuse_reason is not None:
        return {"diagnosis": "", "confidence": 0.0, "evidence": [],
                "suggested_fix": "", "refuse": refuse_reason}

    db_records = records if records is not None else (load_jsonl(db_path) if db_path else [])
    if not db_records:
        return {"diagnosis": "", "confidence": 0.0, "evidence": [],
                "suggested_fix": "", "refuse": "fault database is empty; cannot diagnose."}

    query_feats = _netlist_feats(netlist)
    matches = retrieve(db_records, query_feats, description, k=k)
    if not matches:
        return {"diagnosis": "", "confidence": 0.0, "evidence": [],
                "suggested_fix": "", "refuse": "no similar fault signatures found."}

    top = matches[0]
    confidence = float(top.get("_score", 0.0))
    if confidence < CONFIDENCE_MIN:
        return {"diagnosis": "", "confidence": confidence, "evidence": _evidence(matches),
                "suggested_fix": "", "refuse": "no confident match (top score below threshold)."}

    diagnosis, suggested_fix = _synthesize(matches, description, top)
    return {
        "diagnosis": diagnosis,
        "confidence": round(confidence, 4),
        "evidence": _evidence(matches),
        "suggested_fix": suggested_fix,
        "refuse": False,
    }


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="PCBGenius repair diagnosis")
    ap.add_argument("--db", default="data/fault_db.jsonl")
    ap.add_argument("--description", default="output voltage reads wrong value on the regulator")
    ap.add_argument("--designs", type=int, default=2)
    ap.add_argument("--seed", type=int, default=1)
    a = ap.parse_args()

    from fault_injector import build_fault_db, load_good_netlist, inject_faults
    recs = build_fault_db(a.designs, a.seed)
    good = load_good_netlist(a.seed)
    # diagnose the FIRST injected fault using its symptom text + the (faulty-era)
    # good netlist as the structural query. In production the user supplies the
    # actual faulty netlist; for the demo the symptom text drives retrieval.
    injected = inject_faults(good, seed=a.seed)[0]
    result = diagnose(injected["symptom"], good, records=recs)
    print(json.dumps(result, indent=2))