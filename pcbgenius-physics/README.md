# pcbgenius-physics (E7)

RF EM-field simulation and signal-integrity analysis for PCBGenius.

## What's here
- `em.py` — wraps the **openEMS FDTD** solver for planar structures (rectangular
  microstrip patch antenna, microstrip transmission line). `build_openems_config()`
  generates a complete, runnable openEMS/CSXCAD Python script from an analytic
  design; `solve()` is the marked solver call site with a **deterministic analytic
  fallback** (microstrip Hammerstad Z0 + patch transmission-line model) so EM
  analysis returns a real S11 sweep / input impedance even with no solver.
- `sigint.py` — S-parameters (2-port scattering) and **eye diagrams** via
  **scikit-rf**. scikit-rf use is isolated behind marked call sites; when the
  library is absent a deterministic RLGC-consistent analytic stub returns the
  same shape, and the eye diagram is a seeded PRBS → band-limited NRZ analysis.

## Design goals
- **PURE STDLIB core** — no numpy/scikit-rf/openEMS required to run. Every
  optional engine is guarded and degrades to a deterministic fallback.
- **Deterministic** — identical geometry/rate input ⇒ byte-identical output.
- **Marked seams** — grep `CALLSITE`, `OPENEMS_CALL_START/END`, or
  `SCIKIT_RF_CALL_START/END` to find every place a real solver/library call must
  be wired in.

## Field call sites
- `em.solve` — executes the generated openEMS config (real FDTD sweep).
- `sigint.rf_s_params` — real scikit-rf `Network` s11/s21 extraction.

## Usage
```bash
python em.py --freq 2.4 --er 4.4 --h 1.6 --span 1.0 --config   # patch + openEMS config
python em.py --tline --h 0.254                                 # microstrip Z0
python em.py --freq 2.4 --no-model                             # (see CLI) 
python sigint.py                                               # S-params + eye JSON
python test_em.py                                              # run the test suite
```

## Notes
- No npm/docker/git required to run.
- The openEMS config is written, never executed, by this module; an engineer
  runs it through `openEMS-Project/openEMS.sh` for the true FDTD result.