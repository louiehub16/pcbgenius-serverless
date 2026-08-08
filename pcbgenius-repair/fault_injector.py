#!/usr/bin/env python3
"""PCBGenius — C4 REPAIR-MY-BOARD (feature #27): fault injector.

Loads a *good* contract-netlist (schema v1.0.0: schema_version, metadata,
components[{ref,type,value,package,mpn,pins[{number,name,net}],properties}],
nets[{name,pins:[ref.pin],class}]) and deterministically injects real-world
board faults.  Every injected fault produces a structured record:

    {
      "fault":  str  # fault-class key, e.g. "wrong_value_r"
      "symptom": str # natural-language symptom a user might report
      "symptom_features": {str: int|float}  # deterministic feature vector used
                                             # for similarity retrieval
      "diagnosis": str
      "fix": str
      "good_design": str  # source good design name
      "changed_refs": [str]
      "changed_nets": [str]
    }

Design conventions follow the FROZEN contract + the existing datagen templates
(LDO / LED blinker / buck converter / USB power) so records validate against the
same schema.  Pure stdlib, deterministic (same seed -> same records), no network.
"""

from __future__ import annotations

import json
import random
from typing import Any, Dict, List, Optional

CONTRACT_VERSION = "1.0.0"
CREATED_BY = "pcbgenius"

VALID_TYPES = {
    "resistor", "capacitor", "inductor", "diode", "led", "transistor",
    "ic", "connector", "power", "crystal", "switch",
}
NET_CLASSES = {"power", "ground", "signal", "clock", "analog", "digital"}

# ─────────────────────────────────────────────────────────────────────────────
# Contract validation (mirrors datagen/generate_netlists.py — do not diverge)
# ─────────────────────────────────────────────────────────────────────────────
def validate_netlist(nl: Any) -> tuple[bool, List[str]]:
    """Return (ok, errors) per the FROZEN contract rules."""
    errs: List[str] = []
    if not isinstance(nl, dict):
        return False, ["netlist not an object"]
    if nl.get("schema_version") != CONTRACT_VERSION:
        errs.append("schema_version != 1.0.0")
    comps = nl.get("components", [])
    nets = nl.get("nets", [])
    refs = [c.get("ref") for c in comps]
    if len(refs) != len(set(refs)):
        errs.append("duplicate component ref")
    net_names = {n.get("name") for n in nets}
    for c in comps:
        for p in c.get("pins", []):
            if p.get("net") not in net_names:
                errs.append(f"pin {c.get('ref')}.{p.get('name')} -> missing net")
    classes = {n.get("class") for n in nets}
    if "ground" not in classes:
        errs.append("no ground net")
    if "power" not in classes:
        errs.append("no power net")
    valid_pins = {f"{c.get('ref')}.{p.get('name')}" for c in comps for p in c.get("pins", [])}
    for n in nets:
        for rp in n.get("pins", []):
            if rp not in valid_pins:
                errs.append(f"net {n.get('name')} refs unknown pin {rp}")
    return (len(errs) == 0), errs


def deep_copy(nl: Dict[str, Any]) -> Dict[str, Any]:
    return json.loads(json.dumps(nl))


# ─────────────────────────────────────────────────────────────────────────────
# Deterministic good-design generation (contract-valid)
# ─────────────────────────────────────────────────────────────────────────────
def _comp(ref, ctype, value, package, pins, mpn=None):
    p = [{"number": str(i + 1), "name": n, "net": net_} for i, (n, net_) in enumerate(pins)]
    return {"ref": ref, "type": ctype, "value": value, "package": package,
            "mpn": mpn, "pins": p, "properties": {}}


def _net(name, cls, pins):
    return {"name": name, "pins": pins, "class": cls}


