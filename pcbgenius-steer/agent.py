"""agent.py — D10 steerable agent (feature #16).

A steerable agent is a conversation loop, not a one-shot promptbox:

    goal -> act -> confirm -> refine -> confirm ...

  * `goal`   : the long-horizon intent the user set ("make a 5V->3.3V LDO").
  * `act`    : propose a NETLIST CHANGE. The edit engine is pluggable; the
               default reuses pcbgenius-iterate's `iterate_netlist` (an LLM via
               OpenRouter with validate-and-retry). For tests / offline use you
               inject a deterministic `edit_fn`.
  * `confirm`: the change is validated and rendered into a surgical diff for
               the user to approve or reject.
  * `refine` : on rejection the rejection feedback is fed back and a NEW,
               narrower candidate is produced — never a blind retry.

Diffs are produced by the SHARED pcbgenius-iterate diff engine
(diff_render.render_diff / count_changes), so this component stays surgical:
field-level modifications, real additions/removals, net renames, and pin-netting
changes — identical output to the rest of PCBGenius Wave A.

No npm/docker/git. Stdlib + the sibling pcbgenius-iterate package only.
"""

from __future__ import annotations

import json
import os
import sys
from copy import deepcopy
from typing import Any, Callable, Optional

from state import SteerState, SteerTurn, SteerError

# ---------------------------------------------------------------------------
# Reuse the pcbgenius-iterate diff engine + validator from the sibling workspace.
# The two packages both live directly under the _orch workspace root.
# ---------------------------------------------------------------------------
_ITERATE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "pcbgenius-iterate"
)
if _ITERATE_DIR not in sys.path:
    sys.path.insert(0, _ITERATE_DIR)

from diff_render import render_diff, count_changes  # noqa: E402
from validate import validate_netlist               # noqa: E402


def default_edit_fn(netlist: dict, request: str, model: Optional[str] = None,
                    api_key: Optional[str] = None) -> dict:
    """Default `edit_fn`: delegate to pcbgenius-iterate's LLM iteration engine.

    `iterate_netlist` already validates its own output against the contract and
    returns a clean `{"netlist": ...}`. We unwrap that and raise a SteerError on
    failure so the loop can turn it into a refinement instead of dying.
    """
    from engine import iterate_netlist, IterationError
    try:
        result = iterate_netlist(
            deepcopy(netlist), request, model=model, api_key=api_key
        )
    except IterationError as e:
        raise SteerError(f"edit engine rejected: {e}") from e
    return deepcopy(result["netlist"])


EditFn = Callable[[dict, str], dict]


