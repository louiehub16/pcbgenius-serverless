"""PCBGenius E7 — physics: EM-field (openEMS/analytic) + signal-integrity (scikit-rf/analytic)."""

from em import solve, patch_antenna_design, microstrip_z0, s11_db, build_openems_config  # noqa: F401
from sigint import rf_s_params, eye_diagram  # noqa: F401

__version__ = "1.0.0"