def _buck_template(seed: int) -> Dict[str, Any]:
    """Buck converter (LM2596). Supports ALL 10 fault classes: R, C, L, diode,
    two-pin passives for swaps/shorts/open, mult-component feedback divider."""
    r = random.Random(seed)
    vin = r.choice(["9V", "12V", "24V"])
    vout = r.choice(["3.3V", "5V"])
    comps = [
        _comp("U1", "ic", "LM2596S-ADJ", "TO-263", [("VIN", "VIN"), ("GND", "GND"), ("OUT", "SW"), ("FB", "FB")], "LM2596S-ADJ"),
        _comp("D1", "diode", "SS34", "SMA", [("A", "SW"), ("K", "VOUT")], "SS34"),
        _comp("L1", "inductor", "33uH", "CDRH8D28", [("1", "SW"), ("2", "VOUT")]),
        _comp("C1", "capacitor", "100uF", "10x10mm", [("1", "VIN"), ("2", "GND")]),
        _comp("C2", "capacitor", "220uF", "10x10mm", [("1", "VOUT"), ("2", "GND")]),
        _comp("R1", "resistor", "1k", "0805", [("1", "VOUT"), ("2", "FB")]),
        _comp("R2", "resistor", "3.3k", "0805", [("1", "FB"), ("2", "GND")]),
    ]
    nets = [
        _net("VIN", "power", ["U1.VIN", "C1.1"]),
        _net("GND", "ground", ["U1.GND", "D1.K", "C1.2", "C2.2", "R2.2"]),
        _net("SW", "power", ["U1.OUT", "D1.A", "L1.1"]),
        _net("VOUT", "power", ["D1.K", "L1.2", "C2.1", "R1.1"]),
        _net("FB", "analog", ["U1.FB", "R1.2", "R2.1"]),
    ]
    return {"schema_version": CONTRACT_VERSION,
            "metadata": {"design_name": f"buck_{vin}_to_{vout}", "board_layers": 2,
                         "description": f"Buck converter {vin}->{vout} with LM2596", "created_by": CREATED_BY,
                         "target_fab": "jlcpcb"},
            "components": comps, "nets": nets}


_templates = [_buck_template]  # buck covers every fault class; extend freely


def load_good_netlist(seed: int) -> Dict[str, Any]:
    """Return a deterministic, contract-valid GOOD netlist for the given seed.

    Guarantees all 10 fault-classes can be injected (buck adds value variation
    per seed for realistic diversity while staying schema-valid)."""
    nl = _templates[seed % len(_templates)](seed)
    nl = _vary_design(nl, seed)
    ok, errs = validate_netlist(nl)
    if not ok:
        raise ValueError(f"bad good netlist: {errs}")
    return nl


def _vary_design(nl: Dict[str, Any], seed: int) -> Dict[str, Any]:
    """Deterministically perturb R/C/L values per seed (still contract-valid)."""
    r = random.Random(seed)
    nl = deep_copy(nl)
    for c in nl.get("components", []):
        t = c.get("type")
        if t == "resistor":
            c["value"] = r.choice(["1k", "3.3k", "10k", "47k"])
        elif t == "capacitor":
            c["value"] = r.choice(["10uF", "100uF", "220uF", "470uF"])
        elif t == "inductor":
            c["value"] = r.choice(["22uH", "33uH", "47uH", "100uH"])
    return nl


