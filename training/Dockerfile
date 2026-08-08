# PCBGenius standalone model pipeline image (H100 training node)
FROM nvcr.io/nvidia/pytorch:24.01-py3

# Orchestration deps
RUN apt-get update && apt-get install -y --no-install-recommends \
      bash curl git unzip ca-certificates rclone \
    && rm -rf /var/lib/apt/lists/*

# Python deps (training + data + hub)
RUN pip install --no-cache-dir \
      "unsloth[colab-new] @ git+https://github.com/unslothai/unsloth.git" \
    && pip install --no-cache-dir --no-deps xformers trl peft accelerate bitsandbytes \
    && pip install --no-cache-dir datasets huggingface_hub wandb anthropic

# Pipeline files
WORKDIR /pipeline
COPY master_pipeline.sh /pipeline/master_pipeline.sh
COPY stages/ /pipeline/stages/
RUN chmod +x /pipeline/master_pipeline.sh /pipeline/stages/*.sh

# Work dir for artifacts
RUN mkdir -p /work

ENTRYPOINT ["/bin/bash", "/pipeline/master_pipeline.sh"]
