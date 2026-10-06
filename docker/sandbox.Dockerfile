# =============================================================================
# 研析通 v2.0 — sandboxed code-execution image (data analyst agent)
# Build (from project root):
#   docker build -f docker/sandbox.Dockerfile -t yanxitong-sandbox:2.0 docker
# Execution MUST be network-isolated and resource-capped, e.g.:
#   docker run --rm --network none --memory 512m --cpus 1.0 \
#       --read-only --pids-limit 128 -v ./workdir:/workspace \
#       yanxitong-sandbox:2.0 python /workspace/script.py
# =============================================================================
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    MPLBACKEND=Agg

# Fonts for matplotlib output + basic build tools (removed after wheel install)
RUN apt-get update \
    && apt-get install -y --no-install-recommends fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

# Non-root sandbox user
RUN groupadd --gid 10002 sandbox \
    && useradd --uid 10002 --gid sandbox --shell /usr/sbin/nologin --create-home sandbox

# Data-analysis stack (versions resolved by pip at build time)
RUN pip install --no-cache-dir \
        pandas \
        numpy \
        scipy \
        matplotlib \
        seaborn \
        scikit-learn \
        openpyxl


RUN apt-get update \
    && apt-get install -y --no-install-recommends fonts-wqy-zenhei \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /workspace
RUN chown sandbox:sandbox /workspace
USER sandbox

# Keep the container alive; the agent injects scripts via /workspace
CMD ["sleep", "infinity"]
