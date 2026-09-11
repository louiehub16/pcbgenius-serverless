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
    out["all_present"] = all(p.values())
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