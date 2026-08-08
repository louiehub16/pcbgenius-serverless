FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app

# Minimal deps — supervisor only calls HTTP APIs (no ML runtime needed)
RUN pip install --no-cache-dir boto3

COPY supervisor.py /app/supervisor.py

# Long-lived foreground process: Salad requires the container to never exit.
CMD ["python", "-u", "/app/supervisor.py"]