class SteerAgent:
    """Conversation loop for steering a netlist toward a goal with surgical diffs."""

    def __init__(
        self,
        state: Optional[SteerState] = None,
        edit_fn: Optional[EditFn] = None,
        model: Optional[str] = None,
        api_key: Optional[str] = None,
    ) -> None:
        self.state = state or SteerState()
        self.edit_fn: EditFn = edit_fn or (lambda nl, req: default_edit_fn(
            nl, req, model=model, api_key=api_key))
        self.model = model
        self.api_key = api_key

    # -- goal ---------------------------------------------------------------
    def set_goal(self, goal: str) -> str:
        """Set the high-level intent steering all subsequent acts."""
        self.state.goal = goal
        return goal

    # -- the core conversation loop -----------------------------------------
    def propose(self, request: str, feedback: Optional[list] = None) -> SteerTurn:
        """ACT: turn a request (+ optional refinement feedback) into a candidate.

        Builds a goal-aware prompt, runs the edit engine, validates the result,
        and renders the surgical diff. The candidate is staged as a *pending*
        turn — the netlist does NOT change until `confirm(True)`.
        """
        if self.state.netlist is None:
            raise SteerError("No netlist yet. Assign one via state.netlist before steering.")

        prompt = self._build_request(request, feedback)
        try:
            candidate = self.edit_fn(deepcopy(self.state.netlist), prompt)
        except SteerError:
            raise
        except Exception as e:  # defensive: any engine failure -> steerable error
            raise SteerError(f"edit engine failed: {e}") from e

        violations = validate_netlist(candidate)
        if violations:
            # Honest: never confirm something that violates the contract. Stage a
            # turn flagged for refinement so the caller can feed these back.
            turn = SteerTurn(
                request=request, before=self.state.netlist, after=candidate,
                diff=render_diff(self.state.netlist, candidate),
                goal=self.state.goal, status="proposed",
                feedback=violations,
            )
            self.state.add_pending(turn)
            return turn

        diff = render_diff(self.state.netlist, candidate)
        turn = SteerTurn(
            request=request, before=self.state.netlist, after=candidate, diff=diff,
            goal=self.state.goal, status="proposed",
        )
        self.state.add_pending(turn)
        return turn

    def confirm(self, turn: SteerTurn, approve: bool = True,
                feedback: Optional[list] = None, note: str = "") -> SteerTurn:
        """CONFIRM: accept (commit into the netlist) or reject (-> refine).

        approve=True  commits: undo point recorded, netlist advances.
        approve=False rejects: netlist untouched; `feedback` is stored so the
                     caller can call `propose` again with it (refine).
        """
        if approve:
            return self.state.commit_pending(turn, note=note)
        return self.state.reject_pending(turn, feedback=feedback, note=note)

    def refine(self, turn: SteerTurn, feedback: Optional[list] = None,
               note: str = "") -> SteerTurn:
        """REFINE: reject `turn`, then act again with the feedback folded in.

        Returns a brand-new pending proposal that the user can confirm next.
        This is the loop's self-correcting step — a narrower, feedback-aware edit,
        never a blind retry of the same prompt.
        """
        self.state.reject_pending(turn, feedback=feedback, note=note)
        request = turn.request
        fb = list(feedback) if feedback is not None else turn.feedback
        return self.propose(request, feedback=fb)

    # -- convenience: one-shot run ------------------------------------------
    def run(self, request: str, auto_approve: bool = True) -> SteerTurn:
        """ACT + CONFIRM in one call.

        With auto_approve=True the proposal is committed immediately (useful for
        scripts/tests and non-interactive paths). With auto_approve=False it
        returns the pending turn for manual confirmation, raising if the proposal
        failed validation (so the caller can refine).
        """
        turn = self.propose(request)
        pending_problems = turn.feedback
        if pending_problems:
            raise SteerError(
                f"proposal failed validation: "
                + "; ".join(f"{v.get('rule')} {v.get('message')}" for v in pending_problems)
            )
        if auto_approve:
            return self.confirm(turn, approve=True)
        return turn

    # -- undo ----------------------------------------------------------------
    def undo(self) -> Optional[SteerTurn]:
        """Rewind the last confirmed change and mark it undone in history."""
        return self.state.undo()

    # -- helpers -------------------------------------------------------------
    def _build_request(self, request: str, feedback: Optional[list]) -> str:
        parts = []
        if self.state.goal:
            parts.append(f"[GOAL] {self.state.goal.strip()}")
        parts.append(f"[REQUEST] {request.strip()}")
        prompt = "\n".join(parts)
        if feedback:
            lines = ["[REFINEMENT FEEDBACK]", "The previous attempt was rejected because:"]
            lines += [f"  - {v.get('rule')}: {v.get('message')}" for v in feedback]
            prompt += "\n" + "\n".join(lines)
        return prompt

    # -- persistence passthrough ---------------------------------------------
    def save(self, filepath: Optional[str] = None) -> str:
        return self.state.save(filepath)

    @classmethod
    def load(cls, filepath: str, edit_fn: Optional[EditFn] = None) -> "SteerAgent":
        return cls(state=SteerState.load(filepath), edit_fn=edit_fn)

    def summary(self) -> dict:
        """Compact snapshot used for logging / UI state."""
        return {
            "goal": self.state.goal,
            "turn_count": self.state.turn_count,
            "applied": len(self.state.applied_turns),
            "can_undo": self.state.can_undo(),
            "counts": None if self.state.netlist is None else count_changes(
                render_diff({}, self.state.netlist)),
        }


if __name__ == "__main__":
    # CLI smoke harness: `python agent.py <request> [netlist.json]`
    # Requires OPENROUTER_API_KEY. Purely illustrative.
    import argparse

    ap = argparse.ArgumentParser(description="PCBGenius steerable agent (CLI smoke)")
    ap.add_argument("request", help="natural-language design change")
    ap.add_argument("--netlist", default=None, help="starting netlist JSON (required)")
    ap.add_argument("--goal", default=None, help="set a steering goal first")
    ap.add_argument("--out", default="steered_netlist.json")
    args = ap.parse_args()

    with open(args.netlist, "r", encoding="utf-8") as fh:
        start = json.load(fh)

    agent = SteerAgent(state=SteerState(netlist=start))
    if args.goal:
        agent.set_goal(args.goal)
    turn = agent.run(args.request, auto_approve=True)
    print(json.dumps(turn.diff, indent=2))
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(turn.after, fh, indent=2)
    print(f"\napplied id={turn.id} counts={turn.counts} -> wrote {args.out}",
          file=sys.stderr)