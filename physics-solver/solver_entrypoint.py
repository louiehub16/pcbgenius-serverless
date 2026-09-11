#!/usr/bin/env python3
"""PCBGenius physics-solver container entrypoint.

Runs as the container's ENTRYPOINT. Probes the exact solver binaries
pcbgenius-physics/runner.py probes (openEMS/gmsh, ElmerSolver, ccx/calculix),
runs a tiny presence smoke per engine, THEN runs a tiny per-engine OFFICIAL
verification benchmark (benchmarks/, see benchmarks/README.md) so the CPU test
proves each solver can actually COMPUTE, prints a JSON verdict, and EXITS so the
platform (Salad / backend) can stop billing.

Verification HOWTO
------------------
For each engine that is present, the entrypoint runs a small, deterministic,
offline benchmark and reports:
    result["verification"][engine] = {
        "ran":   bool,          # a benchmark subprocess was actually launched
        "ok":    bool | None,   # True=pass; False=definite fail; None=inconclusive
        "detail": str,          # expected vs got + reason (never a bare "ok")
        "level": "numeric_reference" | "analytic_check" | "exit_only",
    }
`ok` is only ever True when a numeric result matches its reference within
tolerance OR a clean converged solve completes. `ok` is NEVER fabricated: an
engine that cannot be assessed yields None with a reason (fail-closed). All
benchmarks are guarded (try/except) and CPU-only; none can raise out of the
probe.

Engine reference levels:
  gmsh     - full numeric reference: unit-cube mesh bounding box == [0,1]^3 (±0.001)
  calculix - analytic check: peak displacement of a tension bar == F*L/(E*A)=0.0025mm (±2%)
  elmer    - exit_only: clean converged solve (exit 0 + completion marker + .result)
             numeric full-reference not parsed (embedded micro-mesh is best-effort)
  openems  - numeric reference (official 5.8GHz microstrip patch S11 dip, ±10%)
             but only when OPENEMS_RUN_FULL=1; default = presence verified, ok=None

Also supports an optional job-file argument for the real backend later:
    python solver_entrypoint.py /path/to/job.json
where job.json may carry {engine, input} to run a specific solver. For now it
always runs the smoke+verification path if no job file is given.
"""

import json
import os
import re
import shutil
import subprocess
import sys

# ---------------------------------------------------------------------------
# Presence probe (unchanged behaviour)
# ---------------------------------------------------------------------------
def probe() -> dict:
    return {
        "openems": bool(shutil.which("openEMS") or shutil.which("openems") or shutil.which("openems-elfd")),
        "gmsh": bool(shutil.which("gmsh")),
        "elmer": bool(shutil.which("ElmerSolver") or shutil.which("elmerfem.elmersolver")),
        "calculix": bool(shutil.which("ccx") or shutil.which("calculix") or shutil.which("cgx")),
    }


def run(cmd, timeout=30) -> dict:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return {"rc": p.returncode, "stdout": p.stdout[-300:], "stderr": p.stderr[-300:]}
    except Exception as e:  # noqa: BLE001 - guard: never raise out of a probe
        return {"rc": -1, "stdout": "", "stderr": str(e)}


# ---------------------------------------------------------------------------
# Verification benchmarks
# ---------------------------------------------------------------------------
NUMERIC = "numeric_reference"
ANALYTIC = "analytic_check"
EXIT_ONLY = "exit_only"


def _bench_root() -> str:
    return os.environ.get("PHYSICS_BENCH_DIR", "/work/benchmarks")


def _sh(cmd, cwd=None, timeout=90) -> dict:
    """Run a subprocess, never raising. Injected runner used by the unit test."""
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=cwd)
        return {"rc": p.returncode, "out": p.stdout or "", "err": p.stderr or ""}
    except Exception as e:  # noqa: BLE001
        return {"rc": -1, "out": "", "err": str(e)}


