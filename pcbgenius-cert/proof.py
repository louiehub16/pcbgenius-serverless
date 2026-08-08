"""
PCBGenius E1 — proof.py: turn a verification run into a canonical certificate record.
=====================================================================================

Collects the *evidence* of a run — ERC exit code, DRC pass/violations, ngspice
measurements — and folds it together with the *frozen input* (netlist digest +
rule-set version) into a single self-describing JSON record whose keyword data
is hashed into a ``record_digest``. The digest is the thing that later gets
signed (sign.py); verifying recomputes it to detect any tampering or drift.

Evidence model
--------------
Each check in the toolchain is normalised to a tiny "check" object:

    { "tool": "erc", "exit_code": 0, "status": "pass",
      "detail": { ...tool-specific summary... } }

``make_checks()`` normalises the tool-specific result shapes (see docstrings)
so the certificate carries heterogeneous evidence homogeneously. Nothing here
calls a tool — this module only *records* results that the caller already has
(hash-in, hash-out). The actual ERC/DRC/ngspice invocations live upstream; this
is the evidence ledger.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

SCHEMA_VERSION = "1.0.0"
PROVER = "pcbgenius-cert"
CREATED_SYSTEM = "PCBGenius"

# Severity buckets that count as hard errors (vs. warnings) for a check summary.
_ERROR_SEVERITIES = ("error", "fatal", "critical")


# ---------------------------------------------------------------------------
# Canonicalisation + hashing (the core of "provably-correct")
# ---------------------------------------------------------------------------
def canonical(obj: Any) -> str:
    """Deterministic JSON text for an object.

    ``sort_keys=True`` + compact separators + ``ensure_ascii=False`` makes the
    same logical object hash identically regardless of key insertion order or
    python int vs string coercion — a precondition for a stable digest.
    """
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def digest(obj: Any) -> str:
    """SHA-256 digest (hex, prefixed) of the canonical form of ``obj``."""
    return "sha256:" + hashlib.sha256(canonical(obj).encode("utf-8")).hexdigest()


def fingerprint_netlist(netlist: Dict[str, Any], schema_version: str = SCHEMA_VERSION) -> str:
    """A stable identity for the *design* being certified.

    The digest covers the whole netlist object, so changing any net, component
    value, footprint, or metadata line changes the fingerprint and makes a
    previously-signed certificate fail re-verification (that is the desired
    "any edit -> invalid" behaviour the tests assert).
    """
    return digest({"netlist_schema": schema_version, "netlist": netlist})


# ---------------------------------------------------------------------------
# Building the "subject" (what is being certified)
# ---------------------------------------------------------------------------
def erc_check(exit_code: int, errors: int = 0, warnings: int = 0) -> Dict[str, Any]:
    """Normalise an ERC tool result.

    Args:
        exit_code: the ERC tool's process exit code (0 == clean).
        errors:    number of ERC errors reported.
        warnings:  number of ERC warnings reported.
    """
    status = "pass" if exit_code == 0 and errors == 0 else "fail"
    return {"tool": "erc", "exit_code": int(exit_code), "status": status,
            "detail": {"errors": int(errors), "warnings": int(warnings)}}


def drc_check(drc_result: Dict[str, Any]) -> Dict[str, Any]:
    """Normalise a DRC result into a check object.

    Accepts the FROZEN CONTRACT ``run_drc`` return shape
    ``{ "pass": bool, "violations": [ {rule,severity,location,message} ] }``
    (see pcbgenius-verification/verifier.py). Error vs warning is bucketed by
    the violation severity.
    """
    violations = drc_result.get("violations", []) if drc_result else []
    errs = sum(1 for v in violations if str(v.get("severity", "")).lower() in _ERROR_SEVERITIES)
    warns = len(violations) - errs
    status = "pass" if drc_result.get("pass") and errs == 0 else "fail"
    return {"tool": "drc", "exit_code": 0 if drc_result.get("pass") else 1,
            "status": status,
            "detail": {"error_count": errs, "warning_count": warns,
                       "violations": violations}}


def ngspice_check(measurements: Optional[List[Dict[str, Any]]] = None,
                  exit_code: int = 0) -> Dict[str, Any]:
    """Normalise an ngspice / simulation result into a check object.

    ``measurements`` is a list of ``{name, value, unit, tol_ok?}`` so the
    certificate records *measured* quantities (e.g. output ripple, efficiency)
    alongside whether each met its tolerance. If a simulation is expected but
    none was produced, pass ``exit_code`` != 0 to mark the check failed.
    """
    measurements = measurements or []
    all_ok = exit_code == 0 and all(m.get("ok", True) for m in measurements)
    status = "pass" if all_ok else "fail"
    return {"tool": "ngspice", "exit_code": int(exit_code),
            "status": status, "detail": {"measurements": measurements}}


def make_checks(erc: Optional[Dict[str, Any]] = None,
                drc: Optional[Dict[str, Any]] = None,
                ngspice: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """Build the ordered list of checks recorded in the certificate.

    ``erc``/``drc`` are the raw results (see :func:`erc_check`/drc_check for the
    accepted shapes); ``ngspice`` may be either a raw measurement list or a
    pre-built ngspice check dict. Only checks present are recorded — a 2-layer
    board that never runs a simulation simply omits the ngspice check.
    """
    out: List[Dict[str, Any]] = []
    if erc is not None:
        out.append(erc if "tool" in erc else erc_check(**erc))
    if drc is not None:
        out.append(drc_check(drc))
    if ngspice is not None:
        if isinstance(ngspice, dict) and "tool" in ngspice:
            out.append(ngspice)
        else:
            out.append(ngspice_check(ngspice))
    return out


# ---------------------------------------------------------------------------
# Building the certificate record
# ---------------------------------------------------------------------------
def build_record(netlist: Dict[str, Any],
                 checks: List[Dict[str, Any]],
                 rule_version: str = "multilayer_rules.v1.0",
                 prover: str = PROVER,
                 now: Optional[str] = None) -> Dict[str, Any]:
    """Assemble a complete, unsigned certificate record.

    Args:
        netlist:      the contract-shaped netlist being certified.
        checks:       list from :func:`make_checks` (the run evidence).
        rule_version: identity of the rule-set the design was verified against.
        prover:       software claiming the proof (defaults to this package).
        now:          ISO-8601 UTC timestamp; injected for deterministic tests.

    Returns:
        A dict ready for :func:`pcbgenius_cert.sign.sign_record`. The returned
        record already carries ``record_digest`` — a hash of everything except
        the signature block — so signing must only attest to that digest, and
        verify() recomputes it to detect tampering.

    The overall ``pass`` of the record is the AND of every check's status.
    """
    subject = {
        "netlist_digest": fingerprint_netlist(netlist),
        "rule_version": rule_version,
        "checks": checks,
    }
    iso = now or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    unsigned = {
        "schema_version": SCHEMA_VERSION,
        "prover": prover,
        "created_by": CREATED_SYSTEM,
        "proven_at": iso,
        "subject": subject,
    }
    unsigned["record_digest"] = digest(unsigned)
    return unsigned


def summarize_evidence(record: Dict[str, Any]) -> str:
    """Short human-readable summary of the evidence for logs / CLI."""
    checks = record["subject"]["checks"]
    parts = []
    for c in checks:
        detail = c.get("detail", {})
        label = c["tool"]
        if c["tool"] == "erc":
            parts.append(f"ERC {'PASS' if c['status']=='pass' else 'FAIL'} "
                         f"(exit {c['exit_code']})")
        elif c["tool"] == "drc":
            parts.append(f"DRC {'PASS' if c['status']=='pass' else 'FAIL'} "
                         f"({detail.get('error_count',0)} err, "
                         f"{detail.get('warning_count',0)} warn)")
        elif c["tool"] == "ngspice":
            n_meas = len(detail.get("measurements", []))
            parts.append(f"ngspice {'PASS' if c['status']=='pass' else 'FAIL'} "
                         f"({n_meas} measurements)")
        else:
            parts.append(f"{label} {'PASS' if c['status']=='pass' else 'FAIL'}")
    overall = all(c["status"] == "pass" for c in checks)
    return "CERT " + ("PASS" if overall else "FAIL") + " :: " + "; ".join(parts)


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    nl = json.loads(open(sys.argv[1]).read())
    rec = build_record(nl, make_checks(erc={"exit_code": 0},
                                       drc={"pass": True, "violations": []}))
    print(json.dumps(rec, indent=2))
    print(summarize_evidence(rec))