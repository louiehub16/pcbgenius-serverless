FROM nvidia/cuda:12.1.1-runtime-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    VLLM_LOGGING_LEVEL=ERROR \
    VLLM_ATTENTION_BACKEND=FLASH_ATTN \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update && apt-get install -y \
    python3-pip python3-dev libmagic1 curl && \
    rm -rf /var/lib/apt/lists/* && \
    pip3 install --no-cache-dir --upgrade pip && \
    pip3 install --no-cache-dir \
        vllm==0.15.0 \
        runpod \
        huggingface_hub \
        pillow \
        requests \
        python-magic \
        fastapi \
        uvicorn \
    && \
    apt-get clean && \
    rm -rf /root/.cache /tmp/* /var/cache/*

COPY rp_handler.py /rp_handler.py

RUN useradd -m -u 1000 user
USER user
ENV HOME=/home/user PATH=/home/user/.local/bin:$PATH
WORKDIR /home/user

EXPOSE 7860
CMD ["uvicorn", "rp_handler:app", "--host", "0.0.0.0", "--port", "7860"]