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
INSTALL_EDIT="${INSTALL_EDIT:-1}"           # [CHANGED] 預設改為 1：確保會下載 Qwen-Image-Edit (v2509)
INSTALL_EXAMPLE_LORA="${INSTALL_EXAMPLE_LORA:-0}"
USE_ARIA2="${USE_ARIA2:-1}"

# [ADDED] 可選：提供 Hugging Face Token（如需存取受限/速率較佳）
# 使用方式：export HUGGINGFACE_TOKEN=hf_xxx
HUGGINGFACE_TOKEN="${HUGGINGFACE_TOKEN:-}"

# Hugging Face Repos 與路徑（皆為公開資源）
HF_REPO_MAIN="Comfy-Org/Qwen-Image_ComfyUI"
DIFF_FILE="split_files/diffusion_models/qwen_image_fp8_e4m3fn.safetensors"
TXTENC_FILE="split_files/text_encoders/qwen_2.5_vl_7b_fp8_scaled.safetensors"
VAE_FILE="split_files/vae/qwen_image_vae.safetensors"
EDIT_FILE="split_files/diffusion_models/qwen_image_edit_2509_fp8_e4m3fn.safetensors"

# 範例 LoRA（ASCII 檔名，避免路徑含中文造成殼層轉義問題）
LORA_REPO="AMead10/majewski-qwen-image-lora"
LORA_FILE="majewski-qwen-image_000001750.safetensors"

# [ADDED] 下載後的「最小檔案大小」門檻（MB）。若檔案小於門檻，視為失敗以避免破檔。
# 可視需要以環境變數覆寫，例如：export DIFF_MIN_SIZE_MB=12000
DIFF_MIN_SIZE_MB="${DIFF_MIN_SIZE_MB:-10000}"
TXTENC_MIN_SIZE_MB="${TXTENC_MIN_SIZE_MB:-4000}"
VAE_MIN_SIZE_MB="${VAE_MIN_SIZE_MB:-100}"
EDIT_MIN_SIZE_MB="${EDIT_MIN_SIZE_MB:-1000}"
LORA_MIN_SIZE_MB="${LORA_MIN_SIZE_MB:-2}"

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
      USE_ARIA2=0   # [CHANGED] 同步更新旗標以避免之後仍嘗試 aria2
    fi
  fi

  if ! need_cmd huggingface-cli; then
    say "Installing huggingface_hub CLI ..."
    python3 -m pip install -U "huggingface_hub[cli]" || warn "pip 安裝 huggingface_hub 失敗，將改用直連下載。"
  fi
}

# [ADDED] 檢查檔案大小是否達門檻（MB）
ensure_min_size() {
  # $1=filepath  $2=min_size_mb
  local f="$1" min_mb="$2"
  if [ ! -f "$f" ]; then
    die "找不到已下載檔案：$f"
  fi
  # 使用 stat 取得位元組數
  local bytes
  bytes="$(stat -c%s "$f" 2>/dev/null || stat -f%z "$f" 2>/dev/null || echo 0)"
  local need_bytes=$(( min_mb * 1024 * 1024 ))
  if [ "$bytes" -lt "$need_bytes" ]; then
    rm -f "$f"
    die "檔案大小不足（$bytes bytes < ${need_bytes} bytes）。可能下載破檔或網路中斷：$f"
  fi
}

check_space() {
  say "檢查磁碟空間（/workspace）..."
  df -h /workspace || true
  say "目標下載容量（估）：diffusion/text-encoder/vae/edit 可能共計 10~40GB；請確保空間足夠。"
  # [CHANGED] 說明改為「區間」以避免不同壓縮/更新造成容量差異
}

