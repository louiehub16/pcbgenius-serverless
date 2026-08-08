"""test_iterate.py — pure diff/validation plumbing tests (NO API calls).

Covers the validate -> diff -> render pipeline on 5 representative edits:
  1. change a component value       (modified)
  2. add a capacitor                (added)
  3. remove a resistor              (removed)
  4. rename a net                   (modified: field "name")
  5. change a package               (modified)

Run:  python test_iterate.py          (or python -m unittest test_iterate -v)
"""

import json
import unittest

from validate import validate_netlist, is_valid
from diff_render import render_diff, count_changes

# ---------------------------------------------------------------------------
# Fixtures (mirror of the Wave-A sample netlist, pcbgenius-frontend/sampleNetlist.ts)
# ---------------------------------------------------------------------------

BASE_NETLIST = {
    "schema_version": "1.0.0",
    "metadata": {
        "design_name": "led_blinker",
        "description": "Blink an LED from an ESP32 GPIO pin",
        "board_layers": 2,
        "created_by": "pcbgenius",
        "target_fab": None,
    },
    "components": [
        {
            "ref": "U1", "type": "ic", "value": "ESP32-WROOM-32",
            "package": "Module", "mpn": "ESP32-WROOM-32",
            "pins": [
                {"number": "3V3", "name": "3V3", "net": "VCC_3V3"},
                {"number": "GND", "name": "GND", "net": "GND"},
                {"number": "GPIO2", "name": "GPIO2", "net": "LED_CTRL"},
            ],
            "properties": {"voltage_rating": "3.3V"},
        },
        {
            "ref": "R1", "type": "resistor", "value": "330",
            "package": "0805", "mpn": None,
            "pins": [
                {"number": "1", "name": "1", "net": "LED_CTRL"},
                {"number": "2", "name": "2", "net": "LED_NET"},
            ],
            "properties": {"tolerance": "5%", "power": "0.125W"},
        },
        {
            "ref": "LED1", "type": "led", "value": "Red",
            "package": "0805", "mpn": None,
            "pins": [
                {"number": "A", "name": "A", "net": "LED_NET"},
                {"number": "K", "name": "K", "net": "GND"},
            ],
            "properties": {},
        },
        {
            "ref": "C1", "type": "capacitor", "value": "100nF",
            "package": "0603", "mpn": None,
            "pins": [
                {"number": "1", "name": "1", "net": "VCC_3V3"},
                {"number": "2", "name": "2", "net": "GND"},
            ],
            "properties": {"voltage_rating": "16V"},
        },
    ],
    "nets": [
        {"name": "VCC_3V3", "pins": ["U1.3V3", "C1.1"], "class": "power"},
        {"name": "GND", "pins": ["U1.GND", "LED1.K", "C1.2"], "class": "ground"},
        {"name": "LED_CTRL", "pins": ["U1.GPIO2", "R1.1"], "class": "signal"},
        {"name": "LED_NET", "pins": ["R1.2", "LED1.A"], "class": "signal"},
    ],
}


def deepcopy(x):
    return json.loads(json.dumps(x))


class ValidateTests(unittest.TestCase):
    """The validator port must accept the base netlist and catch bad output."""

    def test_base_netlist_is_valid(self):
        self.assertTrue(is_valid(BASE_NETLIST))
        self.assertEqual(validate_netlist(BASE_NETLIST), [])

    def test_rejects_hanging_pin(self):
        bad = deepcopy(BASE_NETLIST)
        bad["components"][1]["pins"][0]["net"] = "NO_SUCH_NET"
        self.assertFalse(is_valid(bad))
        rules = {v["rule"] for v in validate_netlist(bad)}
        self.assertIn("PIN_HANGS", rules)

    def test_rejects_duplicate_ref(self):
        bad = deepcopy(BASE_NETLIST)
        bad["components"].append(deepcopy(bad["components"][1]))  # duplicate R1
        rules = {v["rule"] for v in validate_netlist(bad)}
        self.assertIn("DUPLICATE_REF", rules)

    def test_rejects_missing_power_net(self):
        bad = deepcopy(BASE_NETLIST)
        bad["nets"] = [n for n in bad["nets"] if n["name"] != "VCC_3V3"]
        # Rewire the two pins that referenced VCC_3V3 so no PIN_HANGS noise.
        for c in bad["components"]:
            for p in c["pins"]:
                if p["net"] == "VCC_3V3":
                    p["net"] = "GND"
        rules = {v["rule"] for v in validate_netlist(bad)}
        self.assertIn("NO_POWER", rules)


