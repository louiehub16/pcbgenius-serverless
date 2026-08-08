FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 DEBIAN_FRONTEND=noninteractive
WORKDIR /app

# deps + ssh server for diagnostics
RUN apt-get update && apt-get install -y --no-install-recommends openssh-server curl \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir boto3

COPY supervisor.py /app/supervisor.py

# SSH (debug) — accept PUBLIC_KEY via env; if unset skip.
RUN mkdir -p /root/.ssh && chmod 700 /root/.ssh

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# Long-lived foreground process — never exits.
CMD ["/entrypoint.sh"]
