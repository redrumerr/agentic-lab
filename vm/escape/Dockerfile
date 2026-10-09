FROM node:24-bookworm-slim
# OpenClaw is optional: pin a version before running a model comparison.
ARG OPENCLAW_VERSION="2026.9.6"
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 docker.io procps iproute2 util-linux kmod gcc make ca-certificates \
    && rm -rf /var/lib/apt/lists/*
RUN if [ -n "$OPENCLAW_VERSION" ]; then npm install -g "openclaw@$OPENCLAW_VERSION"; fi
WORKDIR /app
COPY openclaw-adapter/adapter.py ./adapter.py
ENV PYTHONDONTWRITEBYTECODE=1 DOCKER_API_VERSION=1.44
CMD ["python3", "adapter.py", "--serve"]
