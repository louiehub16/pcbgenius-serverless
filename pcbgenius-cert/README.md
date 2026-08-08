# pcbgenius-cert — E1 Provably-correct certificates (feature #21)

Binds a frozen design + rule-set version + the evidence from a verification
**run** (ERC/DRC exit codes, ngspice measurements) into a tamper-evident,
locally-signed certificate, and lets a re-verifier prove that certificate is
still true for the **current** netlist + rules.

**Core property (asserted by `test_cert.py`):** a good design is certified
VALID; **any edit** to the netlist, the rules, or the stored evidence flips
`verify()` to INVALID.

## Modules
- `proof.py` — canonicalise evidence (`make_checks`) + hash the netlist /
  rules / checks into a self-describing record with a `record_digest`.
- `sign.py`   — `sign_record()`: local-key signature over `record_digest`.
  Backends:
  - **node-crypto Ed25519** (preferred; `node_signer.js` subprocess, marked
    `[E1 ext:node]`). Keypair cached under `~/.pcbgenius-cert/`; the public key
    is embedded in the cert for verification.
  - **stdlib HMAC-SHA256** fallback (tamper-evident only, not an authenticity
    proof) so the flow runs with zero non-stdlib deps.
- `verify.py`  — re-verify a cert against current netlist (+ optional
  rule version). Reports *why* an edit invalidated an earlier-good cert
  (netlist fingerprint / record digest / rule version / signature).
- `test_cert.py` — E1 acceptance tests (plain stdlib):
      python test_cert.py
- `node_signer.js` — the external node-crypto signer subprocess.

## Use
```python
from proof import build_record, make_checks
from sign import sign_record
from verify import verify

rec = build_record(netlist, make_checks(
    erc={"exit_code": 0}, drc={"pass": True, "violations": []},
    ngspice=[{"name":"vout","value":3.31,"unit":"V","ok":True}]))
cert = sign_record(rec)                       # local key
result = verify(cert, netlist, rule_version="multilayer_rules.v1.0")
assert result["valid"]
```

No npm / docker / git / network required to run the tests.

## Security note
This is a **project-local** hardware-raised certificate. It proves content
integrity + (with node) that the holder of `~/.pcbgenius-cert/` signed it; it is
not a CA-issued authenticity certificate. The private key never leaves the
local key dir.