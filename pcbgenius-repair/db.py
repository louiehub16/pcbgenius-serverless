#!/usr/bin/env python3
"""PCBGenius — C4 REPAIR-MY-BOARD (feature #27): fault DB (jsonl).

Fault database store/load as JSONL.  Each line is one fault record produced by
fault_injector.inject_faults / build_fault_db:

    {
      "fault": str,
      "symptom": str,
      "symptom_features": {str: int|float},   # deterministic feature vector
      "diagnosis": str,
      "fix": str,
      "good_design": str,
      "changed_refs": [str],
      "changed_nets": [str],
      "faulty_valid": bool
    }

Pure stdlib, deterministic, no network.  Also exposes a small cosine-similarity
retrieval helper used by diagnose.py so the search logic is testable on its own.
"""

from __future__ import annotations

import json
import math
import os
from typing import Any, Dict, List, Optional

# ─────────────────────────────────────────────────────────────────────────────
# IO
# ─────────────────────────────────────────────────────────────────────────────
def save_jsonl(path: str, records: List[Dict[str, Any]]) -> int:
    """Atomically write records as JSONL. Returns number written."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    os.replace(tmp, path)
    return len(records)


def load_jsonl(path: str) -> List[Dict[str, Any]]:
    """Load JSONL records. Returns [] when the file does not exist."""
    if not os.path.exists(path):
        return []
    out: List[Dict[str, Any]] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            out.append(json.loads(line))
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Deterministic similarity helpers (no pandas / no model / no network)
# ─────────────────────────────────────────────────────────────────────────────
def normalize(vec: Dict[str, float]) -> float:
    return math.sqrt(sum(v * v for v in vec.values())) or 1.0


def cosine(a: Dict[str, float], b: Dict[str, float]) -> float:
    """Cosine similarity over the union of keys. a/b may be sparse dicts.
    Non-numeric values are ignored (defensive against stray string fields)."""
    if not a or not b:
        return 0.0
    keys = set(a) | set(b)
    num = 0.0
    ss_a = ss_b = 0.0
    for k in keys:
        try:
            va = float(a.get(k, 0.0))
            vb = float(b.get(k, 0.0))
        except (TypeError, ValueError):
            continue
        num += va * vb
        ss_a += va * va
        ss_b += vb * vb
    denom = math.sqrt(ss_a) * math.sqrt(ss_b)
    return (num / denom) if denom else 0.0


def feature_vector(text: str, dim: int = 256, seed: int = 0) -> Dict[str, float]:
    """Deterministic bag-of-tokens hashing-vector feature dict for free text.

    Uses a stable non-random hash (FNV-1a, not the per-process-randomised
    builtin hash) so embeddings are identical across runs and machines."""
    import re
    tokens = re.findall(r"[a-z0-9]+", text.lower())
    if not tokens:
        return {}
    out: Dict[str, float] = {}
    for tok in tokens:
        if tok in {"the", "a", "an", "is", "are", "of", "to", "on", "for", "and",
                   "or", "it", "with", "from"}:
            continue
        idx = _fnv1a(tok, seed) % dim
        out[f"tok:{idx}"] = out.get(f"tok:{idx}", 0.0) + 1.0
    return out


def _fnv1a(s: str, seed: int = 0) -> int:
    h = (0x811C9DC5 ^ (seed & 0xFFFF)) & 0xFFFFFFFF
    for ch in s.encode("utf-8"):
        h ^= ch
        h = (h * 0x01000193) & 0xFFFFFFFF
    return h


def text_similarity(a_text: str, b_text: str) -> float:
    return cosine(feature_vector(a_text), feature_vector(b_text))


def retrieve(db: List[Dict[str, Any]], query_feats: Dict[str, float],
             query_text: str = "", k: int = 3,
             struct_w: float = 0.6, text_w: float = 0.4) -> List[Dict[str, Any]]:
    """Rank fault records against a query feature vector (+ optional symptom text).

    score = struct_w * cosine(symptom_features, query_feats)
          + text_w  * text_similarity(query_text, record.symptom)

    Returns top-k records, each augmented with {'_score': float, '_rank': int}.
    Pure, deterministic (stable sort)."""
    scored = []
    for rec in db:
        s_struct = cosine(rec.get("symptom_features", {}), query_feats)
        s_text = text_similarity(query_text, rec.get("symptom", "")) if query_text else 0.0
        total = struct_w * s_struct + text_w * s_text
        scored.append((total, rec))
    scored.sort(key=lambda t: -t[0])
    out = []
    for i, (score, rec) in enumerate(scored[:k]):
        r = dict(rec)
        r["_score"] = round(float(score), 4)
        r["_rank"] = i + 1
        out.append(r)
    return out


def paths_from(db_path: str) -> Dict[str, Any]:
    """Convenience: load a DB file and return stats for diagnostics."""
    recs = load_jsonl(db_path)
    by = {}
    for r in recs:
        by[r.get("fault", "?")] = by.get(r.get("fault", "?"), 0) + 1
    return {"total": len(recs), "by_fault": by}


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="PCBGenius fault DB (jsonl)")
    ap.add_argument("--path", default="data/fault_db.jsonl")
    ap.add_argument("--designs", type=int, default=2)
    ap.add_argument("--seed", type=int, default=1)
    a = ap.parse_args()
    from fault_injector import build_fault_db
    recs = build_fault_db(a.designs, a.seed)
    save_jsonl(a.path, recs)
    print(json.dumps(paths_from(a.path), indent=2))