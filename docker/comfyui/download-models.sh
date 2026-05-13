#!/bin/bash
# Download all ComfyUI models needed by the bundled workflows:
#   - FLUX.1-dev          (text-to-image)
#   - FLUX.1 Kontext Dev  (image edit by prompt)
#   - Wan 2.2 14B I2V     (image-to-video)
#
# Run inside abc-comfyui:
#   docker exec -it abc-comfyui bash /app/scripts/download-models.sh
# Re-runnable: hf CLI skips already-downloaded files.
#
# Total download: ~104 GB. Requires HF_TOKEN with access to:
#   - black-forest-labs/FLUX.1-dev (gated — accept license at HF first)

set -euo pipefail

if [ -n "${HF_TOKEN:-}" ]; then
    echo "[*] Logging in to HuggingFace…"
    hf auth login --token "$HF_TOKEN" --add-to-git-credential 2>/dev/null || true
fi

M=/app/ComfyUI/models
mkdir -p "$M/diffusion_models" "$M/text_encoders" "$M/clip" "$M/vae" "$M/loras"

# ----- FLUX.1-dev (gated — needs HF_TOKEN with access granted) -----
echo "[*] FLUX.1-dev UNET (22 GB)…"
hf download black-forest-labs/FLUX.1-dev flux1-dev.safetensors \
    --local-dir "$M/diffusion_models"

echo "[*] FLUX VAE (320 MB)…"
hf download black-forest-labs/FLUX.1-dev ae.safetensors \
    --local-dir "$M/vae"

echo "[*] FLUX CLIP-L + T5XXL fp16 (9 GB)…"
hf download comfyanonymous/flux_text_encoders \
    clip_l.safetensors t5xxl_fp16.safetensors \
    --local-dir "$M/clip"

# ----- FLUX.1 Kontext Dev (image edit) -----
echo "[*] FLUX.1 Kontext Dev UNET fp8 (12 GB)…"
hf download Comfy-Org/flux1-kontext-dev_ComfyUI \
    split_files/diffusion_models/flux1-dev-kontext_fp8_scaled.safetensors \
    --local-dir "$M"

# ----- Wan 2.2 I2V 14B (Comfy-Org repackaged) -----
WAN_REPO=Comfy-Org/Wan_2.2_ComfyUI_Repackaged

echo "[*] Wan 2.2 I2V high-noise expert fp16 (27 GB)…"
hf download "$WAN_REPO" \
    split_files/diffusion_models/wan2.2_i2v_high_noise_14B_fp16.safetensors \
    --local-dir "$M"

echo "[*] Wan 2.2 I2V low-noise expert fp16 (27 GB)…"
hf download "$WAN_REPO" \
    split_files/diffusion_models/wan2.2_i2v_low_noise_14B_fp16.safetensors \
    --local-dir "$M"

echo "[*] Wan UMT5-XXL text encoder fp8 (6 GB)…"
hf download "$WAN_REPO" \
    split_files/text_encoders/umt5_xxl_fp8_e4m3fn_scaled.safetensors \
    --local-dir "$M"

echo "[*] Wan 2.1 VAE (500 MB)…"
hf download "$WAN_REPO" \
    split_files/vae/wan_2.1_vae.safetensors \
    --local-dir "$M"

echo "[*] Wan 2.2 I2V lightx2v 4-step LoRAs — high+low noise (2.3 GB)…"
hf download "$WAN_REPO" \
    split_files/loras/wan2.2_i2v_lightx2v_4steps_lora_v1_high_noise.safetensors \
    split_files/loras/wan2.2_i2v_lightx2v_4steps_lora_v1_low_noise.safetensors \
    --local-dir "$M"

# Note: Wan 2.2 I2V doesn't need a separate CLIP vision model (unlike Wan 2.1 I2V).
# Image conditioning is handled via UMT5 + diffusion model directly.

# Flatten Comfy-Org "split_files/<subdir>" structure into ComfyUI's models/<subdir>
echo "[*] Flattening split_files layout…"
if [ -d "$M/split_files" ]; then
    for sub in diffusion_models text_encoders vae loras; do
        if [ -d "$M/split_files/$sub" ]; then
            mkdir -p "$M/$sub"
            mv -nv "$M/split_files/$sub"/* "$M/$sub/" 2>/dev/null || true
        fi
    done
    rm -rf "$M/split_files"
fi

echo "[✓] Done. Model layout:"
find "$M" -maxdepth 3 -name '*.safetensors' -printf '  %s  %p\n' | sort -k2 | awk '{ printf "  %10.1f MB  %s\n", $1/1024/1024, $2 }'