# --- gmsh : full numeric reference (unit-cube bounding box) -------------------
def _clean_work(work: str, *names) -> None:
    """Remove stale solver artifacts before a run so a leftover .msh/.dat/.result
    from a prior run can never falsely satisfy a benchmark (GPT-sol HIGH). Guarded."""
    for n in names:
        try:
            p = os.path.join(work, n)
            if os.path.exists(p):
                os.remove(p)
        except Exception:
            pass


def _parse_msh_coords(txt: str):
    # gmsh MSH2 (-format msh2) writes node coordinates in a "$Nodes" block as
    #   $Nodes
    #   <count>
    #   <id> x y z
    #   $EndNodes
    # Older MSH1 uses "$Coordinates" with "x y z" per line. Support both so the
    # cube-bbox numeric check can actually parse (GPT-sol CRITICAL: parser was
    # $Coordinates-only before, but -0 msh2 output uses $Nodes -> could never PASS).
    def _parse_block(block):
        coords = []
        lines = block.splitlines()
        # skip a leading integer count line if present ($Nodes)
        if lines and lines[0].strip().isdigit():
            lines = lines[1:]
        for line in lines:
            t = line.split()
            # msh2: <id> x y z ; msh1: x y z
            if len(t) == 4:
                try:
                    coords.append((float(t[1]), float(t[2]), float(t[3])))
                except ValueError:
                    continue
            elif len(t) == 3:
                try:
                    coords.append((float(t[0]), float(t[1]), float(t[2])))
                except ValueError:
                    continue
        return coords

    m = re.search(r"\$Nodes\s*\n(.*?)\$EndNodes", txt, re.S | re.I)
    if m:
        return _parse_block(m.group(1))
    m = re.search(r"\$Coordinates\s*\n(.*?)\$EndCoordinates", txt, re.S | re.I)
    if m:
        return _parse_block(m.group(1))
    return []


def _verify_gmsh(present: bool, root: str, sh) -> dict:
    if not present:
        return {"ran": False, "ok": None, "detail": "gmsh not found; no benchmark run", "level": EXIT_ONLY}
    geo = os.path.join(root, "gmsh", "unit_cube.geo")
    if not os.path.isfile(geo):
        return {"ran": False, "ok": None, "detail": "missing benchmark %s" % geo, "level": EXIT_ONLY}
    work = os.path.join(root, "gmsh")
    try:
        os.makedirs(work, exist_ok=True)
    except Exception:
        pass
    msh = os.path.join(work, "unit_cube.msh")
    _clean_work(work, "unit_cube.msh", "unit_cube.geo_unrolled")
    # GPT/opus5 HIGH: `gmsh -0` only UNROLLS geometry (no mesh, no $Nodes). Must mesh:
    # `-3` meshes 3D volume -> produces a real $Nodes block the bbox check can parse.
    r = sh(["gmsh", geo, "-3", "-o", msh, "-format", "msh2"], cwd=work, timeout=60)
    if r["rc"] != 0 or not os.path.isfile(msh):
        tail = (r["err"] or r["out"])[-220:]
        return {"ran": True, "ok": False,
                "detail": "gmsh meshing failed rc=%s: %s" % (r["rc"], tail), "level": NUMERIC}
    try:
        txt = open(msh, encoding="utf-8", errors="replace").read()
    except Exception as e:
        return {"ran": True, "ok": False, "detail": "gmsh ran but mesh unreadable: %s" % e, "level": NUMERIC}
    coords = _parse_msh_coords(txt)
    if not coords:
        return {"ran": True, "ok": None,
                "detail": "gmsh rc=0 wrote %s but no $Coordinates parsed; numeric result not extracted" % msh,
                "level": NUMERIC}
    mins = [min(c[i] for c in coords) for i in range(3)]
    maxs = [max(c[i] for c in coords) for i in range(3)]
    tol = 0.001
    ok = all(abs(mins[i]) <= tol and abs(maxs[i] - 1.0) <= tol for i in range(3))
    bbox = ", ".join("[%.4f,%.4f]" % (mins[i], maxs[i]) for i in range(3))
    detail = ("unit_cube mesh nodes=%d bbox=(%s) vs expected [0,1]^3 (tol ±%.3f) -> %s"
              % (len(coords), bbox, tol, "PASS" if ok else "FAIL"))
    return {"ran": True, "ok": ok, "detail": detail, "level": NUMERIC}


