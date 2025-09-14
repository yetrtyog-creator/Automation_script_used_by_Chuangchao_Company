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

echo "[1/8] pip 安裝/更新 qdrant-client..."
pip install --upgrade qdrant-client

echo "[2/8] pip 安裝/更新 comfy-cli..."
pip install --upgrade comfy-cli

# 準備 comfy 環境
cd "$COMFYUI_DIR"
export COMFYUI_PATH="$COMFYUI_DIR"
echo "[3/8] 檢查 comfy-cli 可用性..."
python -m comfy_cli.cli --here which >/dev/null

# 取得你的 GitHub 倉庫
echo "[4/8] 取得工作流與自訂節點來源倉庫..."
if [ -d "$REPO_DIR/.git" ]; then
  git -C "$REPO_DIR" fetch --depth=1 origin main
  git -C "$REPO_DIR" reset --hard origin/main
else
  rm -rf "$REPO_DIR"
  git clone --depth=1 "$REPO_URL" "$REPO_DIR"
fi

# 建立目錄
mkdir -p "$WORKFLOWS_DIR" "$CUSTOM_NODES_DIR"

# 安裝你自製的節點
echo "[5/8] 安裝自製節點到 custom_nodes..."
cp -f "$REPO_DIR/自定義節點/TensorToListFloat_nodes.py" "$CUSTOM_NODES_DIR/TensorToListFloat_nodes.py"
cp -f "$REPO_DIR/自定義節點/qdrant_comfyui_node.py" "$CUSTOM_NODES_DIR/qdrant_comfyui_node.py"

# --- 準備 6 個工作流 ---
# API 版（原三個）
WF1_API_DST="$WORKFLOWS_DIR/Face-Swap_01_Embed-Vector_API.json"
WF2_API_DST="$WORKFLOWS_DIR/Face-Swap_02_Search-Match-Organize_API.json"
WF3_API_DST="$WORKFLOWS_DIR/Face-Swap_MINTS_API.json"

echo "[6/8] 複製 API 版工作流到 ComfyUI workflows..."
[ -f "$REPO_DIR/Face_Swap_API/換臉第一工作流(生成嵌入向量)_API.json" ] && cp -f "$REPO_DIR/Face_Swap_API/換臉第一工作流(生成嵌入向量)_API.json" "$WF1_API_DST" || echo "  - 找不到 API WF1 來源檔，已略過"
[ -f "$REPO_DIR/Face_Swap_API/換臉第二工作流(搜索匹配整理)_API.json" ] && cp -f "$REPO_DIR/Face_Swap_API/換臉第二工作流(搜索匹配整理)_API.json" "$WF2_API_DST" || echo "  - 找不到 API WF2 來源檔，已略過"
[ -f "$REPO_DIR/Face_Swap_API/换脸-MINTS_API.json" ] && cp -f "$REPO_DIR/Face_Swap_API/换脸-MINTS_API.json" "$WF3_API_DST" || echo "  - 找不到 API WF3 來源檔，已略過"

# GUI 版（新增三個，來源為你提供的三個連結所在的 repo 根目錄）
WF1_GUI_SRC="$REPO_DIR/換臉第一工作流(生成嵌入向量).json"
WF2_GUI_SRC="$REPO_DIR/換臉第二工作流(搜索匹配整理).json"
WF3_GUI_SRC="$REPO_DIR/换脸-MINTS.json"

WF1_GUI_DST="$WORKFLOWS_DIR/Face-Swap_01_Embed-Vector_GUI.json"
WF2_GUI_DST="$WORKFLOWS_DIR/Face-Swap_02_Search-Match-Organize_GUI.json"
WF3_GUI_DST="$WORKFLOWS_DIR/Face-Swap_MINTS_GUI.json"

