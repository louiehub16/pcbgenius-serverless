"""diff_render.py — turn old/new netlists into a frontend-ready diff.

Pure functions, no I/O, no API. The frontend IteratePanel consumes exactly the
shape produced by `render_diff`:

    {
      "added":    [ {kind, ref|name, ...summary fields} ... ],
      "removed":  [ {kind, ref|name, ...summary fields} ... ],
      "modified": [ {kind, ref, field, old, new} ... ],
    }

Rules:
  * Components are matched by `ref`.  Nets are matched by `name`.
  * A net whose name changed but whose pin set is identical is reported as a
    *rename* (modified field "name") rather than remove+add.
  * Pin-level net changes on a component surface as field "pins.<name>.net".
  * All values are JSON-serializable (strings/lists/dicts) for direct use in
    the TSX panel.
"""

from copy import deepcopy

COMPONENT_FIELDS = ("type", "value", "package", "mpn", "properties")


def _diff_scalars(old, new, kind, ref, out):
    """Report field-level changes for scalar component fields."""
    for field in COMPONENT_FIELDS:
        ov, nv = old.get(field), new.get(field)
        if ov != nv:
            out.append({"kind": kind, "ref": ref, "field": field, "old": ov, "new": nv})


def _diff_pins(old_pins, new_pins, kind, ref, out):
    """Compare pins by name; report added/removed pins and net changes."""
    old_by_name = {p.get("name"): p for p in old_pins}
    new_by_name = {p.get("name"): p for p in new_pins}

    for name in sorted(set(old_by_name) | set(new_by_name)):
        op = old_by_name.get(name)
        np_ = new_by_name.get(name)
        if op is None:
            out.append({"kind": kind, "ref": ref, "field": f"pins.{name}", "old": None, "new": np_})
        elif np_ is None:
            out.append({"kind": kind, "ref": ref, "field": f"pins.{name}", "old": op, "new": None})
        else:
            if op.get("net") != np_.get("net"):
                out.append({
                    "kind": kind, "ref": ref,
                    "field": f"pins.{name}.net",
                    "old": op.get("net"), "new": np_.get("net"),
                })
            if op.get("number") != np_.get("number"):
                out.append({
                    "kind": kind, "ref": ref,
                    "field": f"pins.{name}.number",
                    "old": op.get("number"), "new": np_.get("number"),
                })


def _component_summary(c: dict) -> dict:
    return {
        "kind": "component",
        "ref": c.get("ref"),
        "type": c.get("type"),
        "value": c.get("value"),
        "package": c.get("package"),
    }


def _net_summary(n: dict) -> dict:
    return {"kind": "net", "name": n.get("name"), "class": n.get("class")}


def _net_pin_set(n: dict):
    return sorted(n.get("pins", []))


def compute_diff(old: dict, new: dict) -> dict:
    """Raw structural diff. Returns {"added": [...], "removed": [...], "modified": [...]}.

    added/removed contain full item dicts (deep-copied); modified entries are
    {"kind", "ref", "field", "old", "new"}.
    """
    added, removed, modified = [], [], []

    old_comp = {c.get("ref"): c for c in old.get("components", [])}
    new_comp = {c.get("ref"): c for c in new.get("components", [])}

    for ref in sorted(set(old_comp) | set(new_comp)):
        oc, nc = old_comp.get(ref), new_comp.get(ref)
        if oc is None:
            added.append(deepcopy(nc))
        elif nc is None:
            removed.append(deepcopy(oc))
        else:
            _diff_scalars(oc, nc, "component", ref, modified)
            _diff_pins(oc.get("pins", []), nc.get("pins", []), "component", ref, modified)

    # Nets: match by name first, then by pin set (renames).
    old_nets = {n.get("name"): n for n in old.get("nets", [])}
    new_nets = {n.get("name"): n for n in new.get("nets", [])}

    matched_new = set()
    for name in sorted(old_nets):
        if name in new_nets:
            matched_new.add(name)

    # Rename detection: old net unmatched by name whose pin set equals an
    # unmatched new net -> report the name change, then diff the pair.
    renames = {}  # old_name -> new net dict
    for oname, on in sorted(old_nets.items()):
        if oname in new_nets:
            continue
        for nname, nn in new_nets.items():
            if nname in matched_new:
                continue
            if _net_pin_set(on) == _net_pin_set(nn):
                renames[oname] = nn
                matched_new.add(nname)
                break

    for oname, on in sorted(old_nets.items()):
        if oname in renames:
            nn = renames[oname]
            modified.append({
                "kind": "net", "ref": oname, "field": "name",
                "old": oname, "new": nn.get("name"),
            })
            if on.get("class") != nn.get("class"):
                modified.append({
                    "kind": "net", "ref": oname, "field": "class",
                    "old": on.get("class"), "new": nn.get("class"),
                })
        elif oname in new_nets:
            nn = new_nets[oname]
            if _net_pin_set(on) != _net_pin_set(nn):
                modified.append({
                    "kind": "net", "ref": oname, "field": "pins",
                    "old": on.get("pins"), "new": nn.get("pins"),
                })
            if on.get("class") != nn.get("class"):
                modified.append({
                    "kind": "net", "ref": oname, "field": "class",
                    "old": on.get("class"), "new": nn.get("class"),
                })
        else:
            removed.append(deepcopy(on))

    for nname, nn in sorted(new_nets.items()):
        if nname not in old_nets and nname not in {r.get("name") for r in renames.values()}:
            added.append(deepcopy(nn))

    return {"added": added, "removed": removed, "modified": modified}


def render_diff(old: dict, new: dict) -> dict:
    """Frontend-ready diff: summaries for added/removed, field-level modified.

    {
      "added":    [ {"kind":"component","ref":...}|{"kind":"net","name":...} ],
      "removed":  [                                        ],
      "modified": [ {"kind","ref","field","old","new"} ... ],
    }
    """
    raw = compute_diff(old, new)

    added = [_component_summary(c) if c.get("ref") else _net_summary(c) for c in raw["added"]]
    removed = [_component_summary(c) if c.get("ref") else _net_summary(c) for c in raw["removed"]]

    # Normalise: every added/removed entry carries either `ref` (component) or
    # `name` (net), so the frontend can key them uniformly.
    for entry in added + removed:
        if entry["kind"] == "net":
            entry["ref"] = entry.pop("name")

    modified = []
    for m in raw["modified"]:
        # Only emit modified entries that actually changed value.
        if m.get("old") != m.get("new") or (m.get("old") is None) != (m.get("new") is None):
            modified.append(m)

    return {"added": added, "removed": removed, "modified": modified}


def count_changes(diff: dict) -> dict:
    """Summary counts used by the panel badge: {"added":n,"removed":n,"modified":n}."""
    return {
        "added": len(diff.get("added", [])),
        "removed": len(diff.get("removed", [])),
        "modified": len(diff.get("modified", [])),
    }