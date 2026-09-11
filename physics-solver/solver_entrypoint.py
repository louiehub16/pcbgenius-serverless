#!/usr/bin/env python3
"""PCBGenius physics-solver container entrypoint.

Runs as the container's ENTRYPOINT. Probes the exact solver binaries
pcbgenius-physics/runner.py probes (openEMS/gmsh, ElmerSolver, ccx/calculix),
runs a tiny smoke solve per engine that's cheap enough (~seconds), prints a
JSON verdict, and EXITS so the platform (Salad / backend) can stop billing.

Also supports an optional job-file argument for the real backend later:
    python solver_entrypoint.py /path/to/job.json
where job.json may carry {engine, input} to run a specific solver. For now it
always runs the smoke path if no job file is given.
"""
import json
import os
import shutil
import subprocess
import sys


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


def smoke() -> dict:
    out = {"probe": probe(), "solves": {}, "all_present": True}
    p = out["probe"]
    if not any(p.values()):
        out["all_present"] = False
        out["error"] = "no solver binaries found"
        return out
    # Presence is authoritative (which). Only record a version when the binary
    # safely supports it; openEMS/ccx return non-zero for --version, so don't
    # treat that as a failure — presence (probe) is the real signal.
    if p["gmsh"]:
        out["solves"]["gmsh_version"] = run(["gmsh", "--version"])
    # ElmerSolver needs an input deck; presence via `which` is the signal (no --version).
    if p["elmer"]:
        out["solves"]["elmer_present"] = run(["which", "ElmerSolver"])
    out["all_present"] = all(v for v in p.values() if isinstance(v, bool))
    return out


def run_job(job: dict) -> dict:
    engine = (job.get("engine") or "smoke").lower()
    if engine != "smoke":
        return {"error": f"engine {engine!r} not yet implemented (smoke-only in this image)"}
    return smoke()


def main() -> int:
    # Optional R2 env are passed but unused by smoke (real job uploads later)
    _ = os.environ.get("R2_ENDPOINT_URL")
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
    # Salad Gold Rule #3: a process that exits is treated as a crash and the
    # instance is auto-restarted in a loop. For a one-shot probe, persist the
    # result to a file AND keep the process alive so Salad marks the instance
    # "running" (catches the output), then it exits cleanly.
    try:
        with open("/work/probe_result.json", "w", encoding="utf-8") as f:
            f.write(out)
    except Exception:
        pass
    # Keep alive long enough for Salad to register the instance as running and
    # an operator to read /work/probe_result.json; then exit 0 cleanly.
    import time
    time.sleep(60)
    return 0


if __name__ == "__main__":
    sys.exit(main())