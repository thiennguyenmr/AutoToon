FROM pytorch/pytorch:2.5.1-cuda12.4-cudnn9-runtime

ENV DEBIAN_FRONTEND=noninteractive \
    PIP_NO_CACHE_DIR=1 \
    PYTHONUNBUFFERED=1 \
    TZ=Asia/Ho_Chi_Minh \
    HF_HUB_ENABLE_HF_TRANSFER=1

ARG COMFYUI_VERSION=v0.20.1
ARG COMFYUI_MANAGER_VERSION=4.1

RUN apt-get update && apt-get install -y --no-install-recommends \
        git \
        ffmpeg \
        libgl1 \
        libglib2.0-0 \
        ca-certificates \
        wget \
        curl \
        gosu \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

RUN git clone --depth 1 --branch ${COMFYUI_VERSION} https://github.com/comfyanonymous/ComfyUI /app/ComfyUI

WORKDIR /app/ComfyUI

RUN pip install -r requirements.txt

RUN git clone --depth 1 --branch ${COMFYUI_MANAGER_VERSION} https://github.com/ltdrdata/ComfyUI-Manager /app/ComfyUI/custom_nodes/ComfyUI-Manager \
    && pip install -r /app/ComfyUI/custom_nodes/ComfyUI-Manager/requirements.txt

RUN pip install \
        sentencepiece \
        protobuf \
        imageio \
        imageio-ffmpeg \
        "huggingface_hub[cli]" \
        hf_transfer

# Bake helper scripts and curated workflows into the image so the project
# is reproducible — no host bind mount needed for these.
COPY docker/comfyui/download-models.sh /app/scripts/download-models.sh
COPY docker/comfyui/workflows/         /app/seed-workflows/
COPY docker/comfyui/entrypoint.sh      /app/entrypoint.sh
RUN chmod +x /app/scripts/download-models.sh /app/entrypoint.sh

EXPOSE 8188

ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["python", "main.py", "--listen", "0.0.0.0", "--port", "8188"]