# ─────────────────────────────────────────────────────────────────────────────
# Feature / signature extraction
# ─────────────────────────────────────────────────────────────────────────────
def describe_netlist(nl: Dict[str, Any]) -> Dict[str, float]:
    """Deterministic feature dict describing a netlist's structural signature.

    Used for similarity matching: counts of component types, value families,
    net classes, presence of special nets/refs, and per-organ topology markers.
    All keys are stable strings so vectors compare across records."""
    feats: Dict[str, float] = {}
    comps = nl.get("components", [])
    nets = nl.get("nets", [])

    type_counts: Dict[str, int] = {}
    value_set: set = set()
    special_nets = {"VIN", "VCC", "GND", "VOUT", "FB", "SW", "EN", "VBUS"}
    present_special: set = set()
    saw_res = saw_ind = saw_diod = saw_fb_divider = False

    for c in comps:
        t = c.get("type")
        type_counts[t] = type_counts.get(t, 0) + 1
        v = str(c.get("value", "")).lower()
        # value family buckets (suffix of the value string)
        fam = v[-4:] if v else "none"
        value_set.add(f"val:{t}:{fam}")
        ref = str(c.get("ref", "")).rstrip("0123456789")  # 'R' from 'R1'
        value_set.add(f"ref_prefix:{ref}")
        if t == "resistor":
            saw_res = True
        if t == "inductor":
            saw_ind = True
        if t == "diode":
            saw_diod = True
        for p in c.get("pins", []):
            n = p.get("net", "")
            if n in special_nets:
                present_special.add(n)
        # feedback divider hint: R1 + R2 both present
        if ref == "R" and type_counts.get("resistor", 0) >= 2:
            saw_fb_divider = True

    for t, k in type_counts.items():
        feats[f"comp:{t}"] = float(k)
    nc: Dict[str, int] = {}
    for n in nets:
        nc[n.get("class", "signal")] = nc.get(n.get("class", "signal"), 0) + 1
    for cls, k in nc.items():
        feats[f"net_class:{cls}"] = float(k)
    for v in value_set:
        feats[f"nt:{v}"] = 1.0
    for sn in present_special:
        feats[f"net:{sn}"] = 1.0
    feats["topo:buck_diode"] = 1.0 if (saw_diod and saw_ind) else 0.0
    feats["topo:feedback_divider"] = 1.0 if saw_fb_divider else 0.0
    feats["n_comps"] = float(len(comps))
    feats["n_nets"] = float(len(nets))
    return feats


def _pick(coll):
    lst = list(coll)
    return lst[0] if lst else None


# ─────────────────────────────────────────────────────────────────────────────
# Fault shapes
# ─────────────────────────────────────────────────────────────────────────────
FAULTS = [
    "wrong_value_r", "wrong_value_c", "wrong_value_l", "swapped_pins",
    "missing_cap", "wrong_feedback_divider", "reversed_diode",
    "shorted_pins", "open_net", "missing_resistor",
]

_KNOWN_VALUES = {
    "resistor": ["100", "330", "1k", "3.3k", "10k", "100k"],
    "capacitor": ["100nF", "1uF", "10uF", "100uF", "220uF", "470uF"],
    "inductor": ["22uH", "33uH", "47uH", "100uH"],
}


def _per_comp(nl, ctype):
    return [c for c in nl["components"] if c.get("type") == ctype]


def _find_pin_nets(c, pin_name):
    for p in c.get("pins", []):
        if p.get("name") == pin_name:
            return p.get("net")
    return None


def _rename_signal_net(nl, old, new):
    """Set every pin that was on `old` net to `new` inside components & re-list."""
    for c in nl["components"]:
        for p in c.get("pins", []):
            if p.get("net") == old:
                p["net"] = new
    for n in nl["nets"]:
        if n.get("name") == old:
            n["name"] = new
            n["pins"] = [f"{c['ref']}.{p['name']}"
                         for c in nl["components"] for p in c.get("pins", [])
                         if p.get("net") == new]
            # if the net now has a single (or zero) pin it is effectively open / floating
    return nl


def _rebuild_net_pins(nl, net_name):
    """Recompute net.pins from component pin nets (keeps schema consistent)."""
    for n in nl["nets"]:
        if n.get("name") != net_name:
            continue
        n["pins"] = [f"{c['ref']}.{p['name']}"
                     for c in nl["components"] for p in c.get("pins", [])
                     if p.get("net") == net_name]
    return nl


def _add_stray_net(nl, name, pins, nclass="signal"):
    nl["nets"].append({"name": name, "pins": pins, "class": nclass})
    return nl


