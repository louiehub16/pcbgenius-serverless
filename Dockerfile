FROM python:3.11-slim

ENV DEBIAN_FRONTEND=noninteractive PYTHONUNBUFFERED=1
ENV VLLM_ATTENTION_BACKEND=FLASH_ATTN VLLM_NO_HW_METRICS=1
ENV PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1

RUN apt-get update && apt-get install -y --no-install-recommends libmagic1 curl && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir vllm==0.15.0 runpod huggingface_hub pillow requests python-magic

COPY rp_handler.py /rp_handler.py
CMD ["python3", "-O", "-u", "/rp_handler.py"]
