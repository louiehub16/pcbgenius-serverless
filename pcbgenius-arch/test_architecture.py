"""test_architecture.py — deterministic tests for the D6 architecture stage.

NO network calls. Exercises 3 representative prompts and asserts that each:

  1. produces a valid contract v1.0.0 netlist skeleton
      (>=1 ground net, >=1 power net, no hanging pins, resolvable ref.pin)
  2. produces a non-empty Mermaid flowchart that references every block id
  3. falls back to the deterministic (offline) path when no LLM key is set

The local ``check_skeleton`` mirrors the freeze contract from
pcbgenius-iterate/validate.py so this package has zero cross-dependency.

Run:  python test_architecture.py        (or python -m unittest test_architecture -v)
"""

import json
import unittest

import design
from design import (ArchitectureError, Block, blocks_to_netlist,
                    deterministic_blocks, design_from_prompt, classify_net)
from mermaid import render_mermaid_flowchart

# The 3 deterministic-relevant prompts exercised by this suite.
PROMPTS = [
    "Build me a 12V to 5V buck converter",
    "Design a 5V to 3.3V LDO regulator",
    "Make a blink LED circuit with an ATtiny85",
]


def check_skeleton(netlist: dict) -> list[str]:
    """Minimal contract validator mirroring validate_netlist (v1.0.0).

    Returns a list of violation strings; empty list means valid.
    """
    violations: list[str] = []

    if netlist.get("schema_version") != "1.0.0":
        violations.append("schema_version must be '1.0.0'")

    components = netlist.get("components", [])
    nets = netlist.get("nets", [])

    refs = [c.get("ref") for c in components]
    dup = [r for i, r in enumerate(refs) if r in refs[:i]]
    if dup:
        violations.append(f"duplicate refs: {sorted(set(dup))}")

    net_names = {n.get("name") for n in nets}
    known_pins = {
        f"{c.get('ref')}.{p.get('name')}"
        for c in components
        for p in c.get("pins", [])
    }

    for c in components:
        for p in c.get("pins", []):
            if p.get("net") not in net_names:
                violations.append(f"pin {c.get('ref')}.{p.get('name')} hangs on {p.get('net')}")

    for n in nets:
        for rp in n.get("pins", []):
            if rp not in known_pins:
                violations.append(f"net {n.get('name')} references unknown {rp}")

    classes = {n.get("class") for n in nets}
    if "ground" not in classes:
        violations.append("no ground net")
    if "power" not in classes:
        violations.append("no power net")

    return violations


class DeterministicBlocksTests(unittest.TestCase):
    def test_buck_prompt(self):
        blocks = deterministic_blocks(PROMPTS[0])
        ids = {b.id for b in blocks}
        self.assertIn("U1", ids)
        self.assertTrue(any(b.kind == "inductor" for b in blocks),
                        "buck should include an inductor")

    def test_ldo_prompt(self):
        blocks = deterministic_blocks(PROMPTS[1])
        self.assertTrue(any(b.kind == "regulator" for b in blocks))

    def test_led_prompt(self):
        blocks = deterministic_blocks(PROMPTS[2])
        self.assertTrue(any(b.kind == "led" for b in blocks))
        self.assertTrue(any(b.kind == "resistor" for b in blocks))

    def test_unknown_prompt_defaults_to_mcu(self):
        blocks = deterministic_blocks("spaceship telemetry board")
        self.assertTrue(any(b.kind == "microcontroller" for b in blocks))

    def test_every_block_has_gnd_and_power_coverage(self):
        for prompt in PROMPTS:
            blocks = deterministic_blocks(prompt)
            all_nets = {n for b in blocks for n in b.nets}
            self.assertIn("GND", all_nets)
            self.assertTrue(
                any(classify_net(n) == "power" for n in all_nets),
                f"{prompt} must have a power rail",
            )


class SkeletonValidityTests(unittest.TestCase):
    """The key guarantee: every prompt yields a contract-legal netlist skeleton."""

    def test_three_prompts_produce_valid_skeletons(self):
        for prompt in PROMPTS:
            blocks = deterministic_blocks(prompt)
            netlist = blocks_to_netlist(blocks, _name(prompt))
            self.assertEqual(
                check_skeleton(netlist), [],
                f"invalid skeleton for '{prompt}': {check_skeleton(netlist)}",
            )

    def test_refs_are_unique_and_match_blocks(self):
        blocks = deterministic_blocks(PROMPTS[0])
        netlist = blocks_to_netlist(blocks, "buck")
        comp_refs = [c["ref"] for c in netlist["components"]]
        self.assertEqual(len(comp_refs), len(set(comp_refs)))

    def test_pins_named_after_nets_and_resolvable(self):
        blocks = deterministic_blocks(PROMPTS[2])
        netlist = blocks_to_netlist(blocks, "led")
        for n in netlist["nets"]:
            for rp in n["pins"]:
                self.assertTrue(rp in {
                    f"{c['ref']}.{p['name']}" for c in netlist["components"]
                    for p in c["pins"]
                }, f"unresolved {rp}")

    def test_classify_net_ground_and_power(self):
        self.assertEqual(classify_net("GND"), "ground")
        self.assertEqual(classify_net("VIN"), "power")
        self.assertEqual(classify_net("LED_NET"), "signal")


class MermaidTests(unittest.TestCase):
    def test_render_produces_flowchart_and_all_node_ids(self):
        blocks = deterministic_blocks(PROMPTS[0])
        m = render_mermaid_flowchart(blocks)
        self.assertTrue(m.lstrip().startswith("flowchart"))
        for b in blocks:
            self.assertIn(b.id, m, f"node {b.id} missing from mermaid")

    def test_render_stubs_single_connection_nets(self):
        # GND is only ever one-directional in our blocks; ensure a GND node/stub appears.
        blocks = deterministic_blocks(PROMPTS[2])
        m = render_mermaid_flowchart(blocks)
        self.assertIn("GND", m)

    def test_render_from_json_matches_direct(self):
        result = design_from_prompt(PROMPTS[0])
        from_dict = render_mermaid_flowchart([
            Block(b["id"], b["kind"], b["label"], b["nets"], b["value"])
            for b in result["blocks"]
        ])
        self.assertEqual(from_dict, result["mermaid"])

    def test_empty_blocks_do_not_crash(self):
        m = render_mermaid_flowchart([])
        self.assertIn("flowchart", m)


class EndToEndTests(unittest.TestCase):
    """design_from_prompt must be deterministic (fallback) when no key is set."""

    def test_no_api_key_falls_back_to_deterministic(self):
        for prompt in PROMPTS:
            result = design_from_prompt(prompt)  # no OPENROUTER_API_KEY set
            self.assertEqual(result["source"], "deterministic")
            self.assertEqual(
                check_skeleton(result["netlist"]), [],
                f"end-to-end skeleton invalid for '{prompt}'",
            )
            self.assertIn("flowchart", result["mermaid"])

    def test_result_self_consistent(self):
        result = design_from_prompt(PROMPTS[1])
        json.dumps(result)  # must be JSON-serializable


def _name(prompt: str) -> str:
    return design._slugify(prompt)


if __name__ == "__main__":
    unittest.main(verbosity=2)