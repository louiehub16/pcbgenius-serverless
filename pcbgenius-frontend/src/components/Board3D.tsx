import { useEffect, useMemo, useRef, useState } from "react";
import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import { Netlist } from "../contractTypes";

/**
 * PCBGenius — 3D board viewer (D3-3d-view, REAL source)
 * =====================================================
 * Renders a FROZEN-contract netlist as a rotatable 3D PCB using three.js.
 *
 * Consumes the scene JSON emitted by `pcbgenius-three/board_export.py`
 * (`convert_netlist_to_scene`); when no precomputed scene is supplied it
 * builds one from the netlist with a deterministic TS mirror of the Python
 * exporter (`buildSceneFromNetlist`), so it works offline off the sample
 * netlist exactly like the 2D canvas panel.
 *
 * Features
 *   * OrbitControls (pan / rotate / zoom with damping, auto-fit).
 *   * Board = extruded outline (ExtrudeGeometry), PCB-green, black edges.
 *   * One body box + footprint pad per placed ref, coloured by type, sitting
 *     on the board, rotated about the board normal by the contract rotation.
 *   * Toggles: board / parts / pads / labels / wireframe.
 */

// ── mirror of board_export.py visual + geometry policy ──────────────────────
const TYPE_COLORS: Record<string, string> = {
  resistor: "#c9a13b",
  capacitor: "#5b7db1",
  inductor: "#8b5f96",
  diode: "#b23a48",
  led: "#e26d2b",
  transistor: "#3f6b57",
  ic: "#2f3e50",
  connector: "#9aa0a6",
  power: "#7d2e2e",
  crystal: "#6f7f3f",
  switch: "#575757",
};

const BODY_HEIGHTS: Record<string, number> = {
  resistor: 0.55,
  capacitor: 1.0,
  inductor: 2.4,
  diode: 1.1,
  led: 0.7,
  transistor: 1.0,
  ic: 1.4,
  connector: 2.2,
  power: 1.2,
  crystal: 1.0,
  switch: 1.6,
};

const FOOTPRINTS: Record<string, [number, number]> = {
  "0805": [2.0, 1.25],
  "0603": [1.6, 0.8],
  "0402": [1.0, 0.5],
  "1206": [3.2, 1.6],
  "SOT-223": [6.5, 3.6],
  "SOT-23": [2.9, 1.3],
  "TO-263": [10.2, 9.0],
  SMA: [4.6, 2.6],
  CDRH8D28: [8.3, 8.3],
  "10X10MM": [10.0, 10.0],
  "DIP-8": [10.2, 6.6],
  "USB-C-31": [8.8, 7.6],
};

const GENERIC_SIZE: Record<string, [number, number]> = {
  resistor: [2.0, 1.25],
  capacitor: [2.0, 1.25],
  inductor: [4.5, 4.5],
  diode: [4.6, 2.6],
  led: [2.0, 1.25],
  transistor: [2.9, 1.3],
  ic: [8.0, 6.0],
  connector: [8.0, 8.0],
  power: [6.0, 6.0],
  crystal: [4.9, 2.0],
  switch: [6.0, 6.0],
};

const PITCH_MM = 3.0;
const BOARD_MARGIN_MM = 3.0;
const DEFAULT_THICKNESS = 1.6;
const BOARD_COLOR = 0x1b5e20;

// ── scene types (match board_export.py output) ──────────────────────────────
export interface SceneBoard {
  thickness_mm: number;
  layers: number;
  center: { x: number; y: number };
  outline: [number, number][];
  width_mm: number;
  height_mm: number;
}

export interface SceneComponent {
  ref: string;
  type: string;
  value: string;
  package: string;
  color: string;
  position: { x: number; y: number; z: number };
  rotation: number; // deg CCW about the board normal
  size: { w_mm: number; h_mm: number; body_h_mm: number };
}

export interface SceneNet {
  name: string;
  class: string;
  pins: string[];
}

export interface BoardScene {
  schema_version: string;
  generator: string;
  unit: string;
  design_name: string;
  board: SceneBoard;
  components: SceneComponent[];
  nets: SceneNet[];
}

// ── deterministic netlist → scene builder (mirror of board_export.py) ───────
function footprintFor(c: Netlist["components"][number]): [number, number] {
  const pkg = (c.package ?? "").trim().toUpperCase();
  if (pkg && FOOTPRINTS[pkg]) return FOOTPRINTS[pkg];
  return GENERIC_SIZE[c.type] ?? [8.0, 6.0];
}