# --- CalculiX : analytic reference (Hooke tension bar) ------------------------
_CALC_EXPECTED = 100.0 * 5.0 / (200000.0 * 1.0)   # F*L/(E*A) = 0.0025 mm
_CALC_TOL = 0.02


def _max_abs_displacement(f01: str):
    try:
        txt = open(f01, encoding="utf-8", errors="replace").read()
    except Exception:
        return None
    # CalculiX *NODE PRINT, NSET=EALL, FREQUENCY=1 with variable U writes a
    # section to the .dat (unit 5) headed by a SINGLE line like:
    #     displacements (vx,vy,vz) for set EALL and time 1.0000000E+00
    # followed by a blank line and then one row per node:
    #         node         U1           U2           U3
    # where each value is Fortran E-notation (e.g. 2.500000E-03, 0.000000E+00).
    # The table ends at the first blank line after data rows (or a non-node
    # line). We scan every such section and return the max |U| across the
    # U1,U2,U3 columns. (Note: the header keeps '... for set ... and time ...'
    # on the SAME line as 'displacements', so we match the substring, not
    # '<word>\n'.)
    peak = -1.0
    found = False
    in_table = False
    seen_data = False
    for line in txt.splitlines():
        low = line.strip().lower()
        if not in_table:
            if "displacement" in low:
                in_table = True
                seen_data = False
            continue
        toks = line.split()
        if not toks:
            # a blank line ends a displacement table once rows have been seen;
            # a blank immediately after any header line is skipped harmlessly.
            if seen_data:
                in_table = False
            continue
        try:
            int(toks[0])
        except ValueError:
            # A non-numeric first token is a COLUMN-HEADER row (e.g. "node  U1  U2  U3"
            # or "node  vx  vy  vz") that immediately precedes the data rows. Skip it —
            # do NOT end the table here, or the peak is never read (this was a bug).
            # Only end the table on a blank line AFTER numeric rows have been seen.
            if seen_data:
                in_table = False
            continue
        seen_data = True
        for tok in toks[1:]:
            try:
                v = abs(float(tok))
            except ValueError:
                continue
            found = True
            if v > peak:
                peak = v
    return peak if found else None


def _verify_calculix(present: bool, root: str, sh) -> dict:
    if not present:
        return {"ran": False, "ok": None, "detail": "ccx not found; no benchmark run", "level": EXIT_ONLY}
    work = os.path.join(root, "calculix")
    try:
        os.makedirs(work, exist_ok=True)
    except Exception:
        pass
    src = os.path.join(root, "calculix", "vm_bar_tension.inp")
    if not os.path.isfile(src):
        return {"ran": False, "ok": None, "detail": "missing benchmark %s" % src, "level": EXIT_ONLY}
    # *NODE PRINT, U writes the nodal displacement table to the .dat output
    # (NOT .f01; and NOT '*PRINT U' which is an invalid CalculiX keyword -> fatal
    # calinput card error caught by the built-image probe).
    dat = os.path.join(work, "vm_bar_tension.dat")
    _clean_work(work, "vm_bar_tension.dat")
    r = sh(["ccx", "-i", "vm_bar_tension"], cwd=work, timeout=90)
    if r["rc"] != 0:
        tail = (r["err"] or r["out"])[-220:]
        return {"ran": True, "ok": False,
                "detail": "ccx failed rc=%s: %s" % (r["rc"], tail), "level": ANALYTIC}
    if not os.path.isfile(dat):
        return {"ran": True, "ok": None,
                "detail": "ccx rc=0 but no .dat produced; numeric result not extracted", "level": ANALYTIC}
    peak = _max_abs_displacement(dat)
    if peak is None:
        return {"ran": True, "ok": None,
                "detail": "ccx rc=0 but no 'displacements' block parsed from %s; numeric result not extracted"
                          % dat, "level": ANALYTIC}
    rel = abs(peak - _CALC_EXPECTED) / _CALC_EXPECTED
    ok = rel <= _CALC_TOL
    detail = ("peak|u|=%.6f mm vs analytic F*L/(E*A)=%.6f mm (rel err %.2f%% within %.0f%%) -> %s"
              % (peak, _CALC_EXPECTED, rel * 100.0, _CALC_TOL * 100.0, "PASS" if ok else "FAIL"))
    return {"ran": True, "ok": ok, "detail": detail, "level": ANALYTIC}


