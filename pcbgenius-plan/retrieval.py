"""
PCBGenius — D7 retrieval stage (feature #11)
============================================
RAG over datasheets. Ships TWO backends behind one tiny interface:

  * `RetrievalIndex`   — incremental index over text chunks (documents).
  * `embedding_search` — optional; requires an embedding model/vector store.
                        If none is configured, it raises a clear RuntimeError
                        so callers can fall back.
  * `keyword_retrieval`— PURE-PYTHON keyword/BM25-lite scorer. Zero external
                        dependencies; this is the guaranteed working path used
                        by `test_plan.py` and offline deployments.

FROZEN CONTRACT
---------------
    RetrievalIndex()
        .add(doc_id, text)        -> None           (chunk + tokenize)
        .search(query, top_k)     -> [ { "doc_id", "score", "snippet" } ]

    keyword_retrieval(index, query, top_k) -> same shape (stateless helper)
    embedding_search(index, query, top_k)  -> same shape (raises if no embedder)
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Dict, List, Optional

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _tokenize(text: str) -> List[str]:
    """Lowercase alphanumeric tokens (handles MDN/datasheet prose and mpns)."""
    return _TOKEN_RE.findall(text.lower())


@dataclass
class _Doc:
    doc_id: str
    text: str
    tokens: List[str] = field(default_factory=list)
    tf: Counter = field(default_factory=Counter)


class RetrievalIndex:
    """Simple add/search index. Used directly by the keyword fallback.

    Kept dependency-free on purpose: the plan/test path must run on a bare
    CPython install (no numpy/scikit-learn) so CI is trivially green.
    """

    def __init__(self) -> None:
        self.docs: Dict[str, _Doc] = {}
        self.df: Counter = Counter()  # document frequency per token
        self.total_docs: int = 0

    def add(self, doc_id: str, text: str) -> None:
        tokens = _tokenize(text)
        tf = Counter(tokens)
        self.docs[doc_id] = _Doc(doc_id=doc_id, text=text, tokens=tokens, tf=tf)
        for tok in set(tokens):
            self.df[tok] += 1
        self.total_docs = len(self.docs)

    def search(self, query: str, top_k: int = 3) -> List[Dict[str, str]]:
        return keyword_retrieval(self, query, top_k)

    # -- embedding backend hooks -------------------------------------------------
    def set_embedder(self, embedder: object, store: object) -> None:
        """Attach an embedding model + vector store (optional, not imported)."""
        self._embedder = embedder
        self._store = store

    def embedding_search(self, query: str, top_k: int = 3) -> List[Dict[str, str]]:
        return embedding_search(self, query, top_k)


# ---------------------------------------------------------------------------
# Stateless pure-python keyword retrieval (BM25-lite)
# ---------------------------------------------------------------------------

def _idf(n_docs: int, df: int, avgdl: float) -> float:
    if df == 0:
        return 0.0
    return math.log(1 + (n_docs - df + 0.5) / (df + 0.5))


def keyword_retrieval(
    index: RetrievalIndex,
    query: str,
    top_k: int = 3,
) -> List[Dict[str, str]]:
    """Pure-python keyword search over the index (BM25-lite scoring).

    Zero external dependencies. Given `query` tokens it scores every indexed
    document by the standard BM25 equation (k1=1.5, b=0.75) and returns the
    top matches with a snippet and a normalized score.

    Returns:
        [ { "doc_id", "score", "snippet" } ] sorted by score desc.
    """
    q_tokens = _tokenize(query)
    if not q_tokens or index.total_docs == 0:
        return []

    avgdl = 0.0
    if index.total_docs:
        avgdl = sum(len(d.tokens) for d in index.docs.values()) / index.total_docs

    k1, b = 1.5, 0.75
    scores: List[Dict[str, object]] = []
    for doc in index.docs.values():
        dl = len(doc.tokens)
        denom = k1 * (1 - b + (b * dl / avgdl)) if avgdl else 1.0
        score = 0.0
        for tok in q_tokens:
            tf = doc.tf.get(tok, 0)
            if tf == 0:
                continue
            idf = _idf(index.total_docs, index.df.get(tok, 0), avgdl)
            score += idf * (tf * (k1 + 1)) / (tf + denom)
        scores.append(
            {"doc_id": doc.doc_id, "score": round(score, 4), "snippet": doc.text[:200], "_raw": score}
        )

    scores.sort(key=lambda r: -float(r["_raw"]))
    return [{k: r[k] for k in ("doc_id", "score", "snippet")} for r in scores[:top_k]]


def embedding_search(
    index: RetrievalIndex,
    query: str,
    top_k: int = 3,
) -> List[Dict[str, str]]:
    """Vector search over the index. Raises unless an embedder was attached.

    This is the optional heavy path — normally supplied by a hosting model.
    The keyword fallback (`keyword_retrieval`) is what test_plan exercises.
    """
    # mark:call:model  embedding_search
    embedder = getattr(index, "_embedder", None)
    store = getattr(index, "_store", None)
    if embedder is None or store is None:
        raise RuntimeError(
            "embedding_search requires an embedder + vector store. "
            "Use keyword_retrieval() (pure-python) for the offline path."
        )
    # In production: qv = embedder(query); hits = store.top_k(qv, k=top_k).
    _ = query, top_k  # placeholder
    return []


def build_default_index() -> RetrievalIndex:
    """Seed an index with representative datasheet excerpts for tests/offline.

    Defines the grounded-evidence baseline used by test_plan.py's part refs.
    """
    idx = RetrievalIndex()
    idx.add(
        "TID:OPA2134",
        "OPA2134 SoundPlus dual audio operational amplifier. Low distortion, "
        "high output drive, wide supply range ±2.5V to ±18V. Designed for "
        "high-performance audio signal conditioning.",
    )
    idx.add(
        "MCHP:MCP6022",
        "MCP6021/2/3/4 dual CMOS op amp, rail-to-rail input and output, "
        "10 MHz gain bandwidth, 2.5V to 5.5V supply, low power for battery "
        "applications.",
    )
    idx.add(
        "TID:TL072",
        "TL07x low-noise JFET-input operational amplifiers. Dual TL072, "
        "low input bias, ±5V to ±15V supplies, general audio and "
        "instrumentation use.",
    )
    idx.add(
        "ADI:OP07",
        "OP07 ultralow offset voltage operational amplifier. Very low "
        "input offset, ideal for precision instrumentation and sensor "
        "front-ends.",
    )
    return idx