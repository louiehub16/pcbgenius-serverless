# PCBGenius training image — GH-runner-friendly
# FIX (2026-08-08): use a tiny ubuntu base instead of nvcr.io/nvidia/pytorch
# (huge ~tens-of-GB base). The heavy CUDA/torch/unsloth install is deferred to
# container STARTUP (the documented pattern), so the GH-hosted runner only
# builds a small image — no disk blow-up, no storage expansion needed.
FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive \
    PIP_NO_CACHE_DIR=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/root/.local/bin:/opt/venv/bin:$PATH"

RUN apt-get update && apt-get install -y --no-install-recommends \
      python3 python3-pip python3-venv bash curl git unzip ca-certificates rclone openssh-server \
    && rm -rf /var/lib/apt/lists/* \
    && python3 -m venv /opt/venv

# SSH debug (accept PUBLIC_KEY from env; diagnostics only)
RUN mkdir -p /root/.ssh && chmod 700 /root/.ssh
COPY ssh_entrypoint.sh /ssh_entrypoint.sh
RUN chmod +x /ssh_entrypoint.sh

# Copy pipeline
WORKDIR /pipeline
COPY master_pipeline.sh /pipeline/master_pipeline.sh
COPY stages/ /pipeline/stages/
COPY lib/ /pipeline/lib/
RUN chmod +x /pipeline/master_pipeline.sh /pipeline/stages/*.sh

# A startup bootstrap installs heavy training deps at run time (on the GPU
# node's 50GB+ disk), not at GH build time.
COPY bootstrap.sh /bootstrap.sh
RUN chmod +x /bootstrap.sh

# Work dir for artifacts
RUN mkdir -p /work

ENTRYPOINT ["/bin/bash", "/ssh_entrypoint.sh"]
