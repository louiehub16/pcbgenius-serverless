// PCBGenius gmsh verification: unit cube.
// Canonical ground truth: the meshed bounding box must equal the analytic
// geometry [0,1]^3 within 1e-3 per axis, and the mesh must contain >= 1 node.
// LEVEL: numeric_reference (mesh bounding box vs analytic geometry) + exit-0.
//
// gmsh geometry kernel: the `Box` command is only available in the OpenCASCADE
// kernel (verified against the real image: the Built-in kernel reports
// "Box only available with OpenCASCADE geometry kernel", and SetFactory("OpenFOAM")
// is not a meshing factory). We use OpenCASCADE. The runner meshes with
// `gmsh -3 -format msh2`, producing a $Nodes block the bbox parser reads.
SetFactory("OpenCASCADE");
Box(1) = {0, 0, 0, 1, 1, 1};
// A modest mesh size to force interior nodes so the bbox is well-sampled.
Mesh.MeshSizeMin = 0.1;
Mesh.MeshSizeMax = 0.25;