# ─────────────────────────────────────────────────────────────────────────────
# Individual fault injectors. Each returns (good, faulty, symptom, diagnosis,
# fix, changed_refs, changed_nets) or None when not applicable.
# ─────────────────────────────────────────────────────────────────────────────
def _inj_wrong_value(nl, rng, ctype, fault_key, good=None):
    cands = _per_comp(nl, ctype)
    if not cands:
        return None
    c = rng.choice(cands)
    good_val = c["value"]
    family = [v for v in _KNOWN_VALUES[ctype] if v != good_val]
    bad = rng.choice(family)
    faulty = deep_copy(nl)
    fc = next(x for x in faulty["components"] if x["ref"] == c["ref"])
    fc["value"] = bad
    label = {"resistor": "a resistor", "capacitor": "a capacitor", "inductor": "an inductor"}[ctype]
    symptom = (f"Component {c['ref']} ({label}) reads {bad} but the design calls "
               f"for {good_val}; the {c['ref'].lstrip('RC') or ctype} network diverges.")
    diagnosis = f"{c['ref']} has the wrong value ({bad} vs expected {good_val}), shifting the set-point."
    fix = f"Replace {c['ref']} with the correct value ({good_val}) and re-verify the {next(p for p in fc['pins']).get('net', 'output')} node."
    return faulty, symptom, diagnosis, fix, [c["ref"]], list({p["net"] for p in c["pins"]})


def _inj_wrong_value_r(nl, rng):
    return _inj_wrong_value(nl, rng, "resistor", "wrong_value_r")


def _inj_wrong_value_c(nl, rng):
    return _inj_wrong_value(nl, rng, "capacitor", "wrong_value_c")


def _inj_wrong_value_l(nl, rng):
    return _inj_wrong_value(nl, rng, "inductor", "wrong_value_l")


def _inj_swapped_pins(nl, rng):
    """Swap the two nets of a 2-pin passive (R / C / L) so the part is rotated."""
    cands = [c for c in nl["components"] if c["type"] in ("resistor", "capacitor", "inductor") and len(c["pins"]) == 2]
    if not cands:
        return None
    c = rng.choice(cands)
    p0, p1 = c["pins"][0], c["pins"][1]
    n0, n1 = p0["net"], p1["net"]
    faulty = deep_copy(nl)
    fc = next(x for x in faulty["components"] if x["ref"] == c["ref"])
    fc["pins"][0]["net"], fc["pins"][1]["net"] = n1, n0
    _rebuild_net_pins(faulty, n0)
    _rebuild_net_pins(faulty, n1)
    symptom = f"{c['ref']} looks like it is rotated: pin nets {n0} and {n1} are crossed."
    diagnosis = f"{c['ref']} has its pins swapped ({n0} <-> {n1}), shorting placement."
    fix = f"Rotate/reorient {c['ref']} so pin1 connects {n0} and pin2 connects {n1}."
    return faulty, symptom, diagnosis, fix, [c["ref"]], [n0, n1]


def _inj_missing_cap(nl, rng):
    cands = _per_comp(nl, "capacitor")
    if not cands:
        return None
    c = rng.choice(cands)
    to_drop = set(p["net"] for p in c["pins"])
    faulty = deep_copy(nl)
    faulty["components"] = [x for x in faulty["components"] if x["ref"] != c["ref"]]
    for n in faulty["nets"]:
        for p in c["pins"]:
            rp = f"{c['ref']}.{p['name']}"
            if rp in n.get("pins", []):
                n["pins"].remove(rp)
    symptom = f"A capacitor on {sorted(to_drop)} appears to be missing from the board (design lists {c['ref']})."
    diagnosis = f"{c['ref']} (decoupling/filter cap) is not placed, degrading {sorted(to_drop)}."
    fix = f"Place {c['ref']} (value {c['value']}) bridging {sorted(to_drop)}."
    return faulty, symptom, diagnosis, fix, [c["ref"]], list(to_drop)


def _inj_missing_resistor(nl, rng):
    cands = _per_comp(nl, "resistor")
    if len(cands) < 1:
        return None
    c = rng.choice(cands)
    to_drop = set(p["net"] for p in c["pins"])
    faulty = deep_copy(nl)
    faulty["components"] = [x for x in faulty["components"] if x["ref"] != c["ref"]]
    for n in faulty["nets"]:
        for p in c["pins"]:
            rp = f"{c['ref']}.{p['name']}"
            if rp in n.get("pins", []):
                n["pins"].remove(rp)
    symptom = f"The board is missing resistor {c['ref']} that should sit between {sorted(to_drop)}."
    diagnosis = f"{c['ref']} is not populated, leaving {sorted(to_drop)} disconnected."
    fix = f"Solder {c['ref']} (value {c['value']}) between {sorted(to_drop)}."
    return faulty, symptom, diagnosis, fix, [c["ref"]], list(to_drop)


