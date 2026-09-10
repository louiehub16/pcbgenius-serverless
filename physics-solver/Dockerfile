# PCBGenius — CPU physics-solver image (openEMS / Gmsh / CalculiX / ElmerFEM)
# Provisioned for Stage-3 heavy-FEM/FDTD execution probed by pcbgenius-physics/runner.py.
# CPU-only (no CUDA). Built by .github/workflows/physics-solver-build-push.yml -> Docker Hub hrm3478938.
#
# Package-name truth (verified against Ubuntu 22.04 "jammy" universe index, 2026-09-10):
#   openems / gmsh / calculix-ccx   -> present in jammy universe (apt-installable)
#   elmerfem                        -> NOT apt-packaged and the community PPA is unreliable
#                                      (fails to resolve on GH runners); ElmerFEM ships source-only.
#                                      We SOURCE-BUILD it (official ElmerCSC recipe, v26.2) in a
#                                      build stage and copy the solver binaries into the final image.

# ============================================================================
# STAGE 1 — build ElmerFEM from source (official ElmerCSC v26.2, serial + MPI
#           with MUMPS; no GUI so the image stays lean — solvers only).
# ============================================================================
FROM ubuntu:22.04 AS elmerbuild

ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates git cmake make gfortran g++ \
    libopenblas-dev libopenmpi-dev libmumps-dev libparmetis-dev \
    && rm -rf /var/lib/apt/lists/*

RUN git clone --depth 1 --branch release-26.2 \
      https://github.com/ElmerCSC/elmerfem.git /src/elmerfem \
 && mkdir -p /src/elmerfem-build \
 && cd /src/elmerfem-build \
 && cmake /src/elmerfem \
      -DCMAKE_BUILD_TYPE=Release \
      -DCMAKE_INSTALL_PREFIX=/opt/elmerfem \
      -DBLA_VENDOR=OpenBLAS \
      -DWITH_OpenMP=ON \
      -DWITH_MPI=ON \
      -DWITH_Mumps=ON \
      -DWITH_ELMERGUI=OFF \
      -DWITH_PARAVIEW=OFF \
 && cmake --build . -j$(nproc) \
 && cmake --install .

# ============================================================================
# STAGE 2 — final runtime image: 3 apt solvers + the built ElmerFEM binaries.
# ============================================================================
FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive
ENV LANG=C.UTF-8

# Python toolchain + the 3 apt-confirmed solvers + mesh gen.
# ElmerFEM binaries are copied from the build stage and were compiled WITH_Mumps=ON +
# WITH_MPI=ON, so they dynamically need the parallel MUMPS + OpenMPI RUNTIME libs.
# Correct jammy names: libmumps-ptscotch-5.4 + libscalapack-openmpi2.1 + libopenmpi3
# (NOT "libmumps-ptscotch" — that name does not exist under jammy).
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 python3-pip python3-numpy python3-scipy python3-matplotlib \
    curl ca-certificates \
    openems gmsh calculix-ccx \
    libopenmpi3 libmumps-ptscotch-5.4 libscalapack-openmpi2.1 \
    && rm -rf /var/lib/apt/lists/*

# Copy the source-built ElmerFEM solvers into /usr/local/bin.
COPY --from=elmerbuild /opt/elmerfem/bin/ /usr/local/bin/
RUN chmod +x /usr/local/bin/ElmerSolver /usr/local/bin/ElmerSolver_mpi 2>/dev/null || true

WORKDIR /work

# Thin runtime entrypoint: probes the exact binaries pcbgenius-physics/runner.py
# expects (openEMS/gmsh, ElmerSolver, ccx/calculix), runs a tiny smoke solve per
# engine, prints a JSON verdict, then EXITS so the platform can stop billing.
COPY solver_entrypoint.py /work/solver_entrypoint.py
RUN chmod +x /work/solver_entrypoint.py

ENTRYPOINT ["python3", "/work/solver_entrypoint.py"]