function buildSceneFromNetlist(netlist: Netlist): BoardScene {
  const comps = [...netlist.components].sort((a, b) => a.ref.localeCompare(b.ref));
  const isAnchor = (c: Netlist["components"][number]) =>
    ["ic", "connector", "power", "crystal", "transistor"].includes(c.type);
  const isPassive = (c: Netlist["components"][number]) =>
    ["resistor", "capacitor", "inductor", "led", "diode"].includes(c.type);
  const anchors = comps.filter(isAnchor);
  const passives = comps.filter(isPassive);
  const others = comps.filter((c) => !isAnchor(c) && !isPassive(c));
  const ordered = [...anchors, ...passives, ...others];

  // deterministic pseudo-random rotations (no Math.random → reproducible)
  const rotOf = (gx: number, gy: number) => [0, 90, 180, 270][((gx * 7 + gy * 13) % 4 + 4) % 4];

  const placement = new Map<string, { x: number; y: number; rotation: number }>();
  const occupied = new Set<string>();
  const tryCell = (ref: string, gx: number, gy: number): boolean => {
    const key = `${gx},${gy}`;
    if (occupied.has(key)) return false;
    occupied.add(key);
    placement.set(ref, { x: gx * PITCH_MM, y: gy * PITCH_MM, rotation: rotOf(gx, gy) });
    return true;
  };

  anchors.forEach((c, i) => tryCell(c.ref, i % 3, -(i >> 2)));
  ordered.forEach((c) => {
    if (placement.has(c.ref)) return;
    if (tryCell(c.ref, 0, 0)) return;
    for (const [dx, dy] of [
      [1, 0], [-1, 0], [0, 1], [0, -1],
      [2, 0], [-2, 0], [0, 2], [0, -2], [1, 1], [-1, -1],
    ]) {
      if (tryCell(c.ref, dx, dy)) return;
    }
    outer: for (let gy = 0; gy < 24; gy++) {
      for (let gx = -8; gx < 8; gx++) {
        if (tryCell(c.ref, gx, gy)) break outer;
      }
    }
  });

  // re-centre around the bounding-box centre so the board sits at origin
  const xs = [...placement.values()].map((p) => p.x);
  const ys = [...placement.values()].map((p) => p.y);
  const cx = (Math.min(...xs) + Math.max(...xs)) / 2;
  const cy = (Math.min(...ys) + Math.max(...ys)) / 2;
  placement.forEach((p) => {
    p.x -= cx;
    p.y -= cy;
  });

  const round4 = (n: number) => Math.round(n * 10000) / 10000;
  const compScene: SceneComponent[] = [];
  for (const c of comps) {
    const p = placement.get(c.ref);
    if (!p) continue;
    const [w, h] = footprintFor(c);
    const bh = BODY_HEIGHTS[c.type] ?? 1.0;
    compScene.push({
      ref: c.ref,
      type: c.type,
      value: c.value,
      package: c.package,
      color: TYPE_COLORS[c.type] ?? "#999999",
      position: { x: round4(p.x), y: round4(p.y), z: round4(bh / 2) },
      rotation: ((p.rotation % 360) + 360) % 360,
      size: { w_mm: round4(w), h_mm: round4(h), body_h_mm: round4(bh) },
    });
  }

  const hw = (Math.max(...xs) - Math.min(...xs) + BOARD_MARGIN_MM * 2 + PITCH_MM) / 2;
  const hh = (Math.max(...ys) - Math.min(...ys) + BOARD_MARGIN_MM * 2 + PITCH_MM) / 2;

  return {
    schema_version: "1.0.0",
    generator: "pcbgenius-frontend/src/components/Board3D.tsx (client build)",
    unit: "mm",
    design_name: netlist.metadata?.design_name ?? "design",
    board: {
      thickness_mm: DEFAULT_THICKNESS,
      layers: netlist.metadata?.board_layers ?? 2,
      center: { x: 0, y: 0 },
      outline: [
        [-hw, -hh],
        [hw, -hh],
        [hw, hh],
        [-hw, hh],
      ],
      width_mm: round4(hw * 2),
      height_mm: round4(hh * 2),
    },
    components: compScene,
    nets: netlist.nets.map((n) => ({ name: n.name, class: n.class, pins: n.pins })),
  };
}

const EMPTY_NETLIST: Netlist = {
  schema_version: "1.0.0",
  metadata: {
    design_name: "empty",
    description: "",
    board_layers: 2,
    created_by: "",
    target_fab: null,
  },
  components: [],
  nets: [],
};

