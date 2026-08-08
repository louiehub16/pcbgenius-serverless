#!/usr/bin/env python3
"""
PCBGenius E1 — tests for the provably-correct design certificate.
===============================================================
Run with plain stdlib (no pip deps / no network / no docker):
    python test_cert.py            # all checks; exit 0 pass / 1 fail

This is the E1 acceptance test: it proves a *good* design is certified VALID
and then shows that **any edit to the design invalidates the certificate** —
the whole point of "provably correct".

Assertions
----------
  1. proof.build_record assembles a record; make_checks folds ERC/DRC/ngspice
     evidence homogeneously; record_digest is present.
  2. A good design + clean evidence -> signed cert verifies VALID (both the
     node-crypto Ed25519 backend and the stdlib HMAC fallback backend).
  3. Any edit to the netlist (value change) -> verify() returns INVALID with a
     netlist-fingerprint reason.
  4. Tampering with the certificate's stored evidence -> INVALID (record
     digest + signature both break).
  5. Verifying against a different rule-set version -> INVALID.
"""

from __future__ import annotations

import copy
import json
import os
import sys
import tempfile
from pathlib import Path

# Make the package importable when run as a bare script in this dir.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from proof import build_record, make_checks, summarize_evidence  # noqa: E402
from sign import METHOD_HMAC, METHOD_NODE_ED25519, sign_record  # noqa: E402
from verify import verify  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
def good_netlist():
    """A small contract-shaped netlist for a 3.3 V LDO — valid, no layout."""
    def _pin(number, name, net):
        return {"number": number, "name": name, "net": net}

    props = {}
    return {
        "schema_version": "1.0.0",
        "metadata": {
            "design_name": "ldo_3v3",
            "description": "3.3V regulator demo",
            "board_layers": 2,
            "created_by": "pcbgenius",
            "target_fab": None,
        },
        "components": [
            {"ref": "U1", "type": "ic", "value": "AMS1117-3.3", "package": "SOT-223",
             "mpn": None, "pins": [
                 _pin("1", "VIN", "VIN"), _pin("2", "GND", "GND"),
                 _pin("3", "VOUT", "VOUT")], "properties": props},
            {"ref": "C1", "type": "capacitor", "value": "10uF", "package": "0805",
             "mpn": None, "pins": [
                 _pin("1", "1", "VIN"), _pin("2", "2", "GND")], "properties": props},
            {"ref": "C2", "type": "capacitor", "value": "4.7uF", "package": "0805",
             "mpn": None, "pins": [
                 _pin("1", "1", "VOUT"), _pin("2", "2", "GND")], "properties": props},
        ],
        "nets": [
            {"name": "VIN", "pins": ["U1.1", "C1.1"], "class": "power"},
            {"name": "GND", "pins": ["U1.2", "C1.2", "C2.2"], "class": "ground"},
            {"name": "VOUT", "pins": ["U1.3", "C2.1"], "class": "power"},
        ],
    }


def clean_evidence():
    """ERC clean, DRC pass, ngspice measurements within tolerance."""
    measurements = [
        {"name": "vout", "value": 3.31, "unit": "V", "target": 3.3, "ok": True},
        {"name": "ripple", "value": 8.0, "unit": "mV", "max": 10.0, "ok": True},
    ]
    return make_checks(
        erc={"exit_code": 0, "errors": 0, "warnings": 0},
        drc={"pass": True, "violations": [
            {"rule": "STACKUP", "severity": "warning", "location": "L2",
             "message": "non-grounded pour"}]},
        ngspice=measurements,
    )


RULE_VERSION = "multilayer_rules.v1.0"


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------
def _run(name, fn):
    try:
        fn()
        print(f"  ok    {name}")
        return True
    except AssertionError as e:
        print(f"  FAIL  {name}: {e}")
        return False
    except Exception as e:  # noqa: BLE001
        print(f"  ERROR {name}: {type(e).__name__}: {e}")
        return False


def test_record_shape():
    rec = build_record(good_netlist(), clean_evidence(), RULE_VERSION, now="2026-01-01T00:00:00Z")
    assert rec["schema_version"] == "1.0.0"
    assert rec["subject"]["rule_version"] == RULE_VERSION
    assert rec["subject"]["netlist_digest"].startswith("sha256:")
    assert rec["record_digest"].startswith("sha256:")
    assert {c["tool"] for c in rec["subject"]["checks"]} == {"erc", "drc", "ngspice"}
    assert summarize_evidence(rec).startswith("CERT PASS")


def _sign_and_verify(method, netlist, rule_version=RULE_VERSION, key_dir=None):
    """Helper: build+sign+verify a cert for `method`, return (result, cert)."""
    rec = build_record(netlist, clean_evidence(), rule_version)
    cert = sign_record(rec, method=method, key_dir=key_dir)
    res = verify(cert, netlist, rule_version=rule_version, key_dir=key_dir)
    return res, cert


