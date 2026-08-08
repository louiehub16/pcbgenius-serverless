"""test_steer.py — D10 steerable agent, scripted 3-steer session (feature #16).

NO network, NO LLM, NO npm/docker/git. A deterministic `edit_fn` is injected so
the goal->act->confirm->refine loop is exercised end-to-end on pure plumbing:

  Steer 1  add a 10uF decoupling capacitor (C2)          -> +1 component
  Steer 2  rename net LED_NET -> LED_ANODE               -> 1 net rename
  Steer 3  change R1 value 330 -> 1k                      -> 1 field modified
  Undo     rewind turn 3 (and then turn 2) — netlist returns to an earlier state

It also verifies:
  * goal propagation into the act prompt,
  * surgical diff shape reusing pcbgenius-iterate's diff engine,
  * state persistence (save/load round-trip preserves every confirmed change),
  * the refine path: an invalid proposal is rejected and feedback is folded in.

Run:  python test_steer.py        (or python -m unittest test_steer -v)
"""

import json
import os
import tempfile
import unittest
from copy import deepcopy

from state import SteerState, SteerTurn, SteerError
from agent import SteerAgent

# ---------------------------------------------------------------------------
# Shared fixture — the Wave-A sample netlist (mirrors pcbgenius-iterate's).
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


def clone(x):
    return json.loads(json.dumps(x))


def _add_c2(net):
    net = clone(net)
    net["components"].append({
        "ref": "C2", "type": "capacitor", "value": "10uF",
        "package": "0603", "mpn": None,
        "pins": [
            {"number": "1", "name": "1", "net": "VCC_3V3"},
            {"number": "2", "name": "2", "net": "GND"},
        ],
        "properties": {"voltage_rating": "10V"},
    })
    for n in net["nets"]:
        if n["name"] == "VCC_3V3":
            n["pins"].append("C2.1")
        elif n["name"] == "GND":
            n["pins"].append("C2.2")
    return net


def _rename_led_net(net):
    net = clone(net)
    for n in net["nets"]:
        if n["name"] == "LED_NET":
            n["name"] = "LED_ANODE"
    for c in net["components"]:
        for p in c["pins"]:
            if p["net"] == "LED_NET":
                p["net"] = "LED_ANODE"
    return net


def _set_r1_value(net):
    net = clone(net)
    for c in net["components"]:
        if c["ref"] == "R1":
            c["value"] = "1k"
    return net


def scripted_edit_fn(netlist: dict, prompt: str) -> dict:
    """Deterministic stand-in for the LLM edit engine.

    Reads the [REQUEST] line out of the goal-aware prompt the loop built.
    `BAD[...]` requests intentionally produce an INVALID netlist so the refine
    path can be exercised without any network call.
    """
    request = prompt.split("[REQUEST]", 1)[-1].split("[REFINEMENT FEEDBACK]", 1)[0].strip()
    if "BAD" in request and "[REFINEMENT FEEDBACK]" not in prompt:
        # Only misbehave on the FIRST attempt. Once the loop folds refinement
        # feedback back in, the (simulated) model corrects itself.
        bad = clone(netlist)
        for c in bad["components"]:
            if c["ref"] == "R1":
                for p in c["pins"]:
                    p["net"] = "NO_SUCH_NET"   # guarantees a PIN_HANGS violation
        return bad
    if "add" in request and "decoupling capacitor" in request:
        return _add_c2(netlist)
    if "rename" in request:
        return _rename_led_net(netlist)
    if "value" in request:
        return _set_r1_value(netlist)
    # fallback: echo unchanged (a valid but empty edit)
    return clone(netlist)


