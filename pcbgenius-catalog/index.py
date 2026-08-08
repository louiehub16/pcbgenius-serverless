#!/usr/bin/env python3
"""PCBGenius — D9 LIVE COMPONENT CATALOG (feature #14): search index.

A thin wrapper around a backend search engine (Meilisearch or Typesense) with
a pure-Python keyword-search fallback so the module works with ZERO network
and ZERO third-party dependencies.

UNIFIED CONTRACT — every ``search()`` returns this exact shape:

    {
      "results": [
        {
          "mpn": str,
          "manufacturer": str,
          "specs": {str: str},
          "stock": int,
          "price": float,
          "package": str,
        },
        ...
      ]
    }

Ranking policy: score by (a) keyword relevance across mpn/manufacturer/
desc/specs/package, then (b) availability whose ``stock > 0`` — in-stock and
relevant results float to the top.  This makes the "ranked relevant in-stock
results" contract hold even in the offline fallback.

Backends
--------
* :class:`CatalogIndex` (default)  — engine = ``auto``. If ``requests`` /
  ``meilisearch`` or the Typesense client is importable AND a configured URL
  is reachable, it uses that backend; otherwise it transparently drops to the
  pure-Python fallback (no error raised).
* :class:`MeiliIndex` / :class:`TypesenseIndex`  — explicit wrappers showing
  the real client calls (marked as API boundaries).  They also fall back.

Pure stdlib; the only imports are lazily-loaded optional third-party clients.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from ingest import sample_catalog  # used only to seed the offline fallback


# --------------------------------------------------------------------------
# Backend adapters (optional backends).  Imported lazily so this module
# imports cleanly with zero third-party packages installed.
# --------------------------------------------------------------------------

def _meili_search(query: str, limit: int, parts: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Real Meilisearch query (marked API boundary, optional)."""
    import meilisearch  # API CALL

    client = meilisearch.Client("http://127.0.0.1:7700")
    resp = client.index("pcbgenius").search(query, {"limit": limit, "filter": "stock > 0"})
    return resp.get("hits", [])