// ── label sprite helper ─────────────────────────────────────────────────────
function makeLabelSprite(text: string): THREE.Sprite {
  const canvas = document.createElement("canvas");
  const scale = 8;
  canvas.width = text.length * scale * 1.4 + 12;
  canvas.height = scale * 2 + 4;
  const ctx = canvas.getContext("2d");
  if (ctx) {
    ctx.font = `${scale * 1.5}px sans-serif`;
    ctx.fillStyle = "#ffffff";
    ctx.textBaseline = "middle";
    ctx.fillText(text, 6, canvas.height / 2 + 1);
  }
  const tex = new THREE.CanvasTexture(canvas);
  tex.minFilter = THREE.LinearFilter;
  const mat = new THREE.SpriteMaterial({ map: tex, depthTest: false, transparent: true });
  const sprite = new THREE.Sprite(mat);
  sprite.scale.set(canvas.width / scale, canvas.height / scale, 1);
  return sprite;
}

// ── view building: board (extrude) + components + labels ────────────────────
interface ViewGroup {
  board: THREE.Group;
  components: THREE.Group;
  labels: THREE.Group;
}

function buildView(scene: BoardScene): ViewGroup {
  const boardGroup = new THREE.Group();
  const compGroup = new THREE.Group();
  const labelGroup = new THREE.Group();

  // board: extrude the outline polygon, lay it flat (XZ plane, y up)
  if (scene.board.outline.length >= 3) {
    const shape = new THREE.Shape();
    scene.board.outline.forEach(([x, y], i) => {
      if (i === 0) shape.moveTo(x, y);
      else shape.lineTo(x, y);
    });
    shape.closePath();
    const thick = scene.board.thickness_mm || DEFAULT_THICKNESS;
    const geo = new THREE.ExtrudeGeometry(shape, { depth: thick, bevelEnabled: false });
    const mesh = new THREE.Mesh(
      geo,
      new THREE.MeshStandardMaterial({ color: BOARD_COLOR, roughness: 0.6, metalness: 0.1 })
    );
    mesh.rotation.x = -Math.PI / 2; // extrusion +z → world +y
    mesh.position.y = -thick / 2; // top surface at y = 0
    const edges = new THREE.LineSegments(
      new THREE.EdgesGeometry(geo),
      new THREE.LineBasicMaterial({ color: 0x000000, transparent: true, opacity: 0.4 })
    );
    edges.rotation.x = -Math.PI / 2;
    edges.position.y = -thick / 2;
    boardGroup.add(mesh, edges);
  }

  scene.components.forEach((c) => {
    const { w_mm, h_mm, body_h_mm } = c.size;
    const body = new THREE.Mesh(
      new THREE.BoxGeometry(w_mm, body_h_mm, h_mm),
      new THREE.MeshStandardMaterial({ color: c.color, roughness: 0.45, metalness: 0.15 })
    );
    const pad = new THREE.Mesh(
      new THREE.BoxGeometry(w_mm * 1.15, 0.05, h_mm * 1.15),
      new THREE.MeshStandardMaterial({
        color: 0xd8b96b,
        transparent: true,
        opacity: 0.55,
        roughness: 0.8,
      })
    );
    pad.userData.pad = true;
    const item = new THREE.Group();
    item.add(pad, body);
    // mm (x, y, z=height) → three (x, y=height, z=-y); CCW rotation → +y axis
    item.position.set(c.position.x, c.position.z, -c.position.y);
    item.rotation.y = THREE.MathUtils.degToRad(c.rotation);
    compGroup.add(item);

    const label = makeLabelSprite(c.ref);
    label.position.set(c.position.x, c.size.body_h_mm + 0.9, -c.position.y);
    labelGroup.add(label);
  });

  return { board: boardGroup, components: compGroup, labels: labelGroup };
}

function disposeObject(root: THREE.Object3D): void {
  root.traverse((o) => {
    const mesh = o as THREE.Mesh;
    if (mesh.geometry) mesh.geometry.dispose();
    const mat = mesh.material as THREE.Material | THREE.Material[] | undefined;
    if (Array.isArray(mat)) mat.forEach((m) => m.dispose());
    else if (mat) mat.dispose();
  });
}

// ── component ───────────────────────────────────────────────────────────────
export interface Board3DProps {
  netlist?: Netlist;
  scene?: BoardScene;
  width?: number | string;
  height?: number | string;
}

