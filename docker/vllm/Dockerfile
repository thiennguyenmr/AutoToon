FROM nvidia/cuda:12.8.0-devel-ubuntu24.04

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONDONTWRITEBYTECODE=1

# System deps + Python 3.12 (default on Ubuntu 24.04)
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3.12 \
    python3.12-dev \
    python3-pip \
    curl \
    git \
    build-essential \
    ninja-build \
    && rm -rf /var/lib/apt/lists/* \
    && ln -sf /usr/bin/python3.12 /usr/bin/python3 \
    && ln -sf /usr/bin/python3.12 /usr/bin/python

# Install uv
RUN curl -Ls https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.local/bin:$PATH"

# Install vLLM (matches CUDA 12.8 devel base)
RUN uv pip install --system --break-system-packages --pre vllm==0.20.1 \
    --extra-index-url https://download.pytorch.org/whl/nightly/cu128 \
    --index-strategy unsafe-best-match

# Build DeepGEMM from source with submodules (CUTLASS).
# Required for block-scaled FP8 kernels used by Qwen3.6-A3B-FP8.
ENV TORCH_CUDA_ARCH_LIST="9.0"
RUN git clone --recursive https://github.com/deepseek-ai/DeepGEMM.git /tmp/deep_gemm \
    && cd /tmp/deep_gemm \
    && uv pip install --system --break-system-packages --no-build-isolation . \
    && rm -rf /tmp/deep_gemm

EXPOSE 8000

CMD ["vllm", "serve", "Qwen/Qwen3.6-35B-A3B-FP8", \
     "--trust-remote-code", \
     "--host", "0.0.0.0", \
     "--port", "8000", \
     "--served-model-name", "Qwen/Qwen3.6-35B-A3B-FP8"]