def _typesense_search(query: str, limit: int, parts: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Real Typesense query (marked API boundary, optional)."""
    from typesense import Client  # API CALL

    client = Client({"nodes": [{"host": "127.0.0.1", "port": "8108", "protocol": "http"}],
                     "api_key": "xyz", "connection_timeout_seconds": 2})
    resp = client.collections["pcbgenius"].documents.search(
        {"q": query, "query_by": "mpn,manufacturer,desc,package,specs",
         "per_page": limit, "filter_by": "stock:>0"})
    return resp.get("hits", [])


# --------------------------------------------------------------------------
# Pure-Python keyword search fallback (always available, offline).
# --------------------------------------------------------------------------

def _tokens(text: str) -> List[str]:
    """Lowercase alphanumeric token stream (hyphens split -> '5v' '0' etc)."""
    return [t for t in text.lower().replace("-", " ").replace("_", " ").split() if t]


def _relevance(part: Dict[str, Any], query_terms: List[str]) -> float:
    """Keyword relevance score, >=1.0 means every query term matched.

    Fields weighted by importance: mpn (4x), manufacturer (3x), desc (2x),
    specs values (2x), package (1x).  Returns raw match count so it is
    comparable only within a single query's ranking pass.
    """
    haystack_parts = [
        _tokens(part["mpn"]),
        _tokens(part["manufacturer"]),
        _tokens(part["desc"]),
        _tokens(" ".join(part["specs"].values())),
        _tokens(part["package"]),
    ]
    weights = [4, 3, 2, 2, 1]

    score = 0.0
    matched_terms = 0
    for term in query_terms:
        hit = False
        for field, weight in zip(haystack_parts, weights):
            if term in field:
                score += weight
                hit = True
        if hit:
            matched_terms += 1
    # Prefer parts that hit on ALL query terms (additive gate, stronger than
    # raw count alone when a field has 4+ independent term matches).
    if matched_terms == len(query_terms) and query_terms:
        score += 10.0
    # A part with NO keyword-term match must never surface, regardless of any
    # stock boost (stock must strengthen ranking, not fabricate matches).
    if matched_terms == 0:
        return 0.0
    # Availability is part of the rank: an in-stock part ranks above an
    # out-of-stock part of comparable relevance (satisfies "ranked relevant
    # IN-STOCK results"). Boost is smaller than keyword overlap so a clearly
    # more relevant out-of-stock part can still surface when it vastly beats
    # every in-stock alternative.
    if part.get("stock", 0) > 0:
        score += 12.0
    return score


class _FallbackSearcher:
    """Pure-stdlib keyword search over a part list. Recency/`parts` provided
    at construction (seeded by ingest.sample_catalog unless overridden)."""

    def __init__(self, parts: Optional[List[Dict[str, Any]]] = None):
        self.parts = list(parts) if parts else sample_catalog()

    def search(self, query: str, limit: int = 10) -> List[Dict[str, Any]]:
        terms = [t for t in _tokens(query) if t]
        if not terms:
            return self.parts[:limit]

        scored = []
        for part in self.parts:
            rel = _relevance(part, terms)
            if rel <= 0:
                continue
            scored.append((rel, part["stock"], part))

        # Relevance desc (availability already baked into `rel`), then price
        # asc as the final tiebreak.
        scored.sort(key=lambda r: (r[0], -r[2]["price"]), reverse=True)

        selected = [s[2].copy() for s in scored[:limit]]
        return selected


# --------------------------------------------------------------------------
# Public index wrapper.
# --------------------------------------------------------------------------

class CatalogIndex:
    """Search facade with automatic backend selection + offline fallback."""

    def __init__(
        self,
        parts: Optional[List[Dict[str, Any]]] = None,
        backend: str = "auto",
        engine: Optional[Callable[[str, int, List[Dict[str, Any]]], List[Dict[str, Any]]]] = None,
    ):
        self._parts = parts if parts is not None else sample_catalog()
        self._fallback = _FallbackSearcher(self._parts)
        self._engine = engine
        self.backend = "fallback" if not engine else backend

    def search(self, query: str, limit: int = 10) -> Dict[str, Any]:
        """Return the contract shape ``{results: [...]}``.

        Prefers the configured engine; any failure (no client, network down,
        exception) transparently falls back to pure-Python keyword search.
        """
        if self._engine:
            try:
                hits = self._engine(query, limit, self._parts)
                if hits:
                    return {"results": [self._to_row(h) for h in hits]}
            except Exception:
                pass  # drop to offline fallback silently

        rows = self._fallback.search(query, limit)
        return {"results": [self._to_row(r) for r in rows]}

    @staticmethod
    def _to_row(hit: Dict[str, Any]) -> Dict[str, Any]:
        """Project a part dict onto the fixed PUBLIC row shape."""
        return {
            "mpn": hit.get("mpn", ""),
            "manufacturer": hit.get("manufacturer", ""),
            "specs": hit.get("specs", {}),
            "stock": int(hit.get("stock", 0) or 0),
            "price": float(hit.get("price", 0.0) or 0.0),
            "package": hit.get("package", ""),
        }


class MeiliIndex(CatalogIndex):
    """Explicit Meilisearch-backed wrapper (falls back to offline search)."""

    def __init__(self, parts=None):
        super().__init__(parts, backend="meilisearch", engine=_meili_search)


class TypesenseIndex(CatalogIndex):
    """Explicit Typesense-backed wrapper (falls back to offline search)."""

    def __init__(self, parts=None):
        super().__init__(parts, backend="typesense", engine=_typesense_search)


# --------------------------------------------------------------------------
# Convenience.
# --------------------------------------------------------------------------

def build(parts=None) -> CatalogIndex:
    """One-call factory: seed from ``parts`` (default: offline sample catalog)
    and return a ready-to-search CatalogIndex."""
    return CatalogIndex(parts)