#!/bin/bash
# Tải tất cả model ComfyUI workflow đang sử dụng.
# Idempotent: skip file đã có (check theo size tối thiểu).
# Tự chạy ở entrypoint nếu thiếu model nào.
#
# Manual run:
#   docker exec abc-comfyui bash /app/scripts/download-models.sh
#
# Cần HF_TOKEN với quyền access:
#   - black-forest-labs/FLUX.1-dev (gated)

set -uo pipefail

if [ -n "${HF_TOKEN:-}" ]; then
    hf auth login --token "$HF_TOKEN" --add-to-git-credential 2>/dev/null || true
fi

M=/app/ComfyUI/models
mkdir -p "$M/diffusion_models" "$M/text_encoders" "$M/clip" "$M/vae" \
         "$M/loras" "$M/style_models" "$M/clip_vision"

# need <path> <min_size_MB>  → returns 0 (true) if file MISSING or too small
need() {
    local p="$1"; local min_mb="${2:-10}"
    if [ ! -f "$p" ]; then return 0; fi
    local sz=$(stat -c%s "$p" 2>/dev/null || echo 0)
    [ "$sz" -lt $((min_mb * 1024 * 1024)) ]
}

# hf_get <repo> <file> <target_dir> <min_mb>
hf_get() {
    local repo="$1"; local file="$2"; local dir="$3"; local min_mb="$4"
    local target="$dir/$(basename "$file")"
    if need "$target" "$min_mb"; then
        echo "[*] $repo :: $file → $dir"
        hf download "$repo" "$file" --local-dir "$dir" || echo "  [!] failed: $repo/$file"
    else
        echo "[skip] $target ($(stat -c%s "$target" | awk '{printf "%.0f MB", $1/1024/1024}'))"
    fi
}

# curl_get <url> <target_path> <min_mb>
curl_get() {
    local url="$1"; local target="$2"; local min_mb="$3"
    if need "$target" "$min_mb"; then
        echo "[*] curl → $target"
        curl -sL --fail -o "$target.tmp" "$url" && mv "$target.tmp" "$target" \
            || { rm -f "$target.tmp"; echo "  [!] failed: $url"; }
    else
        echo "[skip] $target ($(stat -c%s "$target" | awk '{printf "%.0f MB", $1/1024/1024}'))"
    fi
}

# ===== FLUX.1-dev (gated — cần HF_TOKEN) =====
hf_get black-forest-labs/FLUX.1-dev flux1-dev.safetensors        "$M/diffusion_models" 20000
hf_get black-forest-labs/FLUX.1-dev ae.safetensors               "$M/vae"              200
hf_get comfyanonymous/flux_text_encoders clip_l.safetensors      "$M/clip"             200
hf_get comfyanonymous/flux_text_encoders t5xxl_fp16.safetensors  "$M/clip"             8000

# ===== FLUX Kontext (image edit) =====
KONTEXT_TARGET="$M/diffusion_models/flux1-dev-kontext_fp8_scaled.safetensors"
if need "$KONTEXT_TARGET" 10000; then
    echo "[*] FLUX Kontext fp8 (12 GB)…"
    hf download Comfy-Org/flux1-kontext-dev_ComfyUI \
        split_files/diffusion_models/flux1-dev-kontext_fp8_scaled.safetensors \
        --local-dir "$M" 2>/dev/null || echo "  [!] kontext download failed"
fi

# ===== Flux Redux (style reference conditioning) =====
curl_get \
  'https://huggingface.co/Comfy-Org/flux1-redux-dev/resolve/main/flux1-redux-dev.safetensors' \
  "$M/style_models/flux1-redux-dev.safetensors" 100

# ===== SigLIP Vision (cho Redux) =====
curl_get \
  'https://huggingface.co/Comfy-Org/sigclip_vision_384/resolve/main/sigclip_vision_patch14_384.safetensors' \
  "$M/clip_vision/sigclip_vision_patch14_384.safetensors" 700

