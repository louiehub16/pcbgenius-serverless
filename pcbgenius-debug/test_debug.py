#!/usr/bin/env python3
"""PCBGenius — D8 AI TESTING & DEBUGGING ASSISTANT: tests.

* 3 distinct contract-valid netlists -> each yields a *valid* deterministic
  test plan (rails found, sequencing ordered, load steps present).
* A KNOWN fault (a floating net referencing a nonexistent rail, plus a short
  keyword) fed to `triage` fallback lands on the RIGHT subsystem
  (missing_net / short_circuit) WITHOUT any LLM or network.

Uses only testplan.py + triage.py. Pure stdlib, deterministic, no network.
Run:  python test_debug.py
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import testplan
import triage


# ── Three distinct contract-valid netlists (mirror datagen templates) ────────
def _net(name, cls, pins):
    return {"name": name, "pins": pins, "class": cls}


def _comp(ref, ctype, value, pins):
    p = [{"number": str(i + 1), "name": n, "net": net_} for i, (n, net_) in enumerate(pins)]
    return {"ref": ref, "type": ctype, "value": value, "package": "0805",
            "mpn": None, "pins": p, "properties": {}}


def _idx_schema(comps, nets, name):
    return {
        "schema_version": "1.0.0",
        "metadata": {"design_name": name, "board_layers": 2, "description": name,
                     "created_by": "pcbgenius", "target_fab": None},
        "components": comps, "nets": nets,
    }


# 1) LDO regulator (single power rail)
LDO = _idx_schema([
    _comp("U1", "ic", "AMS1117-3.3", [("VIN", "VIN"), ("GND", "GND"), ("VOUT", "VCC_3V3")]),
    _comp("C1", "capacitor", "10uF", [("1", "VIN"), ("2", "GND")]),
    _comp("C2", "capacitor", "10uF", [("1", "VCC_3V3"), ("2", "GND")]),
], [
    _net("VIN", "power", ["U1.VIN", "C1.1"]),
    _net("GND", "ground", ["U1.GND", "C1.2", "C2.2"]),
    _net("VCC_3V3", "power", ["U1.VOUT", "C2.1"]),
], "ldo_debug")

# 2) Buck converter (source + derived rails => sequencing dependency)
BUCK = _idx_schema([
    _comp("U1", "ic", "LM2596S-ADJ", [("VIN", "VIN"), ("GND", "GND"), ("OUT", "SW"), ("FB", "FB")]),
    _comp("D1", "diode", "SS34", [("A", "SW"), ("K", "VOUT")]),
    _comp("L1", "inductor", "33uH", [("1", "SW"), ("2", "VOUT")]),
    _comp("C1", "capacitor", "100uF", [("1", "VIN"), ("2", "GND")]),
    _comp("C2", "capacitor", "220uF", [("1", "VOUT"), ("2", "GND")]),
], [
    _net("VIN", "power", ["U1.VIN", "C1.1"]),
    _net("GND", "ground", ["U1.GND", "D1.K", "C1.2", "C2.2"]),
    _net("SW", "power", ["U1.OUT", "D1.A", "L1.1"]),
    _net("VOUT", "power", ["D1.K", "L1.2", "C2.1"]),
], "buck_debug")

# 3) LED blinker w/ MCU (signal net, single rail)
BLINK = _idx_schema([
    _comp("U1", "ic", "ATtiny85", [("VCC", "VCC"), ("GND", "GND"), ("PB0", "NET_LED")]),
    _comp("R1", "resistor", "330", [("1", "NET_LED"), ("2", "VCC")]),
    _comp("LED1", "led", "red", [("A", "NET_LED"), ("K", "GND")]),
], [
    _net("VCC", "power", ["U1.VCC", "R1.2"]),
    _net("GND", "ground", ["U1.GND", "LED1.K"]),
    _net("NET_LED", "signal", ["U1.PB0", "R1.1", "LED1.A"]),
], "blinker_debug")

# ── Faults for triage ─────────────────────────────────────────────────────────
# Known fault A: floating/unknown rail -> must land on missing_net
FAULT_MISSING_NET = (
    "ngspice: no such net \"VCC_5V_DROPPED\"\n"
    "    U2.VIN is not connected to any net\n"
    "Error: unconnected pins detected in netlist export."
)
# Known fault B: power short -> must land on short_circuit
FAULT_SHORT = (
    "KiCad DRC: UnroutedShort VCC_3V3 to GND\n"
    "pcbnew: copper overlap between TrackV1 and TrackV2 -> short to ground."
)


def run():
    passed = 0
    failed = 0
    msg = []

    # ---- 3 designs => valid test plan with rails / sequencing / load steps
    netlists = {"LDO": LDO, "BUCK": BUCK, "BLINK": BLINK}
    plan_checks = 0
    for name, nl in netlists.items():
        plan = testplan.generate_test_plan(nl)
        ok = (plan["valid"] is True and len(plan["rails"]) >= 2
              and len(plan["sequencing"]) >= 2 and len(plan["load_steps"]) >= 2)
        plan_checks += 1
        if ok:
            passed += 1
            msg.append(f"[pass] {name}: valid plan, "
                       f"{len(plan['rails'])} rails, {len(plan['sequencing'])} seq, "
                       f"{len(plan['load_steps'])} load steps")
        else:
            failed += 1
            msg.append(f"[FAIL] {name}: plan invalid -> {json.dumps(plan)}")

    # ---- determinism: same netlist => identical plan bytes
    a = json.dumps(testplan.generate_test_plan(BUCK), sort_keys=True)
    b = json.dumps(testplan.generate_test_plan(BUCK), sort_keys=True)
    ok = a == b
    passed += ok; failed += (not ok)
    msg.append(f"[{'pass' if ok else 'FAIL'}] determinism: buck plan byte-identical")

    # ---- known fault A: triage fallback lands on missing_net (no LLM)
    r = triage.triage(FAULT_MISSING_NET, history=[])
    ok = r["root_cause"] == "missing_net" and r["tool"] == "ngspice" \
         and r["used_llm"] is False
    passed += ok; failed += (not ok)
    msg.append(f"[{'pass' if ok else 'FAIL'}] fault A -> "
               f"root_cause={r['root_cause']} (want missing_net), tool={r['tool']}")

    # ---- known fault B: triage fallback lands on short_circuit (no LLM)
    r2 = triage.triage(FAULT_SHORT, history=[])
    ok = r2["root_cause"] == "short_circuit" and r2["tool"] == "kicad" \
         and r2["used_llm"] is False
    passed += ok; failed += (not ok)
    msg.append(f"[{'pass' if ok else 'FAIL'}] fault B -> "
               f"root_cause={r2['root_cause']} (want short_circuit), tool={r2['tool']}")

    # ---- fallback conditions: empty log -> unknown, no crash
    r3 = triage.triage("", history=[])
    ok = r3["root_cause"] == "unknown"
    passed += ok; failed += (not ok)
    msg.append(f"[{'pass' if ok else 'FAIL'}] empty log -> root_cause={r3['root_cause']} (want unknown)")

    # ---- history is recorded
    hist = []
    triage.triage(FAULT_MISSING_NET, history=hist)
    ok = len(hist) == 1 and hist[0]["root_cause"] == "missing_net"
    passed += ok; failed += (not ok)
    msg.append(f"[{'pass' if ok else 'FAIL'}] history recorded {len(hist)} triage call(s)")

    # ---- testplan degrades gracefully on garbage
    bad = testplan.generate_test_plan("not json at all")
    ok = bad["valid"] is False and bad["rails"] == []
    passed += ok; failed += (not ok)
    msg.append(f"[{'pass' if ok else 'FAIL'}] garbage netlist -> valid=False, no rails")

    print("\n".join(msg))
    print(f"\nD8 RESULT: {passed} passed, {failed} failed, "
          f"{plan_checks}/{len(netlists)} designs produced valid plans")
    return failed


if __name__ == "__main__":
    sys.exit(1 if run() else 0)