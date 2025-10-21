#!/usr/bin/env bash
set -euo pipefail

# =========================
# Config
# =========================
COMFY_ROOT="${COMFY_ROOT:-/workspace/ComfyUI}"
MODELS_DIR="$COMFY_ROOT/models"
DIFF_DIR="$MODELS_DIR/diffusion_models"
VAE_DIR="$MODELS_DIR/vae"
TXTENC_DIR="$MODELS_DIR/text_encoders"
LORA_DIR="$MODELS_DIR/loras"
CONTROLNET_DIR="$MODELS_DIR/controlnet"     # 順手建，後續若要用 ControlNet

# 切換項目（0/1）
INSTALL_EDIT="${INSTALL_EDIT:-0}"           # 1 = 同時安裝 Qwen-Image-Edit (v2509)
INSTALL_EXAMPLE_LORA="${INSTALL_EXAMPLE_LORA:-0}"  # 1 = 下載一個 LoRA 範例
USE_ARIA2="${USE_ARIA2:-1}"                 # 1 = 優先使用 aria2c 多線續傳下載

# Hugging Face Repos 與路徑（皆為公開資源）
HF_REPO_MAIN="Comfy-Org/Qwen-Image_ComfyUI"
DIFF_FILE="split_files/diffusion_models/qwen_image_fp8_e4m3fn.safetensors"
TXTENC_FILE="split_files/text_encoders/qwen_2.5_vl_7b_fp8_scaled.safetensors"
VAE_FILE="split_files/vae/qwen_image_vae.safetensors"
EDIT_FILE="split_files/diffusion_models/qwen_image_edit_2509_fp8_e4m3fn.safetensors"

# 範例 LoRA（ASCII 檔名，避免路徑含中文造成殼層轉義問題）
LORA_REPO="AMead10/majewski-qwen-image-lora"
LORA_FILE="majewski-qwen-image_000001750.safetensors"

# =========================
# Helpers
# =========================
say() { echo -e "\033[1;32m[Qwen-Image]\033[0m $*"; }
warn() { echo -e "\033[1;33m[Qwen-Image WARN]\033[0m $*" >&2; }
die() { echo -e "\033[1;31m[Qwen-Image ERROR]\033[0m $*" >&2; exit 1; }

hf_resolve_url() {
  # $1=repo  $2=path (inside repo)
  # 使用 Hugging Face 直接 resolve 連結（支援 Xet/LFS 大檔）
  local repo="$1" path="$2"
  echo "https://huggingface.co/${repo}/resolve/main/${path}?download=true"
}

need_cmd() { command -v "$1" >/dev/null 2>&1; }

prep_dirs() {
  mkdir -p "$DIFF_DIR" "$VAE_DIR" "$TXTENC_DIR" "$LORA_DIR" "$CONTROLNET_DIR"
}

install_tools() {
  # 裝 aria2 與 huggingface_hub CLI（若未安裝）
  if ! need_cmd aria2c && [ "$USE_ARIA2" = "1" ]; then
    if need_cmd apt-get; then
      say "Installing aria2 ..."
      apt-get update -y && apt-get install -y aria2
    else
      warn "apt-get 不可用，跳過 aria2 安裝；將改用 curl。"
      USE_ARIA2=0
    fi
  fi

  if ! need_cmd huggingface-cli; then
    say "Installing huggingface_hub CLI ..."
    python3 -m pip install -U "huggingface_hub[cli]" || warn "pip 安裝 huggingface_hub 失敗，將改用直連下載。"
  fi
}

check_space() {
  say "檢查磁碟空間（/workspace）..."
  df -h /workspace || true
  say "目標下載容量：約 20.4GB (diffusion) + 9.4GB (text encoder) + 0.25GB (VAE) ≈ 30GB。請確保足夠空間。"
}

dl_file() {
  # $1=repo  $2=path  $3=dest_fullpath
  local repo="$1" path="$2" dest="$3"
  local url; url="$(hf_resolve_url "$repo" "$path")"

  if [ -f "$dest" ]; then
    say "已存在：$dest（略過下載）"
    return 0
  fi

  say "下載：$url"
  if [ "$USE_ARIA2" = "1" ] && need_cmd aria2c; then
    aria2c -x16 -s16 -k1M --continue=true --min-split-size=1M --retry-wait=5 --max-tries=20 \
      --dir="$(dirname "$dest")" --out="$(basename "$dest")" "$url" \
      || die "aria2c 下載失敗：$url"
  else
    # 以 curl 續傳模式下載
    need_cmd curl || die "缺少 curl"
    curl -L --fail --retry 10 --retry-delay 5 -C - -o "$dest" "$url" \
      || die "curl 下載失敗：$url"
  fi
}

# =========================
# Main
# =========================
say "安裝 Qwen-Image 標準工作流模型 到 $MODELS_DIR"
prep_dirs
install_tools
check_space

# 1) Diffusion model
dl_file "$HF_REPO_MAIN" "$DIFF_FILE" "$DIFF_DIR/$(basename "$DIFF_FILE")"

# 2) Text encoder
dl_file "$HF_REPO_MAIN" "$TXTENC_FILE" "$TXTENC_DIR/$(basename "$TXTENC_FILE")"

# 3) VAE
dl_file "$HF_REPO_MAIN" "$VAE_FILE" "$VAE_DIR/$(basename "$VAE_FILE")"

# 4) (optional) Edit model
if [ "$INSTALL_EDIT" = "1" ]; then
  dl_file "$HF_REPO_MAIN" "$EDIT_FILE" "$DIFF_DIR/$(basename "$EDIT_FILE")"
fi

# 5) (optional) Example LoRA
if [ "$INSTALL_EXAMPLE_LORA" = "1" ]; then
  # Hugging Face 有些 LoRA 用 Xet/LFS；直接用 resolve 下載單檔
  dl_file "$LORA_REPO" "$LORA_FILE" "$LORA_DIR/$LORA_FILE"
fi

say "完成。檔案位置："
echo "  - Diffusion:     $DIFF_DIR/$(basename "$DIFF_FILE")"
echo "  - Text Encoder:  $TXTENC_DIR/$(basename "$TXTENC_FILE")"
echo "  - VAE:           $VAE_DIR/$(basename "$VAE_FILE")"
[ "$INSTALL_EDIT" = "1" ] && echo "  - Edit Model:    $DIFF_DIR/$(basename "$EDIT_FILE")"
[ "$INSTALL_EXAMPLE_LORA" = "1" ] && echo "  - Example LoRA:  $LORA_DIR/$LORA_FILE"

say "提示：更新到新版 ComfyUI，可在模板中直接載入 Qwen-Image 工作流。"
