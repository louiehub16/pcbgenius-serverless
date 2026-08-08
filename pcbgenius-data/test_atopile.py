#!/usr/bin/env python3
"""
PCBGenius — B1 atopile pipeline tests
=====================================
Tests the atopile data-pipeline integration WITHOUT requiring the real `ato`
binary or docker. Uses the pure-python fallback path (render_fallback_sch +
parse_kicad_sch) so the parser and the contract-validation gate are exercised
end-to-end in this sandbox.

Run:
    python -m pytest test_atopile.py -v
or standalone:
    python test_atopile.py
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

# Ensure both atopile_integration and the shared validate_netlist resolve
# regardless of CWD (datagen lives one level up).
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "datagen"))

import atopile_integration as b1  # subject under test


# --- unit tests --------------------------------------------------------------
def test_prompt_to_ato_regulator():
    ato = b1.prompt_to_ato("build me a 5V to 3.3V linear regulator")
    assert "module" in ato
    assert "signal" in ato
    assert "VoltageRegulator" in ato


def test_prompt_to_ato_led():
    ato = b1.prompt_to_ato("make an LED blinker")
    assert "Microcontroller" in ato
    assert "Led" in ato


def test_prompt_to_ato_slug_identifier():
    assert b1._slugify("Build! 5V  ->  3.3V") == "build_5v_3_3v"
    assert b1._slugify("123")  # numeric prefix handled (prepends design_)


def test_sexp_parser_nested():
    text = '(kicad_sch (version 20231120) (property "A" "B"))'
    tree = b1.parse_sexp(text)
    # parse_sexp returns a list wrapping the root node; tree[0] is the node
    assert tree[0][0] == "kicad_sch"
    assert tree[0] == ["kicad_sch", ["version", "20231120"],
                       ["property", "A", "B"]]


def test_classify_net():
    assert b1.classify_net("GND") == "ground"
    assert b1.classify_net("VCC_3V3") == "power"
    assert b1.classify_net("NET_LED") == "signal"
    assert b1.classify_net("MISO") == "digital"


def test_fallback_pipeline_validates(tmp_path: Path):
    """Full pipeline in fallback mode must produce a contract-valid netlist."""
    rec = b1.run_pipeline(
        "build me a 5V to 3.3V linear regulator",
        layers=2, use_fallback=True, out_dir=tmp_path / "runs")
    assert rec["contract_validated"] is True
    assert rec["validation_errors"] == []
    nl = rec["netlist"]
    assert nl["schema_version"] == "1.0.0"
    assert nl["metadata"]["created_by"] == "pcbgenius"
    # Contract rules: at least one ground + one power net
    classes = {n["class"] for n in nl["nets"]}
    assert "ground" in classes and "power" in classes
    # Contract rules: unique refs, resolvable ref.pin
    refs = [c["ref"] for c in nl["components"]]
    assert len(refs) == len(set(refs))
    # And the shared datagen validator agrees
    from generate_netlists import validate_netlist
    ok, errs = validate_netlist(nl)
    assert ok, f"shared validator rejected: {errs}"
    # source files were actually materialised
    assert Path(rec["source"]["kicad_sch"]).exists()


def test_ato_build_missing_binary_raises_diagnostic():
    """Without the ato binary the build step must raise, not silently fake."""
    import subprocess
    try:
        b1.ato_build(Path(tempfile.mkdtemp()), "missing")
        raised = False
    except (subprocess.SubprocessError, RuntimeError, FileNotFoundError,
            subprocess.TimeoutExpired):
        raised = True
    assert raised


def test_4layer_stackup_template_exists():
    assert (Path(__file__).parent / "stackup_templates" / "4_layer.ato").exists()
    assert (Path(__file__).parent / "stackup_templates" / "6_layer.ato").exists()


# --- standalone runner -------------------------------------------------------
def _standalone():
    passed = 0
    checks = [
        ("prompt->ato regulator", test_prompt_to_ato_regulator),
        ("prompt->ato led", test_prompt_to_ato_led),
        ("slugify", test_prompt_to_ato_slug_identifier),
        ("sexp parser", test_sexp_parser_nested),
        ("net classify", test_classify_net),
    ]
    import tempfile as _t
    for name, fn in checks:
        fn()
        print(f"  ok  {name}")
        passed += 1
    # pipeline test using a real temp dir
    with _t.TemporaryDirectory() as d:
        from pathlib import Path as _P
        test_fallback_pipeline_validates(_P(d))
        print("  ok  fallback pipeline validates against contract")
        passed += 1
    print(f"\n{passed} checks passed.")


if __name__ == "__main__":
    _standalone()