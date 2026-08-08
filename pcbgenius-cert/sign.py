"""
PCBGenius E1 — sign.py: sign a certificate record with a local key.
==================================================================

sign_record() takes a record produced by :func:`proof.build_record`, computes
the digest it must attest to (``record_digest``), and produces a ``signature``
block. Because the signature binds *only* to ``record_digest`` (which itself
covers netlist fingerprint + rule version + all evidence), any change to the
certified content invalidates the cert at verify time.

Signing backends
----------------
1. node-crypto Ed25519 (preferred). A local keypair is generated on first use
   and cached under ~/.pcbgenius-cert/. Signing + verification then run through
   the bundled ``node_signer.js`` subprocess — the call site is marked
   ``[E1 ext:node]`` (an external signer, per the E1 spec which also allows
   minisign). A node runtime is auto-detected via ``which node``.
2. Stdlib HMAC-SHA256 fallback. When no node runtime is present this keeps the
   whole flow dependency-free. It is *tamper-evident* (detects any edit to the
   content) but NOT a public-key authenticity proof — clearly flagged in the
   stored signature metadata and this docstring.

The chosen backend is recorded in the signature block so verify() can dispatch
to the same algorithm.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, Optional

from proof import digest

# Where the local key material lives (kept out of the repo on purpose).
KEY_DIR = Path.home() / ".pcbgenius-cert"
NODE_SCRIPT = Path(__file__).resolve().parent / "node_signer.js"

HMAC_SALT = b"pcbgenius-cert-e1"

# Backends that never leave the machine. Signer identity is just the method id;
# node-ed25519 additionally publishes its public key inside the cert so a later
# verify() can check the signature with zero shared-secret management.
METHOD_NODE_ED25519 = "node-ed25519"
METHOD_HMAC = "hmac-sha256"


# ---------------------------------------------------------------------------
# node-crypto Ed25519 backend -------------------------------------------------
# ---------------------------------------------------------------------------
def _node_available() -> bool:
    return shutil.which("node") is not None


def _run_node(req: Dict[str, Any] | str) -> Dict[str, Any]:
    """Drive node_signer.js with a request dict over stdio. [E1 ext:node]"""
    # [E1 ext:node] external signer invocation (node crypto backend).
    proc = subprocess.run(
        ["node", str(NODE_SCRIPT)],
        input=json.dumps(req),
        capture_output=True,
        text=True,
        timeout=60,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"node signer failed: {proc.stderr.strip()}")
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"node signer returned invalid JSON: {e}") from e


def _node_keypair(key_dir: Path) -> Dict[str, str]:
    priv_file = key_dir / "ed25519.private.pem"
    pub_file = key_dir / "ed25519.public.pem"
    if priv_file.exists() and pub_file.exists():
        return {"privatePem": priv_file.read_text(), "publicPem": pub_file.read_text()}
    key_dir.mkdir(parents=True, exist_ok=True)
    pair = _run_node({"command": "keygen"})
    priv_file.write_text(pair["privatePem"])
    pub_file.write_text(pair["publicPem"])
    try:
        os.chmod(priv_file, 0o600)
    except OSError:
        pass
    return pair


def _node_sign(keypair: Dict[str, str], record_digest: str) -> str:
    msg = record_digest.encode("utf-8").hex()
    out = _run_node({"command": "sign", "privatePem": keypair["privatePem"],
                     "message": msg})
    return out["signature"]


# ---------------------------------------------------------------------------
# Stdlib HMAC fallback backend -------------------------------------------------
# ---------------------------------------------------------------------------
def _hmac_secret(key_dir: Path) -> bytes:
    """Load-or-create a local HMAC secret (stdlib fallback signing key)."""
    sec_file = key_dir / "hmac.secret"
    if sec_file.exists():
        return bytes.fromhex(sec_file.read_text().strip())
    key_dir.mkdir(parents=True, exist_ok=True)
    secret = os.urandom(32)
    sec_file.write_text(secret.hex())
    return secret


def _hmac_sign(record_digest: str, key_dir: Path) -> str:
    secret = _hmac_secret(key_dir)
    mac = hashlib.sha256(HMAC_SALT + secret + record_digest.encode("utf-8"))
    return base64.b64encode(mac.digest()).decode("ascii")


# ---------------------------------------------------------------------------
# Public API ------------------------------------------------------------------
# ---------------------------------------------------------------------------
def sign_record(record: Dict[str, Any],
                method: Optional[str] = None,
                key_dir: Optional[Path] = None) -> Dict[str, Any]:
    """Return a copy of ``record`` with an attached ``signature`` block.

    Args:
        record: an unsigned record from :func:`proof.build_record` (it carries
            ``record_digest``).
        method: ``"node-ed25519"`` (auto-detected default when node is present),
            ``"hmac-sha256"``, or the recorded cert's stored method when
            re-signing. ``None`` auto-selects node when available.
        key_dir: directory for local key material. Defaults to
            ``~/.pcbgenius-cert/``; tests pass a temp dir to stay hermetic.

    Returns:
        ``record`` with ``record["signature"] = {method, value, ...}``.
    """
    if "record_digest" not in record:
        raise ValueError("record has no record_digest; build via proof.build_record first")

    key_dir = key_dir or KEY_DIR
    chosen = method or (METHOD_NODE_ED25519 if _node_available() else METHOD_HMAC)
    signed = dict(record)

    if chosen == METHOD_NODE_ED25519:
        keypair = _node_keypair(key_dir)
        sig = _node_sign(keypair, record["record_digest"])
        signed["signature"] = {
            "method": METHOD_NODE_ED25519,
            # Public key travels with the cert so verify() needs no pairwise
            # secret exchange; the private key never leaves key_dir.
            "public_key_pem": keypair["publicPem"],
            "value": sig,
            # Not an authenticity claim backed by a CA; this is a project-local
            # hardware-raised certificate to which downstream trusts the key dir.
            "trust_note": "local node-crypto Ed25519; trust the keypair in "
                          f"{key_dir}",
        }
    elif chosen == METHOD_HMAC:
        sig = _hmac_sign(record["record_digest"], key_dir)
        signed["signature"] = {
            "method": METHOD_HMAC,
            "value": sig,
            # FALLBACK backend: tamper-evident only (shared-secret MAC), NOT a
            # public-key proof. Upgrade by installing node.
            "trust_note": "stdlib HMAC fallback (tamper-evident only); install "
                          "node for Ed25519 public-key signing",
        }
    else:
        raise ValueError(f"unknown signing method: {method!r}")
    return signed


if __name__ == "__main__":
    # Quick CLI: read a record file, sign it, write back out.
    import sys
    src, dst = sys.argv[1], sys.argv[2]
    with open(src, encoding="utf-8") as fh:
        rec = json.load(fh)
    signed = sign_record(rec)
    with open(dst, "w", encoding="utf-8") as fh:
        json.dump(signed, fh, indent=2, sort_keys=True)
    print(f"signed {dst} via {signed['signature']['method']}")