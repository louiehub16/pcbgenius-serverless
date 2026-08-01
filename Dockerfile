FROM runpod/worker-v1-vllm:stable-cuda12.1.0
COPY rp_handler.py /rp_handler.py
CMD ["python3", "-O", "-u", "/rp_handler.py"]