class SteerStateTests(unittest.TestCase):
    def test_persist_round_trip(self):
        state = SteerState(goal="steady 3.3V", netlist=BASE_NETLIST)
        turn = state.add_pending(SteerTurn(
            request="add cap", before=BASE_NETLIST, after=_add_c2(BASE_NETLIST),
            diff={"added": [], "removed": [], "modified": []},
        ))
        state.commit_pending(turn)
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "session.json")
            state.save(path)
            loaded = SteerState.load(path)
        self.assertEqual(loaded.goal, "steady 3.3V")
        self.assertEqual(loaded.turn_count, 1)
        self.assertEqual(loaded.history[0].status, "applied")
        # commit restored refs and undo point survive the round trip
        self.assertEqual(loaded.next_id, 2)
        self.assertEqual(len(loaded.undo_stack), 1)
        self.assertEqual(loaded.netlist["components"][-1]["ref"], "C2")

    def test_undo_when_empty_returns_none(self):
        s = SteerState(netlist=BASE_NETLIST)
        self.assertFalse(s.can_undo())
        self.assertIsNone(s.undo())

    def test_commit_advances_netlist(self):
        s = SteerState(netlist=BASE_NETLIST)
        turn = s.add_pending(SteerTurn(
            request="x", before=BASE_NETLIST, after=_add_c2(BASE_NETLIST),
            diff={"added": [], "removed": [], "modified": []},
        ))
        s.commit_pending(turn)
        refs = [c["ref"] for c in s.netlist["components"]]
        self.assertIn("C2", refs)