# [CHANGED] 擴充 dl_file：支援授權標頭與最小檔案大小檢查
dl_file() {
  # $1=repo  $2=path  $3=dest_fullpath  [$4=min_size_mb]
  local repo="$1" path="$2" dest="$3" min_mb="${4:-0}"
  local url; url="$(hf_resolve_url "$repo" "$path")"

  if [ -f "$dest" ]; then
    say "已存在：$dest（略過下載）"
    if [ "$min_mb" -gt 0 ]; then
      ensure_min_size "$dest" "$min_mb"
    fi
    return 0
  fi

  mkdir -p "$(dirname "$dest")"
  say "下載：$url"
  if [ "$USE_ARIA2" = "1" ] && need_cmd aria2c; then
    # [ADDED] 若有 Token，自動帶上授權標頭
    if [ -n "$HUGGINGFACE_TOKEN" ]; then
      aria2c -x16 -s16 -k1M --continue=true --min-split-size=1M --retry-wait=5 --max-tries=20 \
        --header="Authorization: Bearer ${HUGGINGFACE_TOKEN}" \
        --dir="$(dirname "$dest")" --out="$(basename "$dest")" "$url" \
        || die "aria2c 下載失敗：$url"
    else
      aria2c -x16 -s16 -k1M --continue=true --min-split-size=1M --retry-wait=5 --max-tries=20 \
        --dir="$(dirname "$dest")" --out="$(basename "$dest")" "$url" \
        || die "aria2c 下載失敗：$url"
    fi
  else
    need_cmd curl || die "缺少 curl"
    if [ -n "$HUGGINGFACE_TOKEN" ]; then
      curl -L --fail --retry 10 --retry-delay 5 -C - \
        -H "Authorization: Bearer ${HUGGINGFACE_TOKEN}" \
        -o "$dest" "$url" \
        || die "curl 下載失敗：$url"
    else
      curl -L --fail --retry 10 --retry-delay 5 -C - \
        -o "$dest" "$url" \
        || die "curl 下載失敗：$url"
    fi
  fi

  # [ADDED] 下載後做最小檔案大小檢查
  if [ "$min_mb" -gt 0 ]; then
    ensure_min_size "$dest" "$min_mb"
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
dl_file "$HF_REPO_MAIN" "$DIFF_FILE" "$DIFF_DIR/$(basename "$DIFF_FILE")" "$DIFF_MIN_SIZE_MB"   # [CHANGED] 增加最小大小檢查

# 2) Text encoder
dl_file "$HF_REPO_MAIN" "$TXTENC_FILE" "$TXTENC_DIR/$(basename "$TXTENC_FILE")" "$TXTENC_MIN_SIZE_MB"  # [CHANGED]

# 3) VAE
dl_file "$HF_REPO_MAIN" "$VAE_FILE" "$VAE_DIR/$(basename "$VAE_FILE")" "$VAE_MIN_SIZE_MB"  # [CHANGED]

# 4) (optional) Edit model v2509
if [ "$INSTALL_EDIT" = "1" ]; then
  dl_file "$HF_REPO_MAIN" "$EDIT_FILE" "$DIFF_DIR/$(basename "$EDIT_FILE")" "$EDIT_MIN_SIZE_MB"  # [CHANGED]
fi

# 5) (optional) Example LoRA
if [ "$INSTALL_EXAMPLE_LORA" = "1" ]; then
  dl_file "$LORA_REPO" "$LORA_FILE" "$LORA_DIR/$LORA_FILE" "$LORA_MIN_SIZE_MB"  # [CHANGED]
fi

say "完成。檔案位置："
echo "  - Diffusion:     $DIFF_DIR/$(basename "$DIFF_FILE")"
echo "  - Text Encoder:  $TXTENC_DIR/$(basename "$TXTENC_FILE")"
echo "  - VAE:           $VAE_DIR/$(basename "$VAE_FILE")"
[ "$INSTALL_EDIT" = "1" ] && echo "  - Edit Model:    $DIFF_DIR/$(basename "$EDIT_FILE")"
[ "$INSTALL_EXAMPLE_LORA" = "1" ] && echo "  - Example LoRA:  $LORA_DIR/$LORA_FILE"

say "提示：如需授權標頭以提速/存取受限資源：export HUGGINGFACE_TOKEN=hf_xxx 後再執行。"
