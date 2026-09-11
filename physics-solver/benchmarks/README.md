# PCBGenius physics-solver — per-engine verification benchmarks

Small, deterministic, **offline** benchmark cases that the container entrypoint
(`solver_entrypoint.py`) runs after the presence probe to prove each engine can
**compute**, not just that it is installed. Copied into the image at
`/work/benchmarks/` and executed from a temp work area (so each run is
write-isolated and leaves no dirty state in the image).

Every benchmark is attempted inside a `try/except` guard — none can crash the
container. `ok` is only ever `True` when a numeric result matches its reference
within tolerance, or a clean converged solve completes. It is **never
fabricated**; an engine that cannot be assessed is reported `ok=None` with a
reason.

## Reference matrix

| engine   | case                                            | reference ground truth                          | tolerance      | level                       |
|----------|-------------------------------------------------|-------------------------------------------------|----------------|-----------------------------|
| gmsh     | `gmsh/unit_cube.geo`                            | analytic geometry bounding box `[0,1]^3`        | ±0.001 / axis  | `numeric_reference` + exit 0|
| calculix | `calculix/vm_bar_tension.inp`                   | analytic peak elongation `F*L/(E*A) = 0.0025 mm`| ±2 %           | `analytic_check` + exit 0   |
| elmer    | `elmer/ssheat/` (steady-state electrostatics)   | analytic `C=ε₀A/d` (documented, NOT parsed)     | n/a (gate)     | `exit_only` (clean solve)   |
| openems  | `openems/microstrip_5p8.py` (patch antenna)     | official ref: S11 resonant dip at 5.8 GHz       | ±10 % (freq)   | `numeric_reference` (opt-in)|

### Level meanings (honest labelling)
- **`numeric_reference`** — the numeric output is compared to a canonical
  reference/analytic value **and** the solve must exit cleanly. `ok=True` only on
  a match inside tolerance.
- **`analytic_check`** — the numeric output is compared to an explicit analytic
  expectation (same constitutive law the vendor's Verification Manual uses);
  `ok=True` only on a match inside tolerance.
- **`exit_only`** — gate is a *clean, converged solve* (exit 0 + normal completion
  marker / result artifact). No numeric comparison is made (either the reference
  isn't embeddable as a micro-case, or the numeric readout cannot be validated
  offline). This is reported honestly in the JSON as `"level": "exit_only"` and is
  **not** claimed to be a numeric reference.

## Per-engine detail

### gmsh — `gmsh/unit_cube.geo` (full numeric reference)
Mesh the analytic unit cube `box(0,0,0,1,1,1)`, parse the vertex coordinates from
the generated `$Coordinates` block, and require the bounding box to equal
`[0,1]^3` within `±0.001` on every axis with ≥1 node. This is a real numeric check
(the mesh must recover the intended geometry) plus a clean `exit 0`. Solves in
well under a second.

### CalculiX — `calculix/vm_bar_tension.inp` (analytic reference)
5 mm × 1 mm × 1 mm elastic bar (`E=200000 MPa`, `ν=0.3`), left end fixed, total
axial force `F=100 N` on the free face. Canonical Hooke ground truth:
`ΔL = F·L/(E·A) = 100·5/(200000·1) = 0.0025 mm`. `ok=True` iff the peak printed
nodal displacement (`.f01` "displacements:" block) matches `0.0025 mm` within
`±2 %` **and** `ccx` exits 0. Runs in ~1 s.

### ElmerFEM — `elmer/ssheat/` (exit-only, best-effort)
Steady-state electrostatics between a unit plate pair. **Analytic expectation**
`C = ε₀·A/d ≈ 8.8541878128e-12 F` is documented but **not** parsed. The gate is
`ElmerSolver` exiting 0 **and** reporting a normal completion marker **and**
producing a `.result` artifact → `exit_only`. The embedded mesh is best-effort /
**unvalidated offline** (the authoring VM has no solvers; this image's ElmerGrid
was built without the gmsh reader). If ElmerSolver cannot load the case or does
not complete cleanly, the runner reports **`ok=None`** (inconclusive) with Elmer's
own stderr — never a fabricated PASS. Expect at most one on-container tuning pass
to settle this gate.

### openEMS — `openems/microstrip_5p8.py` (full reference, opt-in)
Official openEMS **microstrip patch antenna** tutorial geometry (5.8 GHz ISM
design). `ok=True` iff the computed S11 return-loss minimum (resonant dip) occurs
within `±10 %` of `5.8 GHz` AND the FDTD run completes. A broadband S-parameter
sweep is **not** a seconds-scale micro-case, so the runner **does not** run it by
default (keeps the micro container fast); set `OPENEMS_RUN_FULL=1` to attempt it.
Default mode: presence + launch verified, numeric reported `ok=None`.

## Expected / tolerance summary (machine numbers the runner enforces)

| engine   | expected value                        | tolerance | level                  |
|----------|---------------------------------------|-----------|-------------------------|
| gmsh     | bbox `[0,1]³` (each axis)            | ±0.001    | numeric_reference       |
| calculix | peak `|u| = 0.0025 mm`              | ±2 %      | analytic_check          |
| elmer    | `C = 8.8541878128e-12 F` (doc only) | n/a       | exit_only (gate)        |
| openems  | S11 dip at `5.8 GHz`                | ±10 %     | numeric_reference (opt-in)|

## JSON shape (per engine, emitted under `result["verification"][engine]`)
```json
{
  "ran":      true,
  "ok":       true,          // true | false | null (null = inconclusive, never a fabricated pass)
  "detail":   "human-readable outcome incl. expected vs got + which error if any",
  "level":    "numeric_reference"   // or "analytic_check" | "exit_only"
}
```
A top-level `"verified_all"` boolean is `true` only when every engine `ran` and
`ok is True`.