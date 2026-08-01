FROM nvidia/cuda:12.1.1-devel-ubuntu22.04 AS builder
ENV DEBIAN_FRONTEND=noninteractive PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1
RUN apt-get update && apt-get install -y --no-install-recommends python3-pip python3-dev libmagic1 curl && rm -rf /var/lib/apt/lists/*
RUN pip3 install --no-cache-dir --upgrade pip
RUN pip3 install --no-cache-dir vllm==0.15.0 runpod huggingface_hub pillow requests python-magic

FROM nvidia/cuda:12.1.1-runtime-ubuntu22.04
ENV DEBIAN_FRONTEND=noninteractive PYTHONUNBUFFERED=1
ENV VLLM_LOGGING_LEVEL=ERROR HF_HUB_DISABLE_TELEMETRY=1 VLLM_NO_HW_METRICS=1
ENV VLLM_ATTENTION_BACKEND=FLASH_ATTN VLLM_CONFIGURE_LOGGING=0
ENV PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1
RUN apt-get update && apt-get install -y --no-install-recommends python3-pip libmagic1 curl && rm -rf /var/lib/apt/lists/*
COPY --from=builder /usr/local/lib/python3.10/dist-packages /usr/local/lib/python3.10/dist-packages
COPY --from=builder /usr/local/bin /usr/local/bin
COPY rp_handler.py /rp_handler.py
CMD ["python3", "-O", "-u", "/rp_handler.py"]
