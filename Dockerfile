FROM nvidia/cuda:12.1.1-runtime-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive PYTHONUNBUFFERED=1
ENV SAFETENSORS_FAST_GPU=1 VLLM_LOGGING_LEVEL=ERROR
ENV HF_HUB_DISABLE_TELEMETRY=1 VLLM_NO_HW_METRICS=1
ENV PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True
ENV CUDA_MODULE_LOADING=LAZY VLLM_ENGINE_ITERATION_TIMEOUT_S=60
ENV LC_ALL=C VLLM_CONFIGURE_LOGGING=0
ENV VLLM_USE_V0=1

RUN apt-get update && apt-get install -y python3-pip python3-dev curl libmagic1 && rm -rf /var/lib/apt/lists/*
RUN pip3 install --no-cache-dir vllm==0.15.0 runpod huggingface_hub pillow requests python-magic

COPY rp_handler.py /rp_handler.py
CMD ["taskset", "-c", "0", "python3", "-O", "-u", "/rp_handler.py"]