class SteerAgentTests(unittest.TestCase):
    def setUp(self):
        self.agent = SteerAgent(
            state=SteerState(netlist=clone(BASE_NETLIST)),
            edit_fn=scripted_edit_fn,
        )

    # -- GOAL -> ACT -> CONFIRM ---------------------------------------------
    def test_goal_is_injected_into_act_prompt(self):
        self.agent.set_goal("make the blinker robust")
        captured = {}

        def spy(netlist, prompt):
            captured["prompt"] = prompt
            return clone(netlist)

        self.agent.edit_fn = spy
        self.agent.run("add a decoupling capacitor")
        self.assertIn("make the blinker robust", captured["prompt"])
        self.assertIn("[REQUEST] add a decoupling capacitor", captured["prompt"])

    def test_steer1_add_capacitor(self):
        self.agent.run("add a decoupling capacitor")
        refs = [c["ref"] for c in self.agent.state.netlist["components"]]
        self.assertIn("C2", refs)
        turn = self.agent.state.history[-1]
        self.assertEqual(turn.status, "applied")
        added = {a["ref"] for a in turn.diff["added"]}
        self.assertIn("C2", added)
        self.assertEqual(turn.counts["added"], 1)  # surgical: one add, no removals
        self.assertEqual(turn.counts["removed"], 0)

    def test_steer2_rename_net(self):
        self.agent.run("rename LED_NET to LED_ANODE")
        names = {n["name"] for n in self.agent.state.netlist["nets"]}
        self.assertIn("LED_ANODE", names)
        self.assertNotIn("LED_NET", names)
        turn = self.agent.state.history[-1]
        # rename must be a modified "name", NOT an add/remove pair
        self.assertEqual(turn.diff["added"], [])
        self.assertEqual(turn.diff["removed"], [])
        name_mod = next(m for m in turn.diff["modified"] if m["field"] == "name")
        self.assertEqual((name_mod["old"], name_mod["new"]), ("LED_NET", "LED_ANODE"))
        # every pin that referenced the old name moved consistently
        for c in self.agent.state.netlist["components"]:
            for p in c["pins"]:
                self.assertNotEqual(p["net"], "LED_NET")

    def test_steer3_change_value(self):
        self.agent.run("set R1 value to 1k")
        r1 = next(c for c in self.agent.state.netlist["components"] if c["ref"] == "R1")
        self.assertEqual(r1["value"], "1k")
        turn = self.agent.state.history[-1]
        mods = {m["field"] for m in turn.diff["modified"]}
        self.assertIn("value", mods)

    def test_full_3_steer_session_evolves_correctly(self):
        self.agent.set_goal("stabilise LED supply")
        self.agent.run("add a decoupling capacitor")
        self.agent.run("rename LED_NET to LED_ANODE")
        self.agent.run("set R1 value to 1k")

        net = self.agent.state.netlist
        refs = [c["ref"] for c in net["components"]]
        self.assertIn("C2", refs)                       # steer 1 persisted
        self.assertIn("LED_ANODE", {n["name"] for n in net["nets"]})  # steer 2
        r1 = next(c for c in net["components"] if c["ref"] == "R1")
        self.assertEqual(r1["value"], "1k")             # steer 3
        self.assertEqual(self.agent.state.turn_count, 3)
        self.assertEqual(len(self.agent.state.applied_turns), 3)
        # everything still validates (the diff engine's contract holds)
        from validate import is_valid
        self.assertTrue(is_valid(net))

    # -- UNDO -----------------------------------------------------------------
    def test_undo_rewinds_step_by_step(self):
        self.agent.run("add a decoupling capacitor")
        self.agent.run("rename LED_NET to LED_ANODE")
        self.agent.run("set R1 value to 1k")
        self.assertEqual(len(self.agent.state.applied_turns), 3)

        # Undo steer 3 (R1 value)
        undone3 = self.agent.undo()
        self.assertEqual(undone3.request, "set R1 value to 1k")
        self.assertEqual(undone3.status, "undone")
        r1 = next(c for c in self.agent.state.netlist["components"] if c["ref"] == "R1")
        self.assertEqual(r1["value"], "330")            # value reverted
        refs = [c["ref"] for c in self.agent.state.netlist["components"]]
        self.assertIn("C2", refs)                        # earlier steers intact
        self.assertIn("LED_ANODE", {n["name"] for n in self.agent.state.netlist["nets"]})

        # Undo steer 2 (net rename)
        self.agent.undo()
        names = {n["name"] for n in self.agent.state.netlist["nets"]}
        self.assertIn("LED_NET", names)                  # rename reverted
        self.assertNotIn("LED_ANODE", names)
        # UNDO must keep pins consistent with the restored net (no hanging refs)
        from validate import is_valid
        self.assertTrue(is_valid(self.agent.state.netlist))

        # Undo steer 1 -> back to baseline
        undone1 = self.agent.undo()
        self.assertEqual(undone1.request, "add a decoupling capacitor")
        refs = [c["ref"] for c in self.agent.state.netlist["components"]]
        self.assertNotIn("C2", refs)
        self.assertFalse(self.agent.state.can_undo())

    # -- REFINE ----------------------------------------------------------------
    def test_invalid_proposal_rejects_and_feedback_drives_refinement(self):
        # A scripted request that yields a contract-violating netlist on the
        # FIRST attempt, then a clean one once refinement feedback is present.
        first = self.agent.propose("BAD add a decoupling capacitor")
        self.assertEqual(first.status, "proposed")
        self.assertNotEqual(first.feedback, [])         # validator findings captured
        self.assertTrue(any(v["rule"] == "PIN_HANGS" for v in first.feedback))
        # netlist must NOT have moved while the bad proposal was on the table
        self.assertNotIn("C2", [c["ref"] for c in self.agent.state.netlist["components"]])

        # folding the violations into the next prompt is observable
        prompt = self.agent._build_request("add a decoupling capacitor", first.feedback)
        self.assertIn("[REFINEMENT FEEDBACK]", prompt)
        self.assertIn("PIN_HANGS", prompt)

        # Now REFINE with feedback -> a fresh, valid proposal is made.
        refined = self.agent.refine(first, feedback=first.feedback)
        self.assertEqual(refined.status, "proposed")
        self.assertEqual(refined.feedback, [])          # new candidate validates
        self.assertNotEqual(refined.id, first.id)       # a NEW turn, not a retry
        # the refined candidate actually contains the requested new part
        self.assertIn("C2", [c["ref"] for c in refined.after["components"]])
        # confirm the refined proposal
        self.agent.confirm(refined, approve=True)
        self.assertEqual(refined.status, "applied")
        self.assertEqual(self.agent.state.history[0].status, "rejected")
        self.assertEqual(self.agent.state.history[1].status, "applied")

    def test_run_rejects_unconfirmed_invalid(self):
        # `run(..., auto_approve=True)` must NOT silently commit an invalid proposal.
        with self.assertRaises(SteerError):
            self.agent.run("BAD retry invalid proposal")


if __name__ == "__main__":
    unittest.main(verbosity=2)