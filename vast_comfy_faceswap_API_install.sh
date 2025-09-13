#!/bin/bash
set -e

# --- 基本設定 ---
COMFYUI_DIR="/workspace/ComfyUI"
WORKFLOWS_DIR="$COMFYUI_DIR/user/default/workflows"
CUSTOM_NODES_DIR="$COMFYUI_DIR/custom_nodes"
REPO_URL="https://github.com/yetrtyog-creator/Automation_script_used_by_Chuangchao_Company.git"
REPO_DIR="/tmp/Automation_script_used_by_Chuangchao_Company"

# 檢查 ComfyUI 是否存在
if [ ! -d "$COMFYUI_DIR" ]; then
    echo "ComfyUI 目錄不存在: $COMFYUI_DIR"
    echo "請先安裝 ComfyUI 或檢查路徑是否正確"
    exit 1
fi

# --- 很前面安裝 qdrant-client（依你的要求） ---
echo "[1/8] pip 安裝 qdrant-client..."
pip install --upgrade qdrant-client

# 安裝/更新 comfy-cli
echo "[2/8] pip 安裝 comfy-cli..."
pip install --upgrade comfy-cli

# 設定工作空間為 ComfyUI 目錄並檢查 comfy-cli
cd "$COMFYUI_DIR"
export COMFYUI_PATH="$COMFYUI_DIR"
echo "[3/8] 檢查 comfy-cli..."
comfy --here which >/dev/null

# --- 取得你的 GitHub 倉庫（避免中文 URL 編碼問題） ---
echo "[4/8] 取得工作流與自訂節點來源倉庫..."
if [ -d "$REPO_DIR/.git" ]; then
  git -C "$REPO_DIR" fetch --depth=1 origin main
  git -C "$REPO_DIR" reset --hard origin/main
else
  rm -rf "$REPO_DIR"
  git clone --depth=1 "$REPO_URL" "$REPO_DIR"
fi

# --- 建立目錄 ---
mkdir -p "$WORKFLOWS_DIR" "$CUSTOM_NODES_DIR"

# --- 先安裝你自寫的節點（在安裝依賴之前） ---
echo "[5/8] 安裝自製節點到 custom_nodes（優先）..."
cp -f "$REPO_DIR/自定義節點/TensorToListFloat_nodes.py" "$CUSTOM_NODES_DIR/TensorToListFloat_nodes.py"
cp -f "$REPO_DIR/自定義節點/qdrant_comfyui_node.py" "$CUSTOM_NODES_DIR/qdrant_comfyui_node.py"

# --- 三個工作流（來源檔：Face_Swap_API 目錄） ---
# 目的地檔名（英文翻譯，便於辨識）
WF1_DST="$WORKFLOWS_DIR/Face-Swap_01_Embed-Vector_API.json"
WF2_DST="$WORKFLOWS_DIR/Face-Swap_02_Search-Match-Organize_API.json"
WF3_DST="$WORKFLOWS_DIR/Face-Swap_MINTS_API.json"

echo "[6/8] 複製三個工作流到 ComfyUI workflows..."
cp -f "$REPO_DIR/Face_Swap_API/換臉第一工作流(生成嵌入向量)_API.json" "$WF1_DST"
cp -f "$REPO_DIR/Face_Swap_API/換臉第二工作流(搜索匹配整理)_API.json" "$WF2_DST"
cp -f "$REPO_DIR/Face_Swap_API/换脸-MINTS_API.json"                     "$WF3_DST"

# --- 用 comfy-cli 針對每個工作流安裝依賴 ---
echo "[7/8] comfy-cli 安裝工作流依賴並更新節點..."
for WF in "$WF1_DST" "$WF2_DST" "$WF3_DST"; do
  echo "  - install-deps: $WF"
  if ! comfy --here node install-deps --workflow="$WF"; then
    echo "    警告：依賴安裝可能部分失敗，繼續執行"
  fi
done

# 嘗試更新所有節點
if ! comfy --here node update all; then
  echo "  警告：節點更新可能部分失敗，繼續執行"
fi

# --- InstantID antelopev2 修復與模型下載（原樣保留） ---
echo "[8/8] 下載/安裝 InstantID antelopev2 與必要模型..."

INSIGHT_DIR="$COMFYUI_DIR/models/insightface/models"
CHECKPOINTS="$COMFYUI_DIR/models/checkpoints"
CONTROLNET="$COMFYUI_DIR/models/controlnet"
UPSCALE="$COMFYUI_DIR/models/upscale_models"
INSTANTID="$COMFYUI_DIR/models/instantid"

mkdir -p "$INSIGHT_DIR" "$CHECKPOINTS" "$CONTROLNET" "$UPSCALE" "$INSTANTID"

# antelopev2
(
  cd "$INSIGHT_DIR"
  rm -rf antelopev2 antelopev2.zip
  wget -O antelopev2.zip "https://github.com/deepinsight/insightface/releases/download/v0.7/antelopev2.zip"
  unzip -o antelopev2.zip
  rm antelopev2.zip
)

# 小工具：模型下載（與你原本一致）
download_model() {
  local url="$1"
  local dest="$2"
  local name="$3"
  echo "下載 $name ..."
  if wget -nc -P "$dest" "$url" || wget -nc -O "$dest/$(basename "$url")" "$url"; then
    echo "  -> 完成：$name"
  else
    echo "  -> 失敗：$name（略過）"
  fi
}

download_model \
  "https://huggingface.co/AiWise/Juggernaut-XL-V9-GE-RDPhoto2-Lightning_4S/resolve/main/juggernautXL_v9Rdphoto2Lightning.safetensors" \
  "$CHECKPOINTS" \
  "Juggernaut XL 模型"

download_model \
  "https://huggingface.co/InstantX/InstantID/resolve/main/ip-adapter.bin" \
  "$INSTANTID" \
  "InstantID IP-Adapter"

download_model \
  "https://huggingface.co/InstantX/InstantID/resolve/main/ControlNetModel/diffusion_pytorch_model.safetensors" \
  "$CONTROLNET" \
  "InstantID ControlNet"

download_model \
  "https://huggingface.co/TTPlanet/TTPLanet_SDXL_Controlnet_Tile_Realistic/resolve/main/TTPLANET_Controlnet_Tile_realistic_v2_fp16.safetensors" \
  "$CONTROLNET" \
  "TTPLANET Tile ControlNet"

# Upscale
wget -nc -O "$UPSCALE/2xNomosUni_span_multijpg_ldl.safetensors" \
  "https://huggingface.co/Phips/2xNomosUni_span_multijpg_ldl/resolve/main/2xNomosUni_span_multijpg_ldl.safetensors" || true

echo ""
echo "=== 完成 ==="
echo "- 工作流:"
echo "  * $WF1_DST"
echo "  * $WF2_DST"
echo "  * $WF3_DST"
echo "- 自製節點:"
echo "  * $CUSTOM_NODES_DIR/TensorToListFloat_nodes.py"
echo "  * $CUSTOM_NODES_DIR/qdrant_comfyui_node.py"
echo "- ComfyUI-Manager 安全等級：weak（如你原腳本）"
echo ""
echo "重啟 ComfyUI 後執行：comfy --here launch"
