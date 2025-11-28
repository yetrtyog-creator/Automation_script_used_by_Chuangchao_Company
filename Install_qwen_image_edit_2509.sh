#!/usr/bin/env bash
set -euo pipefail

# =========================
# Config - Qwen-Image-Edit 2509 (Plus 版本) 全量安裝
# =========================
COMFY_ROOT="${COMFY_ROOT:-/workspace/ComfyUI}"
MODELS_DIR="$COMFY_ROOT/models"
DIFF_DIR="$MODELS_DIR/diffusion_models"
VAE_DIR="$MODELS_DIR/vae"
TXTENC_DIR="$MODELS_DIR/text_encoders"
LORA_DIR="$MODELS_DIR/loras"
CONTROLNET_DIR="$MODELS_DIR/controlnet"

# Hugging Face Repos（皆為公開資源）
HF_REPO_EDIT="Comfy-Org/Qwen-Image-Edit_ComfyUI"
HF_REPO_BASE="Comfy-Org/Qwen-Image_ComfyUI"
HF_REPO_LIGHTNING="lightx2v/Qwen-Image-Lightning"

# 模型檔案路徑
DIFF_FILE="split_files/diffusion_models/qwen_image_edit_2509_fp8_e4m3fn.safetensors"
VAE_FILE="split_files/vae/qwen_image_vae.safetensors"
TXTENC_FILE="split_files/text_encoders/qwen_2.5_vl_7b_fp8_scaled.safetensors"

# Lightning LoRA（專為 2509 版本）
LIGHTNING_4STEP="Qwen-Image-Edit-2509/Qwen-Image-Edit-2509-Lightning-4steps-V1.0-fp32.safetensors"
LIGHTNING_8STEP="Qwen-Image-Edit-2509/Qwen-Image-Edit-2509-Lightning-8steps-V1.0-fp32.safetensors"

# =========================
# Helpers
# =========================
say() { echo -e "\033[1;32m[Qwen-Edit-2509]\033[0m $*"; }
warn() { echo -e "\033[1;33m[Qwen-Edit-2509 WARN]\033[0m $*" >&2; }
die() { echo -e "\033[1;31m[Qwen-Edit-2509 ERROR]\033[0m $*" >&2; exit 1; }

hf_resolve_url() {
  local repo="$1" path="$2"
  echo "https://huggingface.co/${repo}/resolve/main/${path}?download=true"
}

need_cmd() { command -v "$1" >/dev/null 2>&1; }

prep_dirs() {
  mkdir -p "$DIFF_DIR" "$VAE_DIR" "$TXTENC_DIR" "$LORA_DIR" "$CONTROLNET_DIR"
}

install_tools() {
  if ! need_cmd aria2c; then
    if need_cmd apt-get; then
      say "Installing aria2 ..."
      apt-get update -y && apt-get install -y aria2
    else
      warn "apt-get 不可用，跳過 aria2 安裝；將改用 curl。"
    fi
  fi
}

check_space() {
  say "檢查磁碟空間（/workspace）..."
  df -h /workspace || true
  say "目標下載容量：約 20.4GB (diffusion) + 9.4GB (text encoder) + 0.25GB (VAE) + 3.2GB (LoRA) ≈ 33GB"
}

dl_file() {
  local repo="$1" path="$2" dest="$3"
  local url; url="$(hf_resolve_url "$repo" "$path")"

  if [ -f "$dest" ]; then
    say "已存在：$dest（略過下載）"
    return 0
  fi

  say "下載：$url"
  mkdir -p "$(dirname "$dest")"

  if need_cmd aria2c; then
    aria2c -x16 -s16 -k1M --continue=true --min-split-size=1M --retry-wait=5 --max-tries=20 \
      --dir="$(dirname "$dest")" --out="$(basename "$dest")" "$url" \
      || die "aria2c 下載失敗：$url"
  else
    need_cmd curl || die "缺少 curl"
    curl -L --fail --retry 10 --retry-delay 5 -C - -o "$dest" "$url" \
      || die "curl 下載失敗：$url"
  fi
}

# =========================
# Main
# =========================
say "======================================"
say "安裝 Qwen-Image-Edit 2509 (Plus 版本)"
say "目標路徑：$MODELS_DIR"
say "======================================"

prep_dirs
install_tools
check_space

# 1) Diffusion model
say "[1/5] 下載 Diffusion Model (FP8)..."
dl_file "$HF_REPO_EDIT" "$DIFF_FILE" "$DIFF_DIR/$(basename "$DIFF_FILE")"

# 2) Text encoder
say "[2/5] 下載 Text Encoder..."
dl_file "$HF_REPO_BASE" "$TXTENC_FILE" "$TXTENC_DIR/$(basename "$TXTENC_FILE")"

# 3) VAE
say "[3/5] 下載 VAE..."
dl_file "$HF_REPO_BASE" "$VAE_FILE" "$VAE_DIR/$(basename "$VAE_FILE")"

# 4) Lightning 4-step LoRA
say "[4/5] 下載 Lightning 4-step LoRA..."
dl_file "$HF_REPO_LIGHTNING" "$LIGHTNING_4STEP" "$LORA_DIR/Qwen-Image-Edit-2509-Lightning-4steps-V1.0-fp32.safetensors"

# 5) Lightning 8-step LoRA
say "[5/5] 下載 Lightning 8-step LoRA..."
dl_file "$HF_REPO_LIGHTNING" "$LIGHTNING_8STEP" "$LORA_DIR/Qwen-Image-Edit-2509-Lightning-8steps-V1.0-fp32.safetensors"

# =========================
# 完成報告
# =========================
say "======================================"
say "✅ 安裝完成！"
say "======================================"
echo "檔案位置："
echo "  - Diffusion:     $DIFF_DIR/$(basename "$DIFF_FILE")"
echo "  - Text Encoder:  $TXTENC_DIR/$(basename "$TXTENC_FILE")"
echo "  - VAE:           $VAE_DIR/$(basename "$VAE_FILE")"
echo "  - Lightning 4步: $LORA_DIR/Qwen-Image-Edit-2509-Lightning-4steps-V1.0-fp32.safetensors"
echo "  - Lightning 8步: $LORA_DIR/Qwen-Image-Edit-2509-Lightning-8steps-V1.0-fp32.safetensors"
say ""
say "📝 使用提示："
say "  - 4-step LoRA: KSampler steps=4, cfg=1.0, shift=3.0"
say "  - 8-step LoRA: KSampler steps=8, cfg=1.0, shift=3.0"
say "  - 支援 1-3 張輸入圖片的多圖編輯"
