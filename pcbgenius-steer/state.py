"""state.py — D10 steerable-agent session state (feature #16).

A steerable session is a single evolving design conversation:
    goal -> act -> confirm -> refine

`SteerState` holds everything that defines "where the design is right now":
  * the high-level `goal` the user is steering toward,
  * the current netlist,
  * an ordered `history` of every confirmed / rejected turn,
  * an `undo_stack` of prior netlists so you can rewind surgical changes.

State is plain dict-based (JSON-serialisable) so a session can be persisted and
resumed. No I/O happens until you call `save`/`load` explicitly. Pure stdlib,
no API, no network.

The diff itself is NOT computed here — that is the job of the shared
pcbgenius-iterate diff engine (diff_render.py). This module only records the
inputs/outputs of a turn so the renderer can run later.
"""

from __future__ import annotations

import json
import os
import time
from copy import deepcopy
from typing import Any, Optional


class SteerError(RuntimeError):
    """Raised on invalid state transitions (undo when empty, confirm without propose)."""


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


class SteerTurn:
    """One goal->act->confirm->refine turn."""

    __slots__ = (
        "id", "ts", "goal", "request", "before", "after", "diff",
        "status", "feedback", "note",
    )

    def __init__(
        self,
        request: str,
        before: dict,
        after: dict,
        diff: dict,
        goal: Optional[str] = None,
        turn_id: Optional[int] = None,
        ts: Optional[str] = None,
        status: str = "proposed",
        feedback: Optional[list] = None,
        note: str = "",
    ) -> None:
        self.request = request
        self.before = deepcopy(before)          # netlist BEFORE this act
        self.after = deepcopy(after)            # netlist AFTER this act (candidate)
        self.diff = deepcopy(diff)              # render_diff(before, after)
        self.goal = goal
        self.id = turn_id
        self.ts = ts or _now()
        self.status = status                    # proposed|applied|rejected|undone
        self.feedback = deepcopy(feedback) if feedback else []
        self.note = note

    # -- convenience accessors ------------------------------------------------
    @property
    def counts(self) -> dict:
        added = len(self.diff.get("added", []))
        removed = len(self.diff.get("removed", []))
        modified = len(self.diff.get("modified", []))
        return {"added": added, "removed": removed, "modified": modified}

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "ts": self.ts,
            "goal": self.goal,
            "request": self.request,
            "before": self.before,
            "after": self.after,
            "diff": self.diff,
            "status": self.status,
            "feedback": self.feedback,
            "note": self.note,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "SteerTurn":
        return cls(
            request=d["request"], before=d["before"], after=d["after"],
            diff=d["diff"], goal=d.get("goal"), turn_id=d.get("id"), ts=d.get("ts"),
            status=d.get("status", "proposed"), feedback=d.get("feedback"),
            note=d.get("note", ""),
        )

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"<SteerTurn id={self.id} status={self.status} {self.request!r}>"


class SteerState:
    """Persistable session state for the steerable agent."""

    STATE_VERSION = "1.0.0"

    def __init__(
        self,
        goal: Optional[str] = None,
        netlist: Optional[dict] = None,
        history: Optional[list] = None,
        undo_stack: Optional[list] = None,
        filepath: Optional[str] = None,
        next_id: int = 1,
    ) -> None:
        self.goal = goal
        self.netlist = deepcopy(netlist) if netlist is not None else None
        self.history: list[SteerTurn] = history or []
        self.undo_stack: list[dict] = undo_stack or []  # netlists we can rewind to
        self.filepath = filepath
        self.next_id = next_id

    # -- history -------------------------------------------------------------
    @property
    def applied_turns(self) -> list[SteerTurn]:
        """Confirmed, still-in-effect turns (and undone ones are excluded)."""
        return [t for t in self.history if t.status == "applied"]

    @property
    def turn_count(self) -> int:
        return len(self.history)

    def _next_turn_id(self) -> int:
        turn_id = self.next_id
        self.next_id += 1
        return turn_id

    def add_pending(self, turn: SteerTurn) -> SteerTurn:
        """Register a proposed (unconfirmed) turn. Does NOT mutate the netlist."""
        turn.id = self._next_turn_id()
        turn.ts = _now()
        self.history.append(turn)
        return turn

    def commit_pending(self, turn: SteerTurn, note: str = "") -> SteerTurn:
        """Accept a proposed turn: record its undo point and adopt the new netlist."""
        if turn.status == "applied":
            return turn  # idempotent
        if turn not in self.history:
            self.history.append(turn)
        # Push the pre-act netlist so `undo()` can restore it.
        self.undo_stack.append(deepcopy(turn.before))
        turn.status = "applied"
        turn.note = note
        self.netlist = deepcopy(turn.after)
        return turn

    def reject_pending(self, turn: SteerTurn, feedback: Optional[list] = None,
                       note: str = "") -> SteerTurn:
        """Reject a proposed turn (drives a refinement) without touching the netlist."""
        turn.status = "rejected"
        if feedback:
            turn.feedback = deepcopy(feedback)
        if note:
            turn.note = note
        return turn

    # -- undo / rewind -------------------------------------------------------
    def undo(self) -> Optional[SteerTurn]:
        """Rewind the last confirmed turn. Returns the undone turn (None if empty)."""
        if not self.undo_stack:
            return None
        if not self.history:
            return None
        prev_netlist = self.undo_stack.pop()
        # Mark the most recent applied turn as undone for the audit trail.
        undone = None
        for t in reversed(self.history):
            if t.status == "applied":
                t.status = "undone"
                undone = t
                break
        self.netlist = deepcopy(prev_netlist)
        return undone

    def can_undo(self) -> bool:
        return bool(self.undo_stack)

    # -- persistence ----------------------------------------------------------
    def to_dict(self) -> dict:
        return {
            "state_version": self.STATE_VERSION,
            "goal": self.goal,
            "netlist": self.netlist,
            "history": [t.to_dict() for t in self.history],
            "undo_stack": self.undo_stack,
            "next_id": self.next_id,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "SteerState":
        return cls(
            goal=d.get("goal"),
            netlist=d.get("netlist"),
            history=[SteerTurn.from_dict(t) for t in d.get("history", [])],
            undo_stack=[deepcopy(n) for n in d.get("undo_stack", [])],
            next_id=d.get("next_id", 1),
        )

    def save(self, filepath: Optional[str] = None) -> str:
        """Persist to `filepath` (or the configured one). Returns the path written."""
        path = filepath or self.filepath
        if not path:
            raise SteerError("save() needs a filepath (pass one or set state.filepath).")
        path = os.path.abspath(path)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(self.to_dict(), fh, indent=2)
        self.filepath = path
        return path

    @classmethod
    def load(cls, filepath: str) -> "SteerState":
        """Resume a session previously persisted with `save()`."""
        with open(filepath, "r", encoding="utf-8") as fh:
            d = json.load(fh)
        state = cls.from_dict(d)
        state.filepath = os.path.abspath(filepath)
        return state