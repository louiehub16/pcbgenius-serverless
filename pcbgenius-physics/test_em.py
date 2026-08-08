#!/usr/bin/env python3
"""
PCBGenius E7 — test_em.py
=========================
Deterministic tests for the physics package (em.py + sigint.py).
Run with plain stdlib (no openEMS / no scikit-rf / no network / no docker):

    python test_em.py            # run all checks, exit 0 on pass
    python -m pytest test_em.py

Coverage
--------
  1. em.microstrip_z0 returns sensible Z0/eps_eff and rejects bad geometry.
  2. em.patch_antenna_design produces physical W/L for a 2.4 GHz FR4 patch and
     a resonance dip in s11_db() exactly at f0.
  3. em.solve returns a real S11 sweep + input impedance; deterministic across
     two identical calls; analytic backend never invokes a solver.
  4. em.build_openems_config emits a runnable openEMS script containing the
     design geometry and the CALLSITE marker.
  5. sigint.rf_s_params returns a deterministic 2-port with the analytic stub
     when scikit-rf is absent and still marks its call site.
  6. sigint.eye_diagram is deterministic and yields physically sane metrics
     (finite height, positive Q, width <= 1 UI).
  7. Markers present in source (grep the seams).
"""

from __future__ import annotations

import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import em  # noqa: E402
import sigint  # noqa: E402


# ─────────────────────────────────────────────────────────────────────────────
def test_microstrip_z0_sane():
    z0, ee = em.microstrip_z0(0.30, 0.254, 4.4)
    assert 30.0 < z0 < 90.0, f"unexpected Z0 {z0}"
    assert 1.0 < ee < 4.4, f"unexpected eps_eff {ee}"
    # wider trace -> lower impedance
    z0b, _ = em.microstrip_z0(1.5, 0.254, 4.4)
    assert z0b < z0
    # bad geometry rejected
    for bad in (em.microstrip_z0,):
        try:
            bad(0.0, 0.254, 4.4)
            raise AssertionError("expected ValueError for zero width")
        except ValueError:
            pass


def test_patch_design_physical():
    d = em.patch_antenna_design(2.4, er=4.4, substrate_h_mm=1.6)
    assert d["W_mm"] > 0 and d["L_mm"] > 0
    assert d["eps_eff"] < 4.4
    # resonant patch length ~ lambda0/(2 sqrt(eps_eff)) minus fringing
    lam = 2.99792458e11 / (2.4e9)
    assert d["L_mm"] < lam / 2.0
    assert d["rin_ohm"] > 0 and d["q"] > 0


def test_s11_dip_at_resonance():
    d = em.patch_antenna_design(2.4)
    at = em.s11_db(d, 2.4)
    off = em.s11_db(d, 2.4 + 0.25)
    assert at < off, "S11 should be lower at resonance than detuned"


def test_solve_returns_sweep_and_is_deterministic():
    d = em.patch_antenna_design(2.4)
    r1 = em.solve(d, backend="analytic", span_ghz=1.0, points=101)
    r2 = em.solve(d, backend="analytic", span_ghz=1.0, points=101)
    assert r1["s11_sweep"] == r2["s11_sweep"]
    assert len(r1["s11_sweep"]) == 101
    assert r1["method"] == "analytic"
    assert r1["zin_ohm"] == round(d["rin_ohm"], 3)
    # analytic path must never try a solver
    assert r1["solver_output"] is None


def test_openems_config_emits_geometry_and_marker():
    d = em.patch_antenna_design(2.4)
    cfg = em.build_openems_config(d)
    assert "openEMS" in cfg
    assert f"W   = {d['W_mm']:.6f}" in cfg
    assert f"L   = {d['L_mm']:.6f}" in cfg
    assert em.CALLSITE in cfg
    assert em.OPENEMS_CALL_START and em.OPENEMS_CALL_END


def test_sigint_analytic_stub_deterministic():
    if sigint._try_import_skrf() is not None:
        sp = sigint.rf_s_params(use_lib=False)
    else:
        sp = sigint.rf_s_params()
    assert sp.engine == "analytic"
    assert len(sp.s11_db) == len(sp.frequency_ghz) == 51
    assert sp.call_site == sigint.CALLSITE
    sp2 = sigint.rf_s_params(use_lib=False)
    assert sp.s11_db == sp2.s11_db


def test_eye_diagram_sane_and_deterministic():
    e1 = sigint.eye_diagram(bitrate_gbps=10.0, samples_per_bit=32)
    e2 = sigint.eye_diagram(bitrate_gbps=10.0, samples_per_bit=32)
    assert e1.eye_width_ui <= 1.0
    assert e1.eye_height_mv >= 0.0
    assert e1.q_factor > 0.0
    assert 0.0 <= e1.ber_est <= 0.5
    assert e1.as_dict() == e2.as_dict()
    # ideal pulse (no filter) opens the eye wider than a band-limited RC pulse
    e_ideal = sigint.eye_diagram(bitrate_gbps=10.0, samples_per_bit=32, pulse="ideal")
    assert e_ideal.eye_width_ui >= e1.eye_width_ui


def test_markers_present_in_source():
    here = os.path.dirname(os.path.abspath(__file__))
    em_src = open(os.path.join(here, "em.py"), encoding="utf-8").read()
    si_src = open(os.path.join(here, "sigint.py"), encoding="utf-8").read()
    assert "OPENEMS_CALL_START" in em_src and "OPENEMS_CALL_END" in em_src
    assert "SCIKIT_RF_CALL_START" in si_src and "SCIKIT_RF_CALL_END" in si_src
    assert em.list_call_sites() and sigint.list_call_sites()


if __name__ == "__main__":
    failures = 0
    total = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            total += 1
            try:
                fn()
                print(f"  PASS  {name}")
            except AssertionError as e:
                failures += 1
                print(f"  FAIL  {name}: {e}")
    print(f"\n{total} tests, {failures} failures.")
    sys.exit(1 if failures else 0)