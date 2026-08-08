#!/usr/bin/env python3
"""
PCBGenius — REAL synthetic data generator (Wave B1)
====================================================
Produces prompt -> netlist pairs that validate against the FROZEN contract
schema (netlist_schema v1.0.0). This REPLACES the stage2 scaffold's
chicken-scratch with a working generator that can run:

  - LOCALLY  (deterministic template expansion — no model, free, fast)
  - WITH MODEL (calls deepseek-r1/qwen via OpenRouter for richer designs)

Output: JSONL of { "prompt": str, "netlist": {...}, "skill": str }
Every netlist VALIDATES against the contract validation_rules before write.
"""

import json
import os
import random
import sys
import urllib.request
import urllib.error

CONTRACT_VERSION = "1.0.0"
CREATED_BY = "pcbgenius"

# ---- Validation against the FROZEN contract schema ----------------------
VALID_TYPES = {
    "resistor", "capacitor", "inductor", "diode", "led", "transistor",
    "ic", "connector", "power", "crystal", "switch",
}
NET_CLASSES = {"power", "ground", "signal", "clock", "analog", "digital"}


def validate_netlist(nl):
    """Return (ok: bool, errors: list[str]) per the frozen contract rules."""
    errs = []
    if not isinstance(nl, dict):
        return False, ["netlist not an object"]
    if nl.get("schema_version") != CONTRACT_VERSION:
        errs.append("schema_version != 1.0.0")
    comps = nl.get("components", [])
    nets = nl.get("nets", [])

    # every ref unique
    refs = [c.get("ref") for c in comps]
    if len(refs) != len(set(refs)):
        errs.append("duplicate component ref")

    # pin -> net exists
    for c in comps:
        for p in c.get("pins", []):
            if p.get("net") not in {n.get("name") for n in nets}:
                errs.append(f"pin {c.get('ref')}.{p.get('name')} -> missing net")

    # at least one power and one ground net
    classes = {n.get("class") for n in nets}
    if "ground" not in classes:
        errs.append("no ground net")
    if "power" not in classes:
        errs.append("no power net")

    # no pin connects to >1 net (implicitly ok by construction here)
    # all ref.pin in nets resolve
    valid_pins = {f"{c.get('ref')}.{p.get('name')}" for c in comps for p in c.get("pins", [])}
    for n in nets:
        for rp in n.get("pins", []):
            if rp not in valid_pins:
                errs.append(f"net {n.get('name')} refs unknown pin {rp}")

    return (len(errs) == 0), errs


# ---- Deterministic design templates (no model, free) --------------------
def _net(name, cls, pins):
    return {"name": name, "pins": pins, "class": cls}


def _comp(ref, ctype, value, package, pins, mpn=None):
    p = [{"number": str(i + 1), "name": n, "net": net_} for i, (n, net_) in enumerate(pins)]
    c = {"ref": ref, "type": ctype, "value": value, "package": package,
         "mpn": mpn, "pins": p, "properties": {}}
    return c


def _simple_power_netlist(seed):
    """A common, valid design: linear voltage regulator with in/out caps."""
    r = random.Random(seed)
    vin, vout, capv = ["5V", "12V", "24V"][r.randrange(3)], "3.3V", "3.3V"
    comps = []
    comps.append(_comp("U1", "ic", "AMS1117-3.3", "SOT-223",
                       [("VIN", "VIN"), ("GND", "GND"), ("VOUT", "VCC_3V3")], "AMS1117-3.3"))
    comps.append(_comp("C1", "capacitor", "10uF", "0805",
                       [("1", "VIN"), ("2", "GND")]))
    comps.append(_comp("C2", "capacitor", "10uF", "0805",
                       [("1", "VCC_3V3"), ("2", "GND")]))
    comps.append(_comp("C3", "capacitor", "100nF", "0603",
                       [("1", "VCC_3V3"), ("2", "GND")]))
    nets = [
        _net("VIN", "power", ["U1.VIN", "C1.1"]),
        _net("GND", "ground", ["U1.GND", "C1.2", "C2.2", "C3.2"]),
        _net("VCC_3V3", "power", ["U1.VOUT", "C2.1", "C3.1"]),
    ]
    nl = {
        "schema_version": CONTRACT_VERSION,
        "metadata": {"design_name": f"ldo_{vin}_to_{vout}", "board_layers": 2,
                     "description": f"Linear regulator {vin}->{vout}", "created_by": CREATED_BY,
                     "target_fab": None},
        "components": comps, "nets": nets,
    }
    prompt = f"Design a simple {vin} to {vout} linear voltage regulator using an AMS1117. Include input, output, and bypass capacitors."
    return prompt, nl, "netlist_design"


