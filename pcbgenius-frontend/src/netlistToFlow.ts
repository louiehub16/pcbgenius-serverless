import { Node, Edge } from "@xyflow/react";
import { Netlist, Component } from "./contractTypes";

/**
 * Converts a contract netlist (Section 1 shape) into React Flow nodes/edges.
 *
 * - Each Component becomes a node with one source handle per pin.
 * - Each Net becomes a "fake" node too (a small pill labelled with the net name),
 *   and each component pin connects to its net node via an edge. This makes nets
 *   easy to see and click (a common beginner-friendly way to lay out schematic
 *   graphs from a connectivity list).
 *
 * Pins that share a net all terminate on the same net node, so the topology is
 * readable without crossing wires: Component.pin ──► NET_NAME.
 */

const NET_COLOR: Record<string, string> = {
  power: "#eab308",
  ground: "#22c55e",
  signal: "#3b82f6",
  clock: "#a855f7",
  analog: "#f97316",
  digital: "#06b6d4",
};

// Simple deterministic grid placement so things don't all pile on top of each other.
export function netlistToNodesAndEdges(netlist: Netlist): { nodes: Node[]; edges: Edge[] } {
  const nodes: Node[] = [];
  const edges: Edge[] = [];

  const netSet = new Set(netlist.nets.map((n) => n.name));

  // One node per component.
  netlist.components.forEach((comp: Component, i: number) => {
    const x = 40 + (i % 4) * 260;
    const y = 60 + Math.floor(i / 4) * 220;
    nodes.push({
      id: `comp-${comp.ref}`,
      type: "component",
      position: { x, y },
      data: { component: comp },
    });
  });

  // One node per net (the hub every connected pin points at).
  netlist.nets.forEach((net, i) => {
    const x = 160 + (i % 4) * 260;
    const y = 520 + Math.floor(i / 4) * 90;
    nodes.push({
      id: `net-${net.name}`,
      type: "net",
      position: { x, y },
      data: { net },
      style: { borderColor: NET_COLOR[net.class] ?? "#64748b" },
    });
  });

  // Edges: component.pin ──► net node.
  for (const comp of netlist.components) {
    for (const pin of comp.pins) {
      if (!pin.net) continue;
      const key = `${comp.ref}.${pin.name}`;
      if (!netSet.has(pin.net)) continue; // pin references unknown net (validator would flag this)
      edges.push({
        id: `edge-${comp.ref}-${pin.name}-${pin.net}`,
        source: `comp-${comp.ref}`,
        sourceHandle: key,
        target: `net-${pin.net}`,
        animated: false,
        style: { stroke: netNetColor(netlist, pin.net) ?? "#94a3b8" },
      });
    }
  }

  return { nodes, edges };
}

function netNetColor(netlist: Netlist, name: string): string | undefined {
  const net = netlist.nets.find((n) => n.name === name);
  return net ? NET_COLOR[net.class] : undefined;
}