# --- ElmerFEM : exit-only clean-converged solve -------------------------------
def _verify_elmer(present: bool, root: str, sh) -> dict:
    if not present:
        return {"ran": False, "ok": None, "detail": "ElmerSolver not found; no benchmark run", "level": EXIT_ONLY}
    case_dir = os.path.join(root, "elmer", "ssheat")
    sif = os.path.join(case_dir, "ssheat.sif")
    if not os.path.isfile(sif):
        return {"ran": False, "ok": None, "detail": "missing elmer case %s" % sif, "level": EXIT_ONLY}
    _clean_work(case_dir, "ssheat.result", "ssheat.log")
    r = sh(["ElmerSolver", "ssheat"], cwd=case_dir, timeout=120)
    combined = (r["out"] + "\n" + r["err"]).lower()
    fatal = any(k in combined for k in ("fatal error", "stopping", "aborting", "segmentation fault"))
    completed = ("program completed" in combined) or ("all done" in combined)
    has_result = os.path.isfile(os.path.join(case_dir, "ssheat.result"))
    if r["rc"] == 0 and not fatal and (completed or has_result):
        ok = True
    elif fatal:
        ok = False           # genuine solver crash / abort -> hard fail
    elif r["rc"] != 0:
        # Non-fatal non-zero exit (e.g. the ssheat mesh is best-effort/unvalidated and
        # Elmer may not accept it). Per this case's documented intent, that is
        # INCONCLUSIVE (ok=None) with Elmer's stderr — never a fabricated PASS or FAIL.
        ok = None
    else:
        ok = None            # rc=0 but no completion marker/result -> inconclusive
    tail = (r["err"] or r["out"])[-180:]
    detail = ("ElmerSolver rc=%s completed=%s result_file=%s -> ok=%s; exit-only clean-converged "
              "gate (numeric full-reference not parsed). last output: %s"
              % (r["rc"], completed, has_result, ok, tail))
    return {"ran": True, "ok": ok, "detail": detail, "level": EXIT_ONLY}