def _inj_wrong_feedback_divider(nl, rng):
    rs = [c for c in _per_comp(nl, "resistor") if c["ref"].startswith("R")]
    if len(rs) < 2:
        return None
    ra, rb = rs[0], rs[1]
    faulty = deep_copy(nl)
    fa = next(x for x in faulty["components"] if x["ref"] == ra["ref"])
    fb = next(x for x in faulty["components"] if x["ref"] == rb["ref"])
    new_ra = "10k" if fa["value"] != "10k" else "1k"
    new_rb = "10k" if fb["value"] != "10k" else "1k"
    fa["value"] = new_ra
    fb["value"] = new_rb
    symptom = (f"The feedback divider around FB is wrong: {ra['ref']}={fa['value']} and "
               f"{rb['ref']}={fb['value']}, so the output regulation point is off.")
    diagnosis = f"Feedback divider resistors set the wrong FB ratio -> shifted output voltage."
    fix = f"Restore {ra['ref']}/{rb['ref']} to the correct values so FB sits in the reference band."
    return faulty, symptom, diagnosis, fix, [ra["ref"], rb["ref"]], ["FB", "VOUT", "GND"]


def _inj_reversed_diode(nl, rng):
    cands = _per_comp(nl, "diode")
    if not cands:
        return None
    c = cands[0]
    # diode pins: anode (A) and cathode (K)
    anode_name = next((p["name"] for p in c["pins"] if p["name"] in ("A", "1")), None)
    cathode_name = next((p["name"] for p in c["pins"] if p["name"] in ("K", "2")), None)
    if not anode_name or not cathode_name:
        return None
    n_a = _find_pin_nets(c, anode_name)
    n_k = _find_pin_nets(c, cathode_name)
    faulty = deep_copy(nl)
    fc = next(x for x in faulty["components"] if x["ref"] == c["ref"])
    for p in fc["pins"]:
        if p["name"] == anode_name:
            p["net"] = n_k
        elif p["name"] == cathode_name:
            p["net"] = n_a
    _rebuild_net_pins(faulty, n_a)
    _rebuild_net_pins(faulty, n_k)
    symptom = f"Diode {c['ref']} is installed backwards: anode/cathode are on {n_k}/{n_a}."
    diagnosis = f"{c['ref']} reversed -> it blocks instead of conducting / conducts the wrong way."
    fix = f"Flip {c['ref']} so anode=({n_a}) and cathode=({n_k})."
    return faulty, symptom, diagnosis, fix, [c["ref"]], [n_a, n_k]


def _inj_shorted_pins(nl, rng):
    cands = [c for c in nl["components"] if len(c["pins"]) >= 2]
    if not cands:
        return None
    c = rng.choice(cands)
    p0, p1 = c["pins"][0], c["pins"][1]
    if p0["net"] == p1["net"]:
        return None
    n0, n1 = p0["net"], p1["net"]
    faulty = deep_copy(nl)
    fc = next(x for x in faulty["components"] if x["ref"] == c["ref"])
    fc["pins"] = [dict(fc["pins"][0]), dict(fc["pins"][1])]
    # short: both pins placed on the same net n0
    for p in fc["pins"]:
        p["net"] = n0
    # move the second pin out of net n1 and into n0
    for n in faulty["nets"]:
        n["pins"] = [rp for rp in n.get("pins", []) if rp != f"{c['ref']}.{p1['name']}"]
    _rebuild_net_pins(faulty, n0)
    symptom = f"{c['ref']} has {n0} and {n1} shorted together (bridge/solder defect)."
    diagnosis = f"A bridge or solder short joins {n0} and {n1} at {c['ref']}."
    fix = f"Clear the bridge at {c['ref']}; separate pins so {n0} and {n1} stay isolated."
    return faulty, symptom, diagnosis, fix, [c["ref"]], [n0, n1]