export function Board3D({ netlist, scene, width = "100%", height = 320 }: Board3DProps) {
  const mountRef = useRef<HTMLDivElement>(null);
  const sceneObjRef = useRef<THREE.Scene | null>(null);
  const contentRef = useRef<THREE.Group | null>(null);
  const viewRef = useRef<ViewGroup | null>(null);
  const cameraRef = useRef<THREE.PerspectiveCamera | null>(null);
  const controlsRef = useRef<OrbitControls | null>(null);

  const [showBoard, setShowBoard] = useState(true);
  const [showComponents, setShowComponents] = useState(true);
  const [showPads, setShowPads] = useState(true);
  const [showLabels, setShowLabels] = useState(false);
  const [wireframe, setWireframe] = useState(false);

  const resolvedScene = useMemo<BoardScene>(
    () => scene ?? buildSceneFromNetlist(netlist ?? EMPTY_NETLIST),
    [scene, netlist]
  );

  // mount: renderer / scene / lights / controls / resize / RAF loop (once)
  useEffect(() => {
    const mount = mountRef.current;
    if (!mount) return;
    const renderer = new THREE.WebGLRenderer({ antialias: true });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.setClearColor(0x111318, 1);
    mount.appendChild(renderer.domElement);

    const sceneObj = new THREE.Scene();
    sceneObjRef.current = sceneObj;
    sceneObj.add(new THREE.HemisphereLight(0xffffff, 0x334455, 1.0));
    const dir = new THREE.DirectionalLight(0xffffff, 1.2);
    dir.position.set(60, 120, 40);
    sceneObj.add(dir);
    const grid = new THREE.GridHelper(200, 40, 0x2c3e50, 0x22303c);
    grid.position.y = -30;
    sceneObj.add(grid);

    const content = new THREE.Group();
    contentRef.current = content;
    sceneObj.add(content);

    const camera = new THREE.PerspectiveCamera(50, 1, 0.1, 2000);
    cameraRef.current = camera;
    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    controls.dampingFactor = 0.08;
    controls.maxPolarAngle = Math.PI / 1.6;
    controlsRef.current = controls;

    const onResize = () => {
      const w = mount.clientWidth || 1;
      const h = mount.clientHeight || 1;
      renderer.setSize(w, h);
      camera.aspect = w / h;
      camera.updateProjectionMatrix();
    };
    onResize();
    const ro = new ResizeObserver(onResize);
    ro.observe(mount);

    let rafId = 0;
    const loop = () => {
      controls.update();
      renderer.render(sceneObj, camera);
      rafId = requestAnimationFrame(loop);
    };
    rafId = requestAnimationFrame(loop);

    return () => {
      cancelAnimationFrame(rafId);
      ro.disconnect();
      controls.dispose();
      renderer.dispose();
      mount.removeChild(renderer.domElement);
      sceneObjRef.current = null;
      contentRef.current = null;
      viewRef.current = null;
      cameraRef.current = null;
      controlsRef.current = null;
    };
  }, []);

  // rebuild the object tree when the resolved scene changes
  useEffect(() => {
    const content = contentRef.current;
    if (!content) return;
    while (content.children.length) {
      const child = content.children[0];
      content.remove(child);
      disposeObject(child);
    }
    const view = buildView(resolvedScene);
    viewRef.current = view;
    content.add(view.board, view.components, view.labels);

    // auto-fit camera around the board
    const camera = cameraRef.current;
    const controls = controlsRef.current;
    if (camera && controls) {
      const b = resolvedScene.board;
      const extent = Math.max(b.width_mm, b.height_mm, 12) + 12;
      camera.position.set(extent, extent * 0.8, extent * 1.15);
      controls.target.set(0, 0, 0);
      controls.update();
    }
  }, [resolvedScene]);

  // apply toggles (mutates visibility / wireframe)
  useEffect(() => {
    const view = viewRef.current;
    if (!view) return;
    view.board.visible = showBoard;
    view.labels.visible = showLabels;
    view.components.visible = showComponents;
    view.components.traverse((o) => {
      if (o instanceof THREE.Mesh) {
        const isPad = o.userData.pad === true;
        o.visible = showComponents && (!isPad || showPads);
        const mat = o.material as THREE.MeshStandardMaterial;
        if (mat && "wireframe" in mat) {
          mat.wireframe = wireframe;
          mat.needsUpdate = true;
        }
      }
    });
  }, [showBoard, showComponents, showPads, showLabels, wireframe]);

  return (
    <div className="board3d">
      <div className="board3d-tools">
        <h2>3D Board</h2>
        <span className="muted">
          {resolvedScene.design_name} · {resolvedScene.components.length} parts
        </span>
        <label>
          <input type="checkbox" checked={showBoard} onChange={(e) => setShowBoard(e.target.checked)} /> board
        </label>
        <label>
          <input type="checkbox" checked={showComponents} onChange={(e) => setShowComponents(e.target.checked)} /> parts
        </label>
        <label>
          <input type="checkbox" checked={showPads} onChange={(e) => setShowPads(e.target.checked)} /> pads
        </label>
        <label>
          <input type="checkbox" checked={showLabels} onChange={(e) => setShowLabels(e.target.checked)} /> labels
        </label>
        <label>
          <input type="checkbox" checked={wireframe} onChange={(e) => setWireframe(e.target.checked)} /> wireframe
        </label>
      </div>
      <div ref={mountRef} className="board3d-canvas" style={{ width, height }} />
    </div>
  );
}

export default Board3D;