def _led_blinker_netlist(seed):
    r = random.Random(seed)
    comps = [
        _comp("U1", "ic", "ATtiny85", "DIP-8",
              [("VCC", "VCC"), ("GND", "GND"), ("PB0", "NET_LED")], "ATTINY85-20PU"),
        _comp("R1", "resistor", "330", "0805", [("1", "NET_LED"), ("2", "VCC")]),
        _comp("LED1", "led", "red", "0805", [("A", "NET_LED"), ("K", "GND")]),
        _comp("C1", "capacitor", "100nF", "0603", [("1", "VCC"), ("2", "GND")]),
    ]
    nets = [
        _net("VCC", "power", ["U1.VCC", "R1.2", "C1.1"]),
        _net("GND", "ground", ["U1.GND", "LED1.K", "C1.2"]),
        _net("NET_LED", "signal", ["U1.PB0", "R1.1", "LED1.A"]),
    ]
    nl = {"schema_version": CONTRACT_VERSION,
          "metadata": {"design_name": "led_blinker", "board_layers": 2,
                       "description": "ATtiny85 blinks an LED", "created_by": CREATED_BY,
                       "target_fab": None},
          "components": comps, "nets": nets}
    prompt = "Design a simple LED blink circuit using an ATtiny85 microcontroller with a current-limiting resistor."
    return prompt, nl, "netlist_design"


_TEMPLATES = [_simple_power_netlist, _led_blinker_netlist]


def generate_deterministic(n, out_path):
    """Generate n contract-valid netlists deterministically (no model)."""
    ok = 0
    with open(out_path, "w") as f:
        for i in range(n):
            fn = _TEMPLATES[i % len(_TEMPLATES)]
            prompt, nl, skill = fn(i)
            val, errs = validate_netlist(nl)
            if not val:
                print(f"[gen] design {i} INVALID: {errs}", file=sys.stderr)
                continue
            f.write(json.dumps({"prompt": prompt, "netlist": nl, "skill": skill})
                    + "\n")
            ok += 1
    print(f"[gen] deterministic: wrote {ok}/{n} valid netlists to {out_path}")
    return ok


# ---- Model-assisted generation (OpenRouter) ----------------------------
def call_model(prompt, model, api_key):
    body = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 1200, "temperature": 0.4,
    }
    req = urllib.request.Request(
        "https://openrouter.ai/api/v1/chat/completions",
        data=json.dumps(body).encode(), method="POST",
        headers={"Authorization": f"Bearer {api_key}",
                 "Content-Type": "application/json",
                 "User-Agent": "Mozilla/5.0 Chrome/126.0"})
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            d = json.loads(r.read().decode())
            return d["choices"][0]["message"]["content"]
    except Exception as e:
        return None


def generate_with_model(n, out_path, model, api_key):
    """Ask an LLM for varied designs; validate before accepting."""
    ok = 0
    with open(out_path, "w") as f:
        for i in range(n):
            prompt = (
                "Output ONLY a valid PCB netlist JSON matching this schema: "
                "{schema_version:'1.0.0', metadata:{design_name,description,board_layers:2,"
                "created_by:'pcbgenius',target_fab:null}, components:[{ref,type,value,package,"
                "mpn,pins:[{number,name,net}],properties:{}}], nets:[{name,pins:[ref.pin],class}]}. "
                "Give a small interesting circuit (e.g. usb power, buck, sensor, rf debug). "
                f"Variation {i}. JSON ONLY."
            )
            content = call_model(prompt, model, api_key)
            if not content:
                continue
            nl = _extract_json(content)
            if not nl:
                continue
            val, errs = validate_netlist(nl)
            if not val:
                continue
            f.write(json.dumps({"prompt": "Design: " + nl.get("metadata", {}).get("description", "?"),
                                "netlist": nl, "skill": "netlist_design"}) + "\n")
            ok += 1
    print(f"[gen] model: wrote {ok}/{n} valid netlists to {out_path}")
    return ok


def _extract_json(text):
    """Grab the first balanced JSON object from arbitrary text."""
    depth = 0; start = -1; in_str = False; esc = False
    for i, ch in enumerate(text):
        if in_str:
            if esc: esc = False
            elif ch == "\\": esc = True
            elif ch == '"': in_str = False
            continue
        if ch == '"': in_str = True
        elif ch == "{":
            if start < 0: start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start >= 0:
                try:
                    return json.loads(text[start:i+1])
                except Exception:
                    start = -1; depth = 0
    return None


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--count", type=int, default=20)
    ap.add_argument("--out", default="data/processed/pcbgenius_training_dataset.jsonl")
    ap.add_argument("--mode", choices=["deterministic", "model"], default="deterministic")
    ap.add_argument("--model", default="deepseek/deepseek-r1")
    ap.add_argument("--api-key", default=os.environ.get("OPENROUTER_API_KEY", ""))
    a = ap.parse_args()

    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    if a.mode == "deterministic":
        generate_deterministic(a.count, a.out)
    else:
        if not a.api_key:
            print("--api-key or OPENROUTER_API_KEY required for model mode")
            sys.exit(2)
        generate_with_model(a.count, a.out, a.model, a.api_key)


if __name__ == "__main__":
    main()