# ===== Style LoRAs =====
curl_get \
  'https://huggingface.co/Shakker-Labs/AWPortrait-FL/resolve/main/AWPortrait-FL-lora.safetensors' \
  "$M/loras/AWPortrait-FL-lora.safetensors" 500
curl_get \
  'https://huggingface.co/alvdansen/sonny-anime-fixed/resolve/main/araminta_k_sonnyanime_fluxd_fixed.safetensors' \
  "$M/loras/sonny_anime_flux.safetensors" 150
curl_get \
  'https://huggingface.co/XLabs-AI/flux-lora-collection/resolve/main/anime_lora.safetensors' \
  "$M/loras/xlabs_anime_lora.safetensors" 30

# ===== Wan 2.2 I2V 14B (video) =====
WAN_REPO=Comfy-Org/Wan_2.2_ComfyUI_Repackaged
HI_T="$M/diffusion_models/wan2.2_i2v_high_noise_14B_fp16.safetensors"
LO_T="$M/diffusion_models/wan2.2_i2v_low_noise_14B_fp16.safetensors"
WAN_TEXT="$M/text_encoders/umt5_xxl_fp8_e4m3fn_scaled.safetensors"
WAN_VAE="$M/vae/wan_2.1_vae.safetensors"
WAN_LORA_HI="$M/loras/wan2.2_i2v_lightx2v_4steps_lora_v1_high_noise.safetensors"
WAN_LORA_LO="$M/loras/wan2.2_i2v_lightx2v_4steps_lora_v1_low_noise.safetensors"

if need "$HI_T" 20000 || need "$LO_T" 20000 || need "$WAN_TEXT" 5000 \
   || need "$WAN_VAE" 200 || need "$WAN_LORA_HI" 1000 || need "$WAN_LORA_LO" 1000; then
    echo "[*] Wan 2.2 bundle…"
    [ -f "$HI_T"  ] && [ "$(stat -c%s "$HI_T" )" -ge $((20000*1024*1024)) ] || \
        hf download "$WAN_REPO" split_files/diffusion_models/wan2.2_i2v_high_noise_14B_fp16.safetensors --local-dir "$M"
    [ -f "$LO_T"  ] && [ "$(stat -c%s "$LO_T" )" -ge $((20000*1024*1024)) ] || \
        hf download "$WAN_REPO" split_files/diffusion_models/wan2.2_i2v_low_noise_14B_fp16.safetensors --local-dir "$M"
    [ -f "$WAN_TEXT" ] && [ "$(stat -c%s "$WAN_TEXT" )" -ge $((5000*1024*1024)) ] || \
        hf download "$WAN_REPO" split_files/text_encoders/umt5_xxl_fp8_e4m3fn_scaled.safetensors --local-dir "$M"
    [ -f "$WAN_VAE" ] && [ "$(stat -c%s "$WAN_VAE" )" -ge $((200*1024*1024)) ] || \
        hf download "$WAN_REPO" split_files/vae/wan_2.1_vae.safetensors --local-dir "$M"
    if need "$WAN_LORA_HI" 1000 || need "$WAN_LORA_LO" 1000; then
        hf download "$WAN_REPO" \
            split_files/loras/wan2.2_i2v_lightx2v_4steps_lora_v1_high_noise.safetensors \
            split_files/loras/wan2.2_i2v_lightx2v_4steps_lora_v1_low_noise.safetensors \
            --local-dir "$M"
    fi
    # Flatten Comfy-Org split_files layout
    if [ -d "$M/split_files" ]; then
        for sub in diffusion_models text_encoders vae loras; do
            if [ -d "$M/split_files/$sub" ]; then
                mv -nv "$M/split_files/$sub"/* "$M/$sub/" 2>/dev/null || true
            fi
        done
        rm -rf "$M/split_files"
    fi
fi

echo "[✓] Done."
