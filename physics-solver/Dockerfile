# PCBGenius — CPU physics-solver image (openEMS / Gmsh / CalculiX / ElmerFEM)
# Provisioned for Stage-3 heavy-FEM/FDTD execution probed by pcbgenius-physics/runner.py.
# CPU-only (no CUDA). Built by .github/workflows/physics-solver-build-push.yml -> Docker Hub hrm3478938.
#
# Package-name truth (verified against Ubuntu 22.04 "jammy" universe index, 2026-09-10):
#   openems / gmsh / calculix-ccx   -> all present in jammy universe (apt-installable)
#   elmerfem                        -> NOT packaged; ElmerFEM installs via its Launchpad PPA
#                                      (ppa:elmerfem/elmerfem). We add that PPA below. If the PPA
#                                      step fails on a runner, drop ElmerFEM (3 solvers still ship).
FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive
ENV LANG=C.UTF-8

# 1) Base toolchain (python + mesh + build tools for the PPA).
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 python3-pip python3-numpy python3-scipy python3-matplotlib \
    software-properties-common ca-certificates curl git \
    && rm -rf /var/lib/apt/lists/*

# 2) ElmerFEM from its Launchpad PPA (community maintained). Tolerate failure so the
#    other three solvers still build even if the PPA is unreachable on a runner.
RUN (echo "== add ElmerFEM PPA ==" \
     && add-apt-repository -y ppa:elmerfem/elmerfem 2>/dev/null \
     && apt-get update 2>/dev/null \
     && apt-get install -y --no-install-recommends elmerfem 2>/dev/null) \
    || echo "WARN: ElmerFEM PPA install skipped/failed (continuing with openEMS/Gmsh/CalculiX)"

# 3) The three apt-confirmed solvers + gmsh mesh generator.
RUN apt-get update && apt-get install -y --no-install-recommends \
    openems gmsh calculix-ccx \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /work

# Thin runtime entrypoint: probes the exact binaries pcbgenius-physics/runner.py
# expects (openEMS/gmsh, ElmerSolver, ccx/calculix), runs a tiny smoke solve per
# engine, prints a JSON verdict, then EXITS so the platform can stop billing.
COPY solver_entrypoint.py /work/solver_entrypoint.py
RUN chmod +x /work/solver_entrypoint.py

ENTRYPOINT ["python3", "/work/solver_entrypoint.py"]