class DiffRenderTests(unittest.TestCase):
    """The core 5 cases: validate -> diff -> render, no LLM involved."""

    def _render(self, new):
        self.assertEqual(validate_netlist(new), [], "edited netlist must validate")
        return render_diff(BASE_NETLIST, new)

    def test_01_change_value(self):
        new = deepcopy(BASE_NETLIST)
        new["components"][1]["value"] = "1k"  # R1: 330 -> 1k
        diff = self._render(new)

        self.assertEqual(diff["added"], [])
        self.assertEqual(diff["removed"], [])
        mods = {m["field"]: m for m in diff["modified"]}
        self.assertIn("value", mods)
        self.assertEqual(mods["value"]["ref"], "R1")
        self.assertEqual(mods["value"]["old"], "330")
        self.assertEqual(mods["value"]["new"], "1k")
        self.assertEqual(count_changes(diff), {"added": 0, "removed": 0, "modified": 1})

    def test_02_add_capacitor(self):
        new = deepcopy(BASE_NETLIST)
        new["components"].append({
            "ref": "C2", "type": "capacitor", "value": "10uF",
            "package": "0603", "mpn": None,
            "pins": [
                {"number": "1", "name": "1", "net": "VCC_3V3"},
                {"number": "2", "name": "2", "net": "GND"},
            ],
            "properties": {"voltage_rating": "10V"},
        })
        new["nets"][0]["pins"].append("C2.1")   # VCC_3V3
        new["nets"][1]["pins"].append("C2.2")   # GND
        diff = self._render(new)

        self.assertEqual(len(diff["added"]), 1)
        self.assertEqual(diff["added"][0]["ref"], "C2")
        self.assertEqual(diff["added"][0]["kind"], "component")
        self.assertEqual(diff["added"][0]["value"], "10uF")
        # The two net pin-list edits count as modifications (pins field on nets).
        pin_mods = [m for m in diff["modified"] if m["field"] == "pins" and m["kind"] == "net"]
        self.assertEqual(len(pin_mods), 2)

    def test_03_remove_resistor(self):
        new = deepcopy(BASE_NETLIST)
        new["components"] = [c for c in new["components"] if c["ref"] != "R1"]
        # LED1.A was on LED_NET with R1; move it to LED_CTRL so no pin hangs.
        for c in new["components"]:
            if c["ref"] == "LED1":
                for p in c["pins"]:
                    if p["name"] == "A":
                        p["net"] = "LED_CTRL"
        new["nets"] = [
            {
                "name": n["name"],
                "pins": [rp for rp in n["pins"] if not rp.startswith("R1.")],
                "class": n["class"],
            }
            for n in new["nets"]
        ]
        # LED_NET is now empty (was only R1.2/LED1.A) -> drop it.
        new["nets"] = [n for n in new["nets"] if n["name"] != "LED_NET"]
        # Stitch LED1.A into LED_CTRL.
        for n in new["nets"]:
            if n["name"] == "LED_CTRL":
                n["pins"].append("LED1.A")
        new["nets"] = [n for n in new["nets"] if n["pins"]]
        diff = self._render(new)

        removed_comps = {r["ref"] for r in diff["removed"] if r["kind"] == "component"}
        removed_nets = {r["ref"] for r in diff["removed"] if r["kind"] == "net"}
        self.assertEqual(removed_comps, {"R1"})
        self.assertEqual(removed_nets, {"LED_NET"})
        self.assertEqual(diff["added"], [])

    def test_04_rename_net(self):
        new = deepcopy(BASE_NETLIST)
        for n in new["nets"]:
            if n["name"] == "LED_NET":
                n["name"] = "LED_ANODE"
        for c in new["components"]:
            for p in c["pins"]:
                if p["net"] == "LED_NET":
                    p["net"] = "LED_ANODE"
        diff = self._render(new)

        # Rename must NOT appear as remove+add; it must be a modified "name".
        self.assertEqual(diff["added"], [])
        self.assertEqual(diff["removed"], [])
        name_mod = next(m for m in diff["modified"] if m["field"] == "name")
        self.assertEqual(name_mod["kind"], "net")
        self.assertEqual((name_mod["old"], name_mod["new"]), ("LED_NET", "LED_ANODE"))

    def test_05_change_package(self):
        new = deepcopy(BASE_NETLIST)
        new["components"][1]["package"] = "0603"  # R1: 0805 -> 0603
        diff = self._render(new)

        mods = {m["field"]: m for m in diff["modified"]}
        self.assertIn("package", mods)
        self.assertEqual(mods["package"]["ref"], "R1")
        self.assertEqual(mods["package"]["old"], "0805")
        self.assertEqual(mods["package"]["new"], "0603")

    def test_pin_net_change_surfaces_as_pin_field(self):
        """Bonus: rewiring one pin reports pins.<name>.net, not a whole-component blob."""
        new = deepcopy(BASE_NETLIST)
        # LED1.K moves from GND to LED_CTRL (silly but valid wiring exercise).
        for c in new["components"]:
            if c["ref"] == "LED1":
                for p in c["pins"]:
                    if p["name"] == "K":
                        p["net"] = "LED_CTRL"
        new["nets"][2]["pins"].append("LED1.K")  # LED_CTRL
        new["nets"][1]["pins"].remove("LED1.K")  # GND
        diff = self._render(new)

        fields = {m["field"] for m in diff["modified"]}
        self.assertIn("pins.K.net", fields)
        self.assertIn("pins", fields)  # both nets' pin lists also changed


if __name__ == "__main__":
    unittest.main(verbosity=2)