def _inj_open_net(nl, rng):
    cands = [c for c in nl["components"] if c.get("pins")]
    if not cands:
        return None
    c = rng.choice(cands)
    p = c["pins"][0]
    old_net = p["net"]
    new_net = f"FLT_OPEN_{c['ref']}_{p['name']}"
    # remove from original net, place on a fresh floating net -> open.
    faulty = deep_copy(nl)
    for n in faulty["nets"]:
        rp = f"{c['ref']}.{p['name']}"
        if rp in n.get("pins", []):
            n["pins"].remove(rp)
    fc = next(x for x in faulty["components"] if x["ref"] == c["ref"])
    for pp in fc["pins"]:
        if pp["name"] == p["name"]:
            pp["net"] = new_net
    _add_stray_net(faulty, new_net, [f"{c['ref']}.{p['name']}"])
    symptom = f"{c['ref']}.{p['name']} is not connected to {old_net} (open joint)."
    diagnosis = f"Cold/open solder joint at {c['ref']}.{p['name']} isolates it from {old_net}."
    fix = f"Re-solder {c['ref']}.{p['name']} so it connects to {old_net}."
    return faulty, symptom, diagnosis, fix, [c["ref"]], [old_net]


_INJECTORS = {
    "wrong_value_r": _inj_wrong_value_r,
    "wrong_value_c": _inj_wrong_value_c,
    "wrong_value_l": _inj_wrong_value_l,
    "swapped_pins": _inj_swapped_pins,
    "missing_cap": _inj_missing_cap,
    "wrong_feedback_divider": _inj_wrong_feedback_divider,
    "reversed_diode": _inj_reversed_diode,
    "shorted_pins": _inj_shorted_pins,
    "open_net": _inj_open_net,
    "missing_resistor": _inj_missing_resistor,
}


def inject_faults(nl: Dict[str, Any], seed: int = 0,
                  only: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    """Inject every applicable fault into a GOOD netlist.

    Returns a list of fault records (see module docstring).  Deterministic for a
    given (netlist, seed).  `only` restricts the fault-class set.  A record is
    always produced even if the netlist afterwards still 'validates', because a
    wrong value / open net is a real defect that no schema validator can catch.
    """
    rng = random.Random(seed)
    keys = [f for f in (only or FAULTS) if f in _INJECTORS]
    records: List[Dict[str, Any]] = []
    for fault in keys:
        res = _INJECTORS[fault](nl, rng)
        if res is None:
            # not applicable to this netlist -> skip (buck covers all)
            continue
        faulty, symptom, diagnosis, fix, refs, nets = res
        features = describe_netlist(faulty)
        records.append({
            "fault": fault,
            "symptom": symptom,
            "symptom_features": features,
            "diagnosis": diagnosis,
            "fix": fix,
            "good_design": nl.get("metadata", {}).get("design_name", "?"),
            "changed_refs": refs,
            "changed_nets": nets,
        })
        ok, _errs = validate_netlist(faulty)
        # record whether the faulty netlist is still schema-valid: useful signal
        records[-1]["faulty_valid"] = ok
    return records


def build_fault_db(n_designs: int, seed_base: int = 1) -> List[Dict[str, Any]]:
    """Build a fault library across `n_designs` good designs x all 10 faults.

    Uses the buck template so every one of the 10 fault classes is applicable on
    every design -> exactly n_designs * 10 records.  Deterministic."""
    all_records: List[Dict[str, Any]] = []
    for d in range(n_designs):
        good = load_good_netlist(seed_base + d)
        records = inject_faults(good, seed=seed_base + d)
        all_records.extend(records)
    return all_records


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="PCBGenius fault injector")
    ap.add_argument("--designs", type=int, default=2)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--sample", action="store_true", help="print first record")
    a = ap.parse_args()
    db = build_fault_db(a.designs, a.seed)
    print(f"[fault_injector] built {len(db)} fault records across {a.designs} designs")
    if a.sample and db:
        print(json.dumps(db[0], indent=2))