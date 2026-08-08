"""
PCBGenius E1 — verify.py: re-verify a certificate against the current design.
=============================================================================

verify() proves that a previously-issued certificate is STILL true for the
*current* netlist + rule-set. It re-computes every digest from scratch and
checks the signature, so both undersigned tampering and legitimate design
evolution are reported explicitly.

A certificate is valid iff ALL of:
  1. The current netlist's fingerprint matches the one the cert was issued for
     (``subject.netlist_digest``) — "design has not changed since proof".
  2. The rule-set version matches what was certified ("verified against THIS
     rule-set").
  3. The evidence is unmodified (the canonical record still hashes to the
     stored ``record_digest``).
  4. The signature attests to ``record_digest`` (tamper-free + authentic, to
     the degree the chosen backend allows).

Each failed rule yields a human-readable reason; ``valid`` is their AND.
"""

from __future__ import annotations

import base64
import hashlib
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from proof import digest, fingerprint_netlist
from sign import (HMAC_SALT, KEY_DIR, METHOD_HMAC, METHOD_NODE_ED25519,
                  _run_node)

VerifyResult = Dict[str, Any]


def _check_result(ok: bool, passes: List[str], reasons: List[str],
                  cond: bool, fail_detail: str, pass_detail: str
                  ) -> Tuple[bool, List[str], List[str]]:
    """Fold one logical check into the running verdict."""
    if cond:
        passes.append(pass_detail)
    else:
        reasons.append(fail_detail)
    return ok and bool(cond), passes, reasons


# ---------------------------------------------------------------------------
# Signature verification (dispatches on the cert's stored method)
# ---------------------------------------------------------------------------
def _verify_signature_node(record: Dict[str, Any]) -> bool:
    """Verify an Ed25519 signature using node_signer.js. [E1 ext:node]"""
    sig = record["signature"]
    if not shutil.which("node"):
        return False
    msg = record["record_digest"].encode("utf-8").hex()
    out = _run_node({"command": "verify",
                     "publicPem": sig["public_key_pem"],
                     "message": msg,
                     "signature": sig["value"]})
    return bool(out.get("ok"))


def _verify_signature_hmac(record: Dict[str, Any], key_dir: Path) -> bool:
    """Recompute the HMAC over record_digest and compare (tamper-evidence)."""
    secret = (key_dir / "hmac.secret")
    if not secret.exists():
        return False
    key_bytes = bytes.fromhex(secret.read_text().strip())
    mac = hashlib.sha256(HMAC_SALT + key_bytes + record["record_digest"].encode("utf-8"))
    expected = base64.b64encode(mac.digest()).decode("ascii")
    return expected == record["signature"]["value"]


def _verify_signature(record: Dict[str, Any], key_dir: Path) -> bool:
    method = record.get("signature", {}).get("method")
    if method == METHOD_NODE_ED25519:
        return _verify_signature_node(record)
    if method == METHOD_HMAC:
        return _verify_signature_hmac(record, key_dir)
    return False


# ---------------------------------------------------------------------------
# Public API ------------------------------------------------------------------
# ---------------------------------------------------------------------------
def verify(cert: Dict[str, Any] | Path | str,
           netlist: Dict[str, Any],
           rule_version: Optional[str] = None,
           key_dir: Optional[Path] = None) -> VerifyResult:
    """Re-verify ``cert`` against the current ``netlist`` (+ optional rule-set).

    Args:
        cert:     the certificate dict, or a path to a JSON cert file.
        netlist:  the current contract-shaped netlist (the thing to re-check).
        rule_version: the rule-set the current design must be verified against.
            If ``None``, the rule check is skipped (only structural + signature
            checks run) so callers who track rules elsewhere aren't forced to
            pass it.
        key_dir:  directory holding the local key material (defaults to
            ``~/.pcbgenius-cert/``). Only used by the HMAC fallback backend;
            Ed25519 verification reads its public key from the cert itself.

    Returns:
        ``{ "valid": bool, "reasons": [ ...why invalid / what passed... ],
           "summary": str }``. ``valid`` is True only when all checks pass.
    """
    key_dir = key_dir or KEY_DIR
    if isinstance(cert, (str, Path)):
        with open(cert, encoding="utf-8") as fh:
            cert = json.load(fh)

    # Structural concerns first.
    if "signature" not in cert or "record_digest" not in cert:
        return {"valid": False,
                "reasons": ["certificate lacks signature/record_digest "
                            "(unsigned or malformed)"],
                "summary": "INVALID — malformed certificate"}

    passes: List[str] = []
    reasons: List[str] = []
    ok = True

    # 1) Current netlist matches the certified design.
    cur_net_digest = fingerprint_netlist(netlist)
    ok, passes, reasons = _check_result(
        ok, passes, reasons,
        cur_net_digest == cert["subject"].get("netlist_digest"),
        "netlist fingerprint: current design does NOT match the certified one "
        f"({cur_net_digest} != {cert['subject'].get('netlist_digest')})",
        "netlist fingerprint matches certified design",
    )

    # 2) Evidence was not tampered with (record still hashes to its digest).
    #    Rebuild the body EXACTLY as proof.build_record hashed it: it excludes
    #    both "signature" and the self-referential "record_digest" <==> the
    #    digest is stable w.r.t. its own value (no circular self-hashing).
    body = {k: v for k, v in cert.items()
            if k not in ("signature", "record_digest")}
    recomputed = digest(body)
    ok, passes, reasons = _check_result(
        ok, passes, reasons,
        recomputed == cert["record_digest"],
        "record digest: certificate content was modified after signing "
        f"({recomputed} != {cert['record_digest']})",
        "record digest recomputes correctly (evidence unmodified)",
    )

    # 3) Rule-set still matches (if the caller pinned one).
    if rule_version is not None:
        same_rules = cert["subject"].get("rule_version") == rule_version
        ok, passes, reasons = _check_result(
            ok, passes, reasons,
            same_rules,
            f"rule version: certified against {cert['subject'].get('rule_version')} "
            f"but current is {rule_version}",
            f"rule version matches ({rule_version})",
        )

    # 4) Signature attests to the digest.
    sig_ok = _verify_signature(cert, key_dir)
    ok, passes, reasons = _check_result(
        ok, passes, reasons,
        sig_ok,
        "signature verification failed (tampered or wrong key)",
        f"signature valid ({cert['signature'].get('method')})",
    )

    reasons.sort()
    summary = "VALID — " + "; ".join(sorted(passes)) if ok else (
        "INVALID — " + "; ".join(reasons))
    return {"valid": bool(ok), "reasons": reasons, "summary": summary}


def verify_file(cert_path: str, netlist_path: str,
                rule_version: Optional[str] = None) -> VerifyResult:
    """Convenience: verify a cert file against a netlist JSON file on disk."""
    with open(netlist_path, encoding="utf-8") as fh:
        netlist = json.load(fh)
    return verify(cert_path, netlist, rule_version=rule_version)


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)
    result = verify_file(sys.argv[1], sys.argv[2],
                         rule_version=sys.argv[3] if len(sys.argv) > 3 else None)
    print(result["summary"])
    sys.exit(0 if result["valid"] else 1)