import { useCallback, useEffect, useMemo, useState } from "react";
import { MouseEvent as ReactMouseEvent } from "react";
import {
  ReactFlow,
  Background,
  Controls,
  MiniMap,
  useNodesState,
  useEdgesState,
  addEdge,
  Connection,
  Node,
  NodeMouseHandler,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";

import { sampleNetlist } from "./sampleNetlist";
import { netlistToNodesAndEdges } from "./netlistToFlow";
import { validateNetlist } from "./validate";
import { ComponentNode, NetNode } from "./components/Nodes";
import { Inspector } from "./components/Inspector";
import { ViolationsPanel } from "./components/ViolationsPanel";
import type { Netlist } from "./contractTypes";

const nodeTypes = { component: ComponentNode, net: NetNode };

export default function App() {
  const [netlist, setNetlist] = useState<Netlist>(sampleNetlist);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [theme, setTheme] = useState<"light" | "dark">("dark");

  const { nodes: initialNodes, edges: initialEdges } = useMemo(
    () => netlistToNodesAndEdges(netlist),
    [netlist]
  );
  const [nodes, setNodes, onNodesChange] = useNodesState(initialNodes);
  const [edges, setEdges, onEdgesChange] = useEdgesState(initialEdges);

  // Keep React Flow nodes/edges in sync when the netlist changes (import/export/reset).
  useEffect(() => {
    setNodes(initialNodes);
    setEdges(initialEdges);
  }, [initialNodes, initialEdges, setNodes, setEdges]);

  const violations = useMemo(() => validateNetlist(netlist), [netlist]);

  const onConnect = useCallback(
    (conn: Connection) => setEdges((eds) => addEdge(conn, eds)),
    [setEdges]
  );

  const onNodeClick: NodeMouseHandler = useCallback(
    (_: ReactMouseEvent, node: Node) => {
      setSelectedId(node.id);
    },
    []
  );

  const onPaneClick = useCallback(() => setSelectedId(null), []);

  const toggleTheme = () => setTheme((t) => (t === "dark" ? "light" : "dark"));

  const handleImport = (file: File) => {
    const reader = new FileReader();
    reader.onload = () => {
      try {
        const parsed = JSON.parse(String(reader.result));
        setNetlist(parsed as Netlist);
      } catch {
        alert("Could not parse that file as JSON. It must be a valid PCBGenius netlist.");
      }
    };
    reader.readAsText(file);
  };

  const handleExport = () => {
    const blob = new Blob([JSON.stringify(netlist, null, 2)], {
      type: "application/json",
    });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${netlist.metadata?.design_name ?? "design"}.netlist.json`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const handleReset = () => setNetlist(sampleNetlist);

  return (
    <div className={`app theme-${theme}`}>
      <header className="toolbar">
        <div className="brand">
          <span className="logo">⚡</span> PCBGenius
          <span className="contract-tag">contract v1.0.0</span>
        </div>
        <div className="tools">
          <button onClick={handleReset}>Reset sample</button>
          <label className="btn">
            Import JSON
            <input
              type="file"
              accept="application/json,.json"
              style={{ display: "none" }}
              onChange={(e) => e.target.files?.[0] && handleImport(e.target.files[0])}
            />
          </label>
          <button onClick={handleExport}>Export JSON</button>
          <button onClick={toggleTheme} className="theme-btn">
            {theme === "dark" ? "☀️ Light" : "🌙 Dark"}
          </button>
        </div>
      </header>

      <div className="layout">
        <main className="canvas-wrap">
          <ReactFlow
            nodes={nodes}
            edges={edges}
            onNodesChange={onNodesChange}
            onEdgesChange={onEdgesChange}
            onConnect={onConnect}
            onNodeClick={onNodeClick}
            onPaneClick={onPaneClick}
            nodeTypes={nodeTypes}
            fitView
            proOptions={{ hideAttribution: true }}
          >
            <Background />
            <Controls />
            <MiniMap pannable zoomable />
          </ReactFlow>
        </main>
        <Inspector netlist={netlist} selectedId={selectedId} />
        <ViolationsPanel violations={violations} />
      </div>
    </div>
  );
}
