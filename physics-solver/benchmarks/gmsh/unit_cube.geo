// PCBGenius gmsh verification: unit cube.
// Canonical ground truth: the mesh bounding box must equal the analytic
// geometry [0,1]^3 within 1e-3 per axis, and the mesh must contain >= 1 node.
// LEVEL: numeric_reference (mesh bounding box vs analytic geometry) + exit-0.
SetFactory("OpenFOAM");
box(0.0, 0.0, 0.0, 1.0, 1.0, 1.0, 0.25);