echo "[6.1/8] 複製 GUI 版工作流到 ComfyUI workflows..."
[ -f "$WF1_GUI_SRC" ] && cp -f "$WF1_GUI_SRC" "$WF1_GUI_DST" || echo "  - 找不到 GUI WF1 來源檔，已略過"
[ -f "$WF2_GUI_SRC" ] && cp -f "$WF2_GUI_SRC" "$WF2_GUI_DST" || echo "  - 找不到 GUI WF2 來源檔，已略過"
[ -f "$WF3_GUI_SRC" ] && cp -f "$WF3_GUI_SRC" "$WF3_GUI_DST" || echo "  - 找不到 GUI WF3 來源檔，已略過"

# --- 依賴安裝：只針對 GUI 版，使用 comfy-cli ---
echo "[7/8] comfy-cli 針對 GUI 工作流安裝依賴..."
RETRIES=3
for WF in "$WF1_GUI_DST" "$WF2_GUI_DST" "$WF3_GUI_DST"; do
  [ -f "$WF" ] || { echo "  - 跳過（找不到工作流檔）：$WF"; continue; }
  echo "  - install-deps: $WF"
  ok=0
  for ((i=1; i<=RETRIES; i++)); do
    if yes "" | python -m comfy_cli.cli --here node install-deps --workflow "$WF"; then
      ok=1
      break
    else
      echo "    嘗試第 $i 次失敗，將重試..."
      sleep 2
    fi
  done
  if [ "$ok" -ne 1 ]; then
    echo "    仍無法安裝依賴：$WF"
    exit 1
  fi
done

# 節點更新（可選）
echo "[7.1/8] 嘗試更新所有節點（comfy-cli）..."
if ! python -m comfy_cli.cli --here node update all; then
  echo "  警告：節點更新可能部分失敗，繼續執行"
fi

# --- InstantID / 模型下載（原樣保留） ---
echo "[8/8] 下載/安裝 InstantID 與必要模型..."

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
  rm -f antelopev2.zip
)

download_model() {
  local url="$1"; local dest="$2"; local name="$3"
  echo "下載 $name ..."
  if wget -nc -P "$dest" "$url" || wget -nc -O "$dest/$(basename "$url")" "$url"; then
    echo "  -> 完成：$name"
  else
    echo "  -> 失敗：$name（略過）"
  fi
}

download_model \
  "https://huggingface.co/AiWise/Juggernaut-XL-V9-GE-RDPhoto2-Lightning_4S/resolve/main/juggernautXL_v9Rdphoto2Lightning.safetensors" \
  "$CHECKPOINTS" "Juggernaut XL 模型"

download_model \
  "https://huggingface.co/InstantX/InstantID/resolve/main/ip-adapter.bin" \
  "$INSTANTID" "InstantID IP-Adapter"

download_model \
  "https://huggingface.co/InstantX/InstantID/resolve/main/ControlNetModel/diffusion_pytorch_model.safetensors" \
  "$CONTROLNET" "InstantID ControlNet"

download_model \
  "https://huggingface.co/TTPlanet/TTPLanet_SDXL_Controlnet_Tile_Realistic/resolve/main/TTPLANET_Controlnet_Tile_realistic_v2_fp16.safetensors" \
  "$CONTROLNET" "TTPLANET Tile ControlNet"

wget -nc -O "$UPSCALE/2xNomosUni_span_multijpg_ldl.safetensors" \
  "https://huggingface.co/Phips/2xNomosUni_span_multijpg_ldl/resolve/main/2xNomosUni_span_multijpg_ldl.safetensors" || true

echo ""
echo "=== 完成 ==="
echo "- 新增 GUI 工作流（3）："
echo "  * $WF1_GUI_DST"
echo "  * $WF2_GUI_DST"
echo "  * $WF3_GUI_DST"
echo "- 既有 API 工作流（3）："
echo "  * $WF1_API_DST"
echo "  * $WF2_API_DST"
echo "  * $WF3_API_DST"
echo "- 自製節點："
echo "  * $CUSTOM_NODES_DIR/TensorToListFloat_nodes.py"
echo "  * $CUSTOM_NODES_DIR/qdrant_comfyui_node.py"
echo ""
echo "依賴安裝已用 comfy-cli（僅 GUI 三個）。啟動 GUI：comfy --here launch"
