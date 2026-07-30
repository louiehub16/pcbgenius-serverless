FROM ubuntu:22.04
ENV DEBIAN_FRONTEND=noninteractive PYTHONUNBUFFERED=1
ENV VLLM_LOGGING_LEVEL=ERROR HF_HUB_DISABLE_TELEMETRY=1 VLLM_NO_HW_METRICS=1
ENV VLLM_ATTENTION_BACKEND=FLASH_ATTN VLLM_CONFIGURE_LOGGING=0
ENV PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1 PIP_REQUIRE_HASHES=0
RUN apt-get update && apt-get install -y --no-install-recommends python3-pip python3-dev libmagic1 curl && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir --upgrade pip && pip install --no-cache-dir vllm==0.15.0 runpod huggingface_hub pillow requests python-magic && apt-get clean && rm -rf /root/.cache
COPY rp_handler.py /rp_handler.py
CMD ["python3", "-O", "-u", "/rp_handler.py"]
