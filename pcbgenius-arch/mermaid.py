"""mermaid.py — render a PCBGenius D6 block diagram as a Mermaid flowchart.

Takes the :class:`design.Block` list and emits a ``flowchart`` graph:
each block is a node labelled with its id + label; an edge is drawn for every
net shared by two blocks, labelled with the net name. This gives a quick,
paste-into-mermaid.live architecture view of a design before it is expanded
into a concrete netlist.

Pure stdlib, no network. Two entry points:

* :func:`render_mermaid_flowchart` — from Block objects (used by ``design``).
* :func:`render_mermaid_from_json` — from the serialized block dicts that
  ``design_from_prompt`` returns under the ``"blocks"`` key.

Both produce identical output.
"""

from __future__ import annotations

from typing import Any, Dict, List, Set, Tuple


def render_mermaid_flowchart(blocks: List["Any"], direction: str = "LR") -> str:
    """Render a Block list to a Mermaid ``flowchart LR`` string.

    Nodes are ``id[\\"id<br/>label\\"]``. Edges connect every pair of block ids
    that share an output/input net; the edge is labelled with the net name.
    Nets only used by a single block are shown as a terminal ``--|net|`` stub so
    dangling supply rails (e.g. GND, VIN) remain visible in the diagram.

    Returns a non-empty mermaid block; empty/None input produces an empty node
    set rather than crashing.
    """
    edges: List[Tuple[str, str, str]] = []          # (from, to, net)
    seen: Set[Tuple[str, str, str]] = set()
    single_net_stubs: List[Tuple[str, str]] = []    # (block_id, net)

    net_to_blocks: Dict[str, List[str]] = {}
    for b in blocks or []:
        for net in b.nets:
            net_to_blocks.setdefault(net, []).append(b.id)

    node_ids = {b.id for b in blocks or []}
    for net, ids in net_to_blocks.items():
        within = [i for i in ids if i in node_ids]
        if len(within) < 2:
            # Net only on one block -> dangling rail stub.
            if within:
                single_net_stubs.append((within[0], net))
            continue
        head = within[0]
        for tail in within[1:]:
            key = (head, tail, net)
            rev_key = (tail, head, net)
            if key not in seen and rev_key not in seen:
                edges.append(key)
                seen.add(key)

    lines: List[str] = [f"flowchart {direction}"]
    for b in blocks or []:
        label = b.label.replace('"', "'")
        lines.append(f'    {b.id}["{b.id}<br/>{label}"]')
    for a, z, net in edges:
        lines.append(f'    {a} -->|{net}| {z}')
    for bid, net in single_net_stubs:
        lines.append(f'    {bid} -->|{net}| {net}')

    return "\n".join(lines) + "\n"


def _block_from_dict(d: Dict[str, Any]):
    """Rehydrate a plain dict (as serialized from a Block) into a Block."""
    from design import Block
    return Block(
        id=d.get("id") or "",
        kind=d.get("kind") or "ic",
        label=d.get("label") or d.get("id") or "block",
        nets=list(d.get("nets") or []),
        value=d.get("value") or "",
    )


def render_mermaid_from_json(blocks_json: List[Dict[str, Any]],
                             direction: str = "LR") -> str:
    """Render serialized block dicts (the ``blocks`` key of a design result)."""
    return render_mermaid_flowchart([_block_from_dict(d) for d in blocks_json],
                                    direction=direction)


if __name__ == "__main__":
    # A tiny self-check: render a known 3-block design.
    from design import Block
    example = [
        Block("U1", "regulator", "BuckConverter (LM2596)", ["VIN", "GND", "SW", "VOUT"], "LM2596"),
        Block("L1", "inductor", "Output Inductor (33uH)", ["SW", "VOUT"], "33uH"),
        Block("C1", "capacitor", "Output Capacitor (100uF)", ["VOUT", "GND"], "100uF"),
    ]
    print(render_mermaid_flowchart(example))