# PCBGenius — CPU physics-solver image (openEMS / Elmer / CalculiX)
# Provisioned for Stage-3 heavy-FEM/FDTD execution probed by pcbgenius-physics/runner.py.
# CPU-only (no CUDA). Built by .github/workflows/physics-solver-build-push.yml -> Docker Hub hrm3478938.
#
# Honest sizing note: these three solvers + gmsh + python3-scipy pull ~1.5-2 GB.
FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive
ENV LANG=C.UTF-8

# Solver toolchain (apt). gmsh = 3D mesh generator both openEMS & Elmer/CalculiX use.
# CalculiX ships `ccx` (solver) + `cgx` (pre/post). Elmer ships `ElmerSolver`.
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 python3-pip python3-numpy python3-scipy python3-matplotlib \
    git curl ca-certificates \
    gmsh openems elmerfem calculix \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /work

# Thin runtime entrypoint: probes the exact binaries pcbgenius-physics/runner.py
# expects, runs a tiny solve per engine, prints a JSON verdict, then exits
# (so Salad/backend can stop billing when it finishes).
COPY solver_entrypoint.py /work/solver_entrypoint.py
RUN chmod +x /work/solver_entrypoint.py

ENTRYPOINT ["python3", "/work/solver_entrypoint.py"]