def test_good_design_valid_ed25519(tmp):
    res, cert = _sign_and_verify(METHOD_NODE_ED25519, good_netlist(), key_dir=tmp)
    assert cert["signature"]["method"] == METHOD_NODE_ED25519
    assert res["valid"] is True, res["reasons"]


def test_good_design_valid_hmac(tmp):
    res, cert = _sign_and_verify(METHOD_HMAC, good_netlist(), key_dir=tmp)
    assert cert["signature"]["method"] == METHOD_HMAC
    assert res["valid"] is True, res["reasons"]


def test_edit_invalidates_ed25519(tmp):
    """The E1 core property: any edit to the design -> certificate INVALID."""
    nl = good_netlist()
    _, cert = _sign_and_verify(METHOD_NODE_ED25519, nl, key_dir=tmp)

    edited = copy.deepcopy(nl)              # bump one component value
    edited["components"][1]["value"] = "22uF"
    res = verify(cert, edited, rule_version=RULE_VERSION, key_dir=tmp)
    assert res["valid"] is False
    assert any("netlist fingerprint" in r for r in res["reasons"]), res["reasons"]


def test_edit_invalidates_hmac(tmp):
    nl = good_netlist()
    _, cert = _sign_and_verify(METHOD_HMAC, nl, key_dir=tmp)

    edited = copy.deepcopy(nl)              # swap a net pin
    edited["nets"][2]["pins"] = ["U1.3", "C2.1", "EXTRA.X"]
    res = verify(cert, edited, rule_version=RULE_VERSION, key_dir=tmp)
    assert res["valid"] is False
    assert any("netlist fingerprint" in r for r in res["reasons"]), res["reasons"]


def test_tampered_evidence_invalid(tmp):
    """Modifying the certified evidence must break the record+signature."""
    rec = build_record(good_netlist(), clean_evidence(), RULE_VERSION)
    cert = sign_record(rec, method=METHOD_NODE_ED25519, key_dir=tmp)

    forged = copy.deepcopy(cert)            # flip erc exit_code in stored evidence
    forged["subject"]["checks"][0]["exit_code"] = 1
    res = verify(forged, good_netlist(), rule_version=RULE_VERSION, key_dir=tmp)
    assert res["valid"] is False
    assert any("record digest" in r for r in res["reasons"]), res["reasons"]


def test_wrong_rule_version_invalid(tmp):
    _, cert = _sign_and_verify(METHOD_NODE_ED25519, good_netlist(), key_dir=tmp)
    res = verify(cert, good_netlist(), rule_version="multilayer_rules.v2.0",
                 key_dir=tmp)
    assert res["valid"] is False
    assert any("rule version" in r for r in res["reasons"]), res["reasons"]


def test_unsigned_cert_invalid(tmp):
    rec = build_record(good_netlist(), clean_evidence(), RULE_VERSION)
    res = verify(rec, good_netlist(), rule_version=RULE_VERSION, key_dir=tmp)
    assert res["valid"] is False
    assert any("unsigned" in r for r in res["reasons"]), res["reasons"]


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------
def main():
    with tempfile.TemporaryDirectory(prefix="pcbgenius_cert_") as d:
        tmp = Path(d)
        tests = [
            ("record built from ERC/DRC/ngspice evidence", test_record_shape),
            ("good design -> VALID (node-crypto Ed25519)", lambda: test_good_design_valid_ed25519(tmp)),
            ("good design -> VALID (stdlib HMAC fallback)", lambda: test_good_design_valid_hmac(tmp)),
            ("ANY edit to netlist -> INVALID (Ed25519)", lambda: test_edit_invalidates_ed25519(tmp)),
            ("ANY edit to netlist -> INVALID (HMAC)", lambda: test_edit_invalidates_hmac(tmp)),
            ("tampered evidence -> INVALID", lambda: test_tampered_evidence_invalid(tmp)),
            ("different rule-set version -> INVALID", lambda: test_wrong_rule_version_invalid(tmp)),
            ("unsigned cert -> INVALID", lambda: test_unsigned_cert_invalid(tmp)),
        ]
        passed = 0
        for name, fn in tests:
            passed += 1 if _run(name, fn) else 0

        print(f"\n{passed}/{len(tests)} checks passed")
        # Also write a sample cert for eyeballing.
        rec = build_record(good_netlist(), clean_evidence(), RULE_VERSION)
        cert = sign_record(rec, key_dir=tmp)
        Path("sample_cert.json").write_text(
            json.dumps(cert, indent=2, sort_keys=True), encoding="utf-8")
        print(f"wrote sample_cert.json ({cert['signature']['method']})")
        return 0 if passed == len(tests) else 1


if __name__ == "__main__":
    sys.exit(main())