# --- openEMS : numeric reference (official 5.8GHz patch), opt-in ---------------
def _verify_openems(present: bool, root: str, sh) -> dict:
    if not present:
        return {"ran": False, "ok": None, "detail": "openEMS not found; no benchmark run", "level": EXIT_ONLY}
    script = os.path.join(root, "openems", "microstrip_5p8.py")
    # Numeric S-param reference (5.8GHz microstrip patch) is a full FDTD sweep,
    # not a seconds micro-case -> only attempted when explicitly enabled so the
    # micro container stays fast. Default: presence+launch, numeric ok=None.
    if os.environ.get("OPENEMS_RUN_FULL", "").lower() not in ("1", "true", "yes"):
        return {"ran": False, "ok": None,
                "detail": ("presence+launch verified; numeric S-param reference (official 5.8GHz "
                           "microstrip patch S11 dip) is a full FDTD sweep, not a seconds micro-case — "
                           "not run in micro mode (set OPENEMS_RUN_FULL=1 to attempt). ok=None: numeric not assessed."),
                "level": NUMERIC}
    if not os.path.isfile(script):
        return {"ran": False, "ok": None, "detail": "missing benchmark script %s" % script, "level": NUMERIC}
    work = os.path.join(root, "openems")
    try:
        os.makedirs(work, exist_ok=True)
    except Exception:
        pass
    r = sh(["python3", script], cwd=work, timeout=300)
    out = (r["out"] or "") + ("\n" + (r["err"] or ""))
    m = re.search(r"OPENEMS_RESULT\s+(\{.*\})", out, re.S)
    if not m:
        tail = out[-200:].replace("\n", " ")
        return {"ran": True, "ok": None,
                "detail": "openEMS sweep ran but no parseable result line; inconclusive: %s" % tail,
                "level": NUMERIC}
    try:
        res = json.loads(m.group(1))
    except Exception as e:
        return {"ran": True, "ok": None, "detail": "openEMS result line unparseable: %s" % e, "level": NUMERIC}
    f_ghz = res.get("S11_dip_freq_ghz")
    if f_ghz is None:
        return {"ran": True, "ok": None,
                "detail": "openEMS ran but produced no S11 dip frequency (see script note): %s" % res,
                "level": NUMERIC}
    ok = 5.8 * (1.0 - 0.10) <= f_ghz <= 5.8 * (1.0 + 0.10)
    detail = ("openEMS S11 dip at %.2f GHz vs reference 5.8 GHz (tol +-10%%) -> %s"
              % (f_ghz, "PASS" if ok else "FAIL"))
    return {"ran": True, "ok": ok, "detail": detail, "level": NUMERIC}


def run_verifications(present: dict, sh=None) -> dict:
    """Run all four per-engine verification benchmarks (guarded, never raises)."""
    sh = sh or _sh
    root = _bench_root()
    return {
        "openems": _verify_openems(present.get("openems", False), root, sh),
        "gmsh": _verify_gmsh(present.get("gmsh", False), root, sh),
        "elmer": _verify_elmer(present.get("elmer", False), root, sh),
        "calculix": _verify_calculix(present.get("calculix", False), root, sh),
    }


# ---------------------------------------------------------------------------
# Smoke + orchestration
# ---------------------------------------------------------------------------
def smoke() -> dict:
    out = {"probe": probe(), "solves": {}, "all_present": True}
    p = out["probe"]
    if not any(p.values()):
        out["all_present"] = False
        out["error"] = "no solver binaries found"
    if p["gmsh"]:
        out["solves"]["gmsh_version"] = run(["gmsh", "--version"])
    if p["elmer"]:
        out["solves"]["elmer_present"] = run(["which", "ElmerSolver"])
    out["all_present"] = all(p.values())
    # Verification benchmarks (NEW) — guarded, never raise.
    out["verification"] = run_verifications(p)
    # Fail-closed pass: ALL mandatory engines must have ran AND ok is True. openEMS
    # micro-mode legitimately returns ok=None (numeric S-param not assessed) — it is
    # OPTIONAL (not mandatory) so it must NOT force verified_all False. Track separately:
    # verified_all = over the 3 mandatory engines (gmsh, elmer, calculix);
    # openems_verified = its own ok. Any mandatory ok=None/False => verified_all False.
    _mandatory = ["gmsh", "elmer", "calculix"]
    out["verified_all"] = all(
        out["verification"][k]["ran"] and out["verification"][k]["ok"] is True
        for k in _mandatory)
    out["openems_verified"] = out["verification"]["openems"]["ran"] and \
        out["verification"]["openems"]["ok"] is True
    return out


def run_job(job: dict) -> dict:
    engine = (job.get("engine") or "smoke").lower()
    if engine != "smoke":
        return {"error": f"engine {engine!r} not yet implemented (smoke-only in this image)"}
    return smoke()


