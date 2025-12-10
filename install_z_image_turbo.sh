#!/usr/bin/env bash
set -euo pipefail

# =========================
# Z-Image Turbo 安裝腳本 for ComfyUI
# =========================
# Z-Image Turbo 是阿里巴巴通義實驗室推出的高效能 6B 參數圖像生成模型
# 蒸餾版本僅需 8 NFEs 即可生成高質量照片寫實圖像
# 支援 16GB VRAM 消費級顯卡
# =========================

# =========================
# Config
# =========================
COMFY_ROOT="${COMFY_ROOT:-/workspace/ComfyUI}"
MODELS_DIR="$COMFY_ROOT/models"
DIFF_DIR="$MODELS_DIR/diffusion_models"
VAE_DIR="$MODELS_DIR/vae"
TXTENC_DIR="$MODELS_DIR/text_encoders"
LORA_DIR="$MODELS_DIR/loras"
CONTROLNET_DIR="$MODELS_DIR/controlnet"
MODEL_PATCHES_DIR="$MODELS_DIR/model_patches"

# 切換項目（0/1）
INSTALL_EXAMPLE_LORA="${INSTALL_EXAMPLE_LORA:-0}"  # 1 = 下載 Pixel Art 風格 LoRA 範例
INSTALL_CONTROLNET="${INSTALL_CONTROLNET:-0}"     # 1 = 下載 Z-Image Turbo Fun ControlNet Union
USE_ARIA2="${USE_ARIA2:-1}"                       # 1 = 優先使用 aria2c 多線續傳下載

# Hugging Face Repos 與路徑（皆為公開資源）
HF_REPO_MAIN="Comfy-Org/z_image_turbo"

# 核心模型檔案路徑
DIFF_FILE="split_files/diffusion_models/z_image_turbo_bf16.safetensors"
TXTENC_FILE="split_files/text_encoders/qwen_3_4b.safetensors"
VAE_FILE="split_files/vae/ae.safetensors"

# 範例 LoRA（Pixel Art 風格）
LORA_REPO="tarn59/pixel_art_style_lora_z_image_turbo"
LORA_FILE="pixel_art_style_z_image_turbo.safetensors"

# ControlNet Union（可選）- 支援 Canny, HED, Depth, Pose, MLSD
CONTROLNET_REPO="alibaba-pai/Z-Image-Turbo-Fun-Controlnet-Union"
CONTROLNET_FILE="Z-Image-Turbo-Fun-Controlnet-Union.safetensors"

# =========================
# Helpers
# =========================
say() { echo -e "\033[1;32m[Z-Image-Turbo]\033[0m $*"; }
warn() { echo -e "\033[1;33m[Z-Image-Turbo WARN]\033[0m $*" >&2; }
die() { echo -e "\033[1;31m[Z-Image-Turbo ERROR]\033[0m $*" >&2; exit 1; }

hf_resolve_url() {
  # $1=repo  $2=path (inside repo)
  # 使用 Hugging Face 直接 resolve 連結（支援 Xet/LFS 大檔）
  local repo="$1" path="$2"
  echo "https://huggingface.co/${repo}/resolve/main/${path}?download=true"
}

need_cmd() { command -v "$1" >/dev/null 2>&1; }

prep_dirs() {
  mkdir -p "$DIFF_DIR" "$VAE_DIR" "$TXTENC_DIR" "$LORA_DIR" "$CONTROLNET_DIR" "$MODEL_PATCHES_DIR"
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
  say "目標下載容量："
  say "  - Diffusion Model (BF16): ~12.2 GB"
  say "  - Text Encoder (Qwen 3 4B): ~8.0 GB"
  say "  - VAE: ~0.3 GB"
  say "  總計約 20.5 GB，請確保足夠空間。"
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
say "=========================================="
say "安裝 Z-Image Turbo 模型到 $MODELS_DIR"
say "=========================================="
say ""
say "Z-Image Turbo 是阿里巴巴通義實驗室的蒸餾式圖像生成模型"
say "特點：8 步推理、照片寫實品質、支援中英文文字渲染"
say ""

prep_dirs
install_tools
check_space

say ""
say "開始下載核心模型..."
say ""

# 1) Diffusion model (BF16)
say "[1/3] 下載 Diffusion Model..."
dl_file "$HF_REPO_MAIN" "$DIFF_FILE" "$DIFF_DIR/$(basename "$DIFF_FILE")"

# 2) Text encoder (Qwen 3 4B)
say "[2/3] 下載 Text Encoder (Qwen 3 4B)..."
dl_file "$HF_REPO_MAIN" "$TXTENC_FILE" "$TXTENC_DIR/$(basename "$TXTENC_FILE")"

# 3) VAE (與 Flux 1 共用)
say "[3/3] 下載 VAE..."
dl_file "$HF_REPO_MAIN" "$VAE_FILE" "$VAE_DIR/$(basename "$VAE_FILE")"

# 4) (optional) Example LoRA - Pixel Art Style
if [ "$INSTALL_EXAMPLE_LORA" = "1" ]; then
  say ""
  say "[可選] 下載範例 LoRA (Pixel Art Style)..."
  dl_file "$LORA_REPO" "$LORA_FILE" "$LORA_DIR/$LORA_FILE"
fi

# 5) (optional) ControlNet Union
if [ "$INSTALL_CONTROLNET" = "1" ]; then
  say ""
  say "[可選] 下載 Z-Image Turbo Fun ControlNet Union..."
  dl_file "$CONTROLNET_REPO" "$CONTROLNET_FILE" "$MODEL_PATCHES_DIR/$CONTROLNET_FILE"
fi

say ""
say "=========================================="
say "安裝完成！"
say "=========================================="
say ""
say "檔案位置："
echo "  📂 Diffusion Model:  $DIFF_DIR/$(basename "$DIFF_FILE")"
echo "  📂 Text Encoder:     $TXTENC_DIR/$(basename "$TXTENC_FILE")"
echo "  📂 VAE:              $VAE_DIR/$(basename "$VAE_FILE")"
[ "$INSTALL_EXAMPLE_LORA" = "1" ] && echo "  📂 Example LoRA:     $LORA_DIR/$LORA_FILE"
[ "$INSTALL_CONTROLNET" = "1" ] && echo "  📂 ControlNet:       $MODEL_PATCHES_DIR/$CONTROLNET_FILE"

say ""
say "ComfyUI 目錄結構："
cat << 'EOF'
📂 ComfyUI/
├── 📂 models/
│   ├── 📂 text_encoders/
│   │   └── qwen_3_4b.safetensors
│   ├── 📂 diffusion_models/
│   │   └── z_image_turbo_bf16.safetensors
│   └── 📂 vae/
│       └── ae.safetensors
EOF

say ""
say "使用說明："
say "  1. 確保 ComfyUI 已更新至最新版本"
say "  2. 在 ComfyUI 中載入 Z-Image Turbo 工作流模板"
say "  3. 推薦設定：8 步推理、Guidance Scale = 0.0"
say ""
say "官方工作流範例："
say "  https://comfyanonymous.github.io/ComfyUI_examples/z_image/"
say ""
say "注意：Z-Image Turbo 使用獨特的 S3-DiT 架構，"
say "      需使用專用節點而非傳統的 Checkpoint Loader。"
