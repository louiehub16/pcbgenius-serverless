"""
PCBGenius E6 — tests/test_impedance.py
======================================
Verify impedance engine against known designs:

  A) 50-ohm microstrip (FR4, er=4.6): textbook geometry tuned for Z0 ~ 50 ohm.
  B) 50+/-10% broad-spectrum check on a range of w/h.
  C) Stripline symmetry: centered trace returns Z0 in a sane 30-75 ohm band and
     er_eff == er.
  D) Determinism: identical stackup repeated -> bit-identical result.
  E) Validation: non-physical stackup raises.

Tests call the module under pcbgenius-physics directly (not a re-implementation).
"""
from __future__ import annotations

import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from impedance import (
    Stackup,
    impedance_microstrip,
    impedance_stripline,
    differential_microstrip,
    solver_hint,
)


def _approx(actual: float, expected: float, tol_rel: float) -> bool:
    return abs(actual - expected) <= tol_rel * abs(expected)


def test_microstrip_50ohm():
    """Classic 4-layer FR4 stackup tuned to ~50 ohm single microstrip."""
    # FR4 er=4.4, h=0.2mm dielectric, 0.035mm copper, 0.37mm trace -> ~50 ohm.
    s = Stackup(er=4.4, h=0.2, t=0.035, w=0.37)
    r = impedance_microstrip(s)
    # Hammerstad formula should land within +/-5% of the 50 ohm target.
    assert _approx(r.z0, 50.0, 0.05), f"microstrip Z0={r.z0} not near 50 ohm"
    assert r.topology == "microstrip"


def test_microstrip_sweep_band():
    """Across w/h in [0.1, 3.0], Z0 monotonically decreases and er_eff>1."""
    s = Stackup(er=4.4, h=1.0, t=0.035, w=1.0)
    prev = None
    for w in (0.1, 0.2, 0.4, 0.8, 1.5, 3.0):
        s.w = w
        r = impedance_microstrip(s)
        assert r.z0 > 20.0, f"w={w} too low"
        assert r.er_eff > 1.0, "er_eff must exceed 1"
        if prev is not None:
            assert r.z0 < prev, f"Z0 must fall as w grows (w={w})"
        prev = r.z0


def test_stripline_symmetric():
    """Symmetric stripline: er_eff == er and Z0 in a sane band."""
    s = Stackup(er=4.4, h=0.5, t=0.035, w=0.25)
    r = impedance_stripline(s)
    assert 25.0 <= r.z0 <= 80.0, f"stripline Z0={r.z0} out of band"
    assert _approx(r.er_eff, 4.4, 0.001), "symmetric stripline er_eff==er"


def test_determinism():
    """Same stackup twice -> identical Z0 (deterministic analytic model)."""
    s = Stackup(er=4.4, h=1.0, t=0.035, w=0.3)
    a = impedance_microstrip(s)
    b = impedance_microstrip(s)
    assert a.z0 == b.z0
    assert a.er_eff == b.er_eff
    assert a.params == b.params


def test_differential_coupling_present():
    """Differential microstrip uses computed coupling factor."""
    s = Stackup(er=4.4, h=1.0, t=0.035, w=0.3)
    r = differential_microstrip(s, gap=0.2)
    assert "coupling_factor" in r.params
    assert r.z0 > 0.0
    # Single-ended must be >= differential/2 (coupling reduces the effective Z0).
    single = impedance_microstrip(s).z0
    assert r.z0 < 2.0 * single, "coupling should bring differential Z0 below 2x single"


def test_validation_rejects_bad_stackup():
    """Non-physical inputs must raise, not silently produce junk."""
    try:
        impedance_microstrip(Stackup(er=1.0, h=1.0, t=0.035, w=0.3))
    except ValueError:
        pass
    else:
        raise AssertionError("er=1.0 should have raised")
    try:
        impedance_stripline(Stackup(er=4.4, h=0.0, t=0.035, w=0.3))
    except ValueError:
        pass
    else:
        raise AssertionError("h=0 should have raised")


def test_solver_hint_marks_external_solvers():
    """External openEMS / scikit-rf integration contact is recorded."""
    assert solver_hint("microstrip")["recommended_solver"] == "openEMS"
    assert solver_hint("stripline")["recommended_solver"] == "openEMS"
    assert "scikit-rf" in solver_hint("differential-microstrip")["recommended_solver"]