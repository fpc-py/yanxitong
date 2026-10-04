# =============================================================================
# 研析通 v2.0 — application image
# Build (from project root):
#   docker build -f docker/Dockerfile -t yanxitong-app:2.0 .
# =============================================================================
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app
RUN pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple \
    && pip config set global.trusted-host pypi.tuna.tsinghua.edu.cn
## Install dependencies first for better layer caching
## 先安装 CPU 版 PyTorch（约 180MB，而非 2.4GB 的 CUDA 版）
#RUN pip install torch torchvision torchaudio \
#    --index-url https://download.pytorch.org/whl/cpu \
#    --trusted-host download.pytorch.org
# 使用上海交大镜像安装 CPU 版 torch（仅 torch，不需要 torchvision/torchaudio）
RUN pip install torch \
    --index-url https://mirror.sjtu.edu.cn/pytorch-wheels/cpu \
    --trusted-host mirror.sjtu.edu.cn \
    --retries 10 --timeout 120

# 再安装其余依赖
COPY requirements.txt ./requirements.txt
RUN pip install --no-cache-dir \
    --retries 10 --timeout 120 \
    -r requirements.txt

## 再安装其余依赖，此时 sentence-transformers 会直接复用已装的 CPU torch
#COPY requirements.txt ./requirements.txt
#RUN pip install --no-cache-dir -r requirements.txt


# Application code + configuration
COPY src ./src
COPY config.yaml ./config.yaml

# Run as non-root
RUN groupadd --gid 1001 appuser \
    && useradd --uid 1001 --gid appuser --shell /bin/bash --create-home appuser \
    && mkdir -p /app/data \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8001

# Lightweight healthcheck (no curl installed in slim image)
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8001/health', timeout=3)" || exit 1

CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8001", "--proxy-headers"]