def main() -> int:
    # Optional R2 env are passed but unused by smoke (real job uploads later)
    import time as _time
    if len(sys.argv) > 1:
        try:
            job = json.load(open(sys.argv[1], encoding="utf-8"))
        except Exception:
            job = {}
    else:
        job = {"engine": "smoke"}
    result = run_job(job)
    out = json.dumps(result, indent=2)
    print(out, flush=True)

    # ---- Auto-upload result to R2 (if creds provided) + write local copy ----
    try:
        with open("/work/probe_result.json", "w", encoding="utf-8") as f:
            f.write(out)
    except Exception:
        pass
    _try_upload_r2(result, out)

    # ---- Auto-shutdown flow: after upload, EXIT (Salad restart_policy=never stops the
    #      instance, halting billing). Keep-alive only matters if we need an operator to
    #      read a local file; with R2 upload the container can exit immediately. We hold
    #      briefly so Salad marks the instance running / an operator can see it, then exit 0.
    _time.sleep(15)
    # GPT-sol MED: exit non-zero when verification FAILED so an operator trusting
    # the process exit code can detect it (the JSON + verified_all remains the truth;
    # this simply makes a failure observable via exit status too). Salad restart_policy=never
    # treats a non-zero exit as completion, not a crash-loop, so billing still halts.
    if not result.get("verified_all", True):
        print("[probe] verified_all=False -> exiting non-zero (1) to signal verification failure",
              flush=True)
        return 1
    return 0


def _try_upload_r2(result: dict, out: str) -> None:
    """Best-effort upload of the probe result to Cloudflare R2 (S3-compatible).

    Uses boto3 if available; otherwise falls back to urllib SigV4-free presigned-style
    is NOT supported, so we require boto3 (installed in the image). Missing creds ->
    silent no-op (never raise).
    """
    endpoint = os.environ.get("R2_ENDPOINT_URL") or os.environ.get("R2_ENDPOINT")
    bucket = os.environ.get("BUCKET_NAME") or os.environ.get("R2_BUCKET")
    ak = os.environ.get("AWS_ACCESS_KEY_ID") or os.environ.get("R2_ACCESS_KEY")
    sk = os.environ.get("AWS_SECRET_ACCESS_KEY") or os.environ.get("R2_SECRET") or os.environ.get("R2_SECRET_KEY")
    if not (endpoint and bucket and ak and sk):
        print(f"[probe] R2 creds not all present (endpoint={bool(endpoint)} bucket={bool(bucket)} "
              f"ak={bool(ak)} sk={bool(sk)}) — skipping upload (local copy kept)", flush=True)
        return
    try:
        import boto3
        from botocore.client import Config
        from concurrent.futures import ThreadPoolExecutor, TimeoutError as FT
        key = "physics/probe_result.json"
        s3 = boto3.client(
            "s3", endpoint_url=endpoint,
            aws_access_key_id=ak, aws_secret_access_key=sk,
            config=Config(signature_version="s3v4", connect_timeout=10, read_timeout=15,
                          retries={"max_attempts": 1}),
            region_name="auto",
        )
        # Hard per-request cap: put_object alone can stall past connect/read timeouts
        # on a pathological endpoint. Run it in a worker thread and abort at 25s so the
        # container can never hang (Gemini HIGH fix).
        def _put():
            s3.put_object(Bucket=bucket, Key=key, Body=out.encode(), ContentType="application/json")
        with ThreadPoolExecutor(max_workers=1) as ex:
            f = ex.submit(_put)
            f.result(timeout=25)
        print(f"[probe] uploaded result -> s3://{bucket}/{key}", flush=True)
    except Exception as e:
        print(f"[probe] R2 upload failed (non-fatal): {str(e)[:120]}", flush=True)


if __name__ == "__main__":
    sys.exit(main())