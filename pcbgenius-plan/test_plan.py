"""
PCBGenius — D7 research copilot tests (feature #11)
===================================================
Three grounded-answer queries exercised with the PURE-PYTHON keyword
retrieval fallback (no hosted model, no network, no third-party deps).

Run:  python test_plan.py            (verbose assertions + printed plans)
Or:   python -m pytest test_plan.py  (collects test_* functions)

Each test asserts the FROZEN CONTRACT of `plan()`: a grounded answer with
part refs, i.e. every referenced MPN exists in the candidate set and in the
sources, and `grounded == True`.
"""

from __future__ import annotations

import sys
from typing import Any, Dict

from research import plan
from retrieval import RetrievalIndex, keyword_retrieval, build_default_index

INDEX = build_default_index()


def _assert_grounded_plan(query: str) -> Dict[str, Any]:
    """Helper: run plan() with the keyword fallback and assert grounding."""
    result = plan(query, retrieval=INDEX)
    assert result["grounded"] is True, f"expected grounded plan, got {result}"
    assert result["answer"], "answer should be non-empty"
    assert isinstance(result["refs"], list) and result["refs"], "refs required"
    # Every referenced MPN must exist in the returned candidate parts.
    mpns = {p["mpn"] for p in result["parts"]}
    for ref in result["refs"]:
        assert ref in mpns, f"ref {ref!r} not in candidate parts {mpns}"
    # Every part must have a source line (catalog or datasheet).
    assert len(result["sources"]) == len(result["parts"])
    for s in result["sources"]:
        assert s["mpn"] in mpns and s["src"], f"bad source line {s}"
    # The keyword retrieval fallback must return hits for our seeded index.
    for p in result["parts"]:
        hits = keyword_retrieval(INDEX, f"{p['mpn']} {p['desc']}", top_k=1)
        assert hits, f"keyword fallback returned no hit for {p['mpn']}"
    return result


def _part_known(result: Dict[str, Any], needle: str) -> str:
    """Return the exact ref/MPN containing `needle`, or '' if absent."""
    for ref in result["refs"]:
        if needle in ref:
            return ref
    for p in result["parts"]:
        if needle in p["mpn"]:
            return p["mpn"]
    return ""


def test_query_audio_opamp():
    """Query 1: 'dual low-noise audio op amp' -> grounded OPA2134 ref."""
    result = _assert_grounded_plan("dual low-noise audio op amp")
    refs = set(result["refs"])
    # OPA2134 is the seeded recommendation (audio-tuned op amp).
    assert any("OPA2134" in p["mpn"] for p in result["parts"]), "OPA2134 missing"
    assert any("OPA2134" in ref for ref in refs), "answer must reference OPA2134"
    print("[1] pass  audio-opamp ->", _part_known(result, "OPA2134"))
    return result


def test_query_rail_to_rail():
    """Query 2: 'rail-to-rail low power op amp' -> grounded MCP6022 ref."""
    result = _assert_grounded_plan("rail-to-rail low power op amp")
    assert any("MCP6022" in p["mpn"] for p in result["parts"]), "MCP6022 missing"
    assert any("MCP6022" in ref for ref in result["refs"]), "answer must reference MCP6022"
    print("[2] pass  rail-to-rail ->", _part_known(result, "MCP6022"))
    return result


def test_query_precision_sensor():
    """Query 3: 'precision low offset instrumentation' -> grounded OP07 ref."""
    result = _assert_grounded_plan(
        "precision low offset instrumentation amplifier"
    )
    assert any("OP07" in p["mpn"] for p in result["parts"]), "OP07 missing"
    assert any("OP07" in ref for ref in result["refs"]), "answer must reference OP07"
    print("[3] pass  precision-sensor ->", _part_known(result, "OP07"))
    return result


def _self_test() -> int:
    """Run all three queries, print a compact report, return 0 on success."""
    results: Dict[str, Dict[str, Any]] = {
        "audio_signal": test_query_audio_opamp(),
        "rail_to_rail": test_query_rail_to_rail(),
        "precision_frontend": test_query_precision_sensor(),
    }
    print("\n=== D7 plan grounding summary ===")
    for name, r in results.items():
        print(f"  {name}: grounded={r['grounded']} refs={r['refs']}")
    print("\nAll 3 queries passed with keyword-retrieval grounding.")
    return 0


if __name__ == "__main__":
    pytest = ("pytest" in sys.argv)  # keep collectable; not used otherwise
    _ = pytest
    sys.exit(_self_test())