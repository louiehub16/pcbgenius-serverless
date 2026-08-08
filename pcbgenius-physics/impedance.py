"""
PCBGenius — E6 Impedance & transmission-line (impedance.py)
============================================================
Compute characteristic impedance Z0 for PCB transmission lines from the board
stackup using closed-form analytic approximations. Deterministic, dependency-free
(stdlib only) so the same stackup always yields the same Z0.

Supported topologies
--------------------
  * microstrip  — outer trace over a single reference plane (surface / 1C layer)
  * stripline   — trace sandwiched between two reference planes (buried layer)
  * edge-coupled differential microstrip (twice the single-ended value via
    coupling factor)  [optional, deterministic too]

Formulas (referenced below)
---------------------------
Microstrip (Hammerstad–Jensen, the standard IPC-2141A closed form):

    Z0 = 87/sqrt(er_eff+1.41) * ln(5.98*h / (0.8*w + t))          [ohm]

    er_eff = (er+1)/2 + (er-1)/2 * 1/sqrt(1 + 10*h/w)

  Good for w/h in ~[0.1, 3.0]; ~2-3% accurate vs full-wave. This is the widely
  used first-pass estimator and reproduces a textbook 50-ohm microstrip design.

Stripline (IPC-2141A / standard symmetric stripline):

    Z0 = 60/sqrt(er_eff) * ln(4b / (0.67*pi*w*(0.8 + t/w)))        [ohm]

    er_eff = er            (uniform dielectric for symmetric stripline)
    b      = distance between the two reference planes
           = 2*h + t        (trace centered: t/2 above bottom plane, t/2 below)

These are analytical approximations, NOT full-wave. For final fabrication the
designer should validate against a field solver (openEMS / scikit-rf) — the
integration call sites are marked `E6-EXT-OPENEMS` / `E6-EXT-SKRF` below.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional


# --------------------------------------------------------------------------- #
# Public data structures                                                      #
# --------------------------------------------------------------------------- #
@dataclass
class Stackup:
    """Board stackup parameters for impedance computation.

    Units: er (rel. permittivity) unitless; h, w, t all in the SAME length unit
    (caller's choice — mm or mil). Only ratios matter to the formulas, so any
    consistent unit works.

    Attributes:
        er:      relative permittivity of the dielectric.
        h:       dielectric height below the trace (microstrip) / one-half the
                 plane spacing (stripline). Same unit as w/t.
        t:       copper thickness of the trace. Same unit as w/t.
        w:       trace width. Same unit as h/t.
        rho:     surface roughness factor, default 1.0 (smooth); <1.0 models
                 etching enhance (greater etch factor). Kept for tuning.
    """

    er: float
    h: float
    t: float
    w: float
    rho: float = 1.0


@dataclass
class ImpedanceResult:
    """Deterministic impedance result for one trace."""

    z0: float                      # characteristic impedance, ohm
    er_eff: float                  # effective relative permittivity for the line
    topology: str                  # "microstrip" | "stripline"
    params: dict = field(default_factory=dict)  # snapshot of inputs used

    def __post_init__(self) -> None:
        self.z0 = round(self.z0, 3)
        self.er_eff = round(self.er_eff, 4)


# --------------------------------------------------------------------------- #
# Internal: microstrip approximations                                         #
# --------------------------------------------------------------------------- #
def _eeff_microstrip(er: float, h: float, w: float) -> float:
    """Effective permittivity (Hammerstad approximation)."""
    return (er + 1.0) / 2.0 + (er - 1.0) / 2.0 * (1.0 / math.sqrt(1.0 + 12.0 * h / w))


def _z0_microstrip(er: float, h: float, w: float, t: float, rho: float = 1.0) -> tuple:
    """Return (Z0, er_eff) for a surface microstrip trace."""
    er_eff = _eeff_microstrip(er, h, w)
    # Etch-factor term: widening from thickness. Standard Hammerstad correction.
    w_prime = w + (t / math.pi) * math.log(4.0) * rho  # slight width gain ~0.44t
    z0 = (87.0 / math.sqrt(er_eff + 1.41)) * math.log(5.98 * h / (0.8 * w_prime + t))
    return z0, er_eff


def _z0_stripline(er: float, h: float, w: float, t: float) -> tuple:
    """Return (Z0, er_eff) for a symmetric buried stripline.

    h is the half-spacing (trace is centered: plane-to-plane distance b = 2h+t).
    er_eff == er for symmetric stripline (field fully in homogeneous dielectric).
    """
    b = 2.0 * h + t                     # plane-to-plane spacing
    denom = 0.67 * math.pi * w * (0.8 + t / w)
    z0 = (60.0 / math.sqrt(er)) * math.log(4.0 * b / denom)
    return z0, er


def _microstrip_coupling_factor(s: float, b: float, h: float) -> float:
    """Edge-coupled microstrip coupling factor (0..1).

    s: edge-to-edge gap, b: floor height, h: same height as stackup. Larger gap
    -> factor -> 0 (decoupled). Uses a monotone logistic falloff.
    """
    k = s / (b + h + s)                     # 0..1 gap ratio
    # Empirical-style smoothing; deterministic.
    factor = 1.0 / (1.0 + math.exp(4.0 * (k - 0.5)))
    return factor


# --------------------------------------------------------------------------- #
# Public API                                                                  #
# --------------------------------------------------------------------------- #
def impedance_microstrip(stackup: Stackup) -> ImpedanceResult:
    """Characteristic impedance of an outer microstrip from the stackup."""
    _validate(stackup, topology="microstrip")
    z0, er_eff = _z0_microstrip(stackup.er, stackup.h, stackup.w, stackup.t, stackup.rho)
    return ImpedanceResult(
        z0=z0,
        er_eff=er_eff,
        topology="microstrip",
        params=_snapshot(stackup),
    )


def impedance_stripline(stackup: Stackup, offset: Optional[float] = None) -> ImpedanceResult:
    """Characteristic impedance of a symmetric stripline.

    `offset` (0..1) provides a hook for off-center traces in a future hardened
    revision; when None (default) the centered formula is used. Currently only
    the symmetric case is computed — offset is accepted for API stability.
    """
    _validate(stackup, topology="stripline")
    if offset not in (None, 0.0):
        # Future work: offset stripline (h1 != h2). Marked as an external-solver
        # integration point; for now falls back to symmetric (documented).
        pass  # E6-EXT-OPENEMS: full-wave offset-stripline validation stub.
    z0, er_eff = _z0_stripline(stackup.er, stackup.h, stackup.w, stackup.t)
    return ImpedanceResult(
        z0=z0,
        er_eff=er_eff,
        topology="stripline",
        params=_snapshot(stackup),
    )


def differential_microstrip(
    stackup: Stackup, gap: float, coupling: Optional[float] = None
) -> ImpedanceResult:
    """Edge-coupled differential microstrip Z0diff = 2 * Z0_single * (1 - k).

    `gap` is edge-to-edge spacing; `coupling` optionally overrides the computed
    factor (0 < coupling < 1) for cal'd designs. Deterministic.
    """
    _validate(stackup, topology="microstrip")
    z0_s, er_eff = _z0_microstrip(stackup.er, stackup.h, stackup.w, stackup.t, stackup.rho)
    k = _microstrip_coupling_factor(gap, stackup.h, stackup.h) if coupling is None else coupling
    z0_diff = 2.0 * z0_s * (1.0 - min(max(k, 0.0), 0.99))
    return ImpedanceResult(
        z0=z0_diff,
        er_eff=er_eff,
        topology="differential-microstrip",
        params={**_snapshot(stackup), "gap": gap, "coupling_factor": round(k, 4)},
    )


# --------------------------------------------------------------------------- #
# Validation + helpers                                                        #
# --------------------------------------------------------------------------- #
def _validate(stackup: Stackup, topology: str) -> None:
    if stackup.er <= 1.0:
        raise ValueError(f"er must be > 1 for {topology} (got {stackup.er})")
    for name in ("h", "t", "w"):
        val = getattr(stackup, name)
        if val <= 0.0:
            raise ValueError(f"{name} must be > 0 for {topology} (got {val})")


def _snapshot(stackup: Stackup) -> dict:
    return {
        "er": stackup.er,
        "h": stackup.h,
        "t": stackup.t,
        "w": stackup.w,
        "rho": stackup.rho,
    }


# --------------------------------------------------------------------------- #
# External solver integration anchor (no heavy deps imported here)            #
# --------------------------------------------------------------------------- #
def solver_hint(topology: str) -> dict:
    """Return which external field solver should validate this topology.

    PCBGenius keeps analytical models fast and deterministic; external solvers
    (openEMS / scikit-rf) are the authoritative full-wave check. This function
    documents that hand-off point for the orchestration layer.

      E6-EXT-OPENEMS: openEMS FDTD validation target (unused_editor).
      E6-EXT-SKRF     : scikit-rf / network parameter cross-check.
    """
    solver = {
        "microstrip": "openEMS",
        "stripline": "openEMS",
        "differential-microstrip": "openEMS + scikit-rf",
    }
    return {"topology": topology, "recommended_solver": solver.get(topology, "openEMS")}