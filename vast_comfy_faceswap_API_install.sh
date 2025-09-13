#!/bin/bash
set -euo pipefail

# --- 基本設定 ---
COMFYUI_DIR="/workspace/ComfyUI"

# 檢查 ComfyUI 是否存在
if [ ! -d "$COMFYUI_DIR" ]; then
    echo "❌ ComfyUI 目錄不存在: $COMFYUI_DIR"
    echo "請先安裝 ComfyUI 或檢查路徑是否正確"
    exit 1
fi

# 準備 Python（用來做 URL encoding）
PYTHON_BIN="$(command -v python3 || true)"
if [ -z "$PYTHON_BIN" ]; then
  PYTHON_BIN="$(command -v python || true)"
fi
if [ -z "$PYTHON_BIN" ]; then
  echo "❌ 找不到 python3 / python，請先安裝 Python"
  exit 1
fi

uriencode () {
  "$PYTHON_BIN" -c 'import sys, urllib.parse; print(urllib.parse.quote(sys.argv[1]))' "$1"
}

# --- 很前面安裝 qdrant-client（依你的要求） ---
echo "📦 安裝/更新 qdrant-client..."
pip install --upgrade qdrant-client

# 安裝 comfy-cli (如果尚未安裝)
echo "📦 安裝/更新 comfy-cli..."
pip install --upgrade comfy-cli

# 設定工作空間為當前 ComfyUI 目錄
cd "$COMFYUI_DIR"
export COMFYUI_PATH="$COMFYUI_DIR"

# 測試 comfy-cli 是否正常工作
echo "🔍 檢查 comfy-cli 環境..."
if comfy --here which; then
    echo "✅ comfy-cli 環境正常"
else
    echo "❌ comfy-cli 檢查失敗"
    exit 1
fi

# --- 工作流程檔案設定（3 個） ---
WORKFLOWS_DIR="$COMFYUI_DIR/user/default/workflows/"
mkdir -p "$WORKFLOWS_DIR"

# 你的 GitHub 原始檔 base
GH_USER="yetrtyog-creator"
GH_REPO="Automation_script_used_by_Chuangchao_Company"
RAW_BASE="https://raw.githubusercontent.com/${GH_USER}/${GH_REPO}/main"

# 來源目錄
WF_DIR_API="Face_Swap_API"

# 遠端實際檔名（中文原名）
WF1_FILE_CHS="換臉第一工作流(生成嵌入向量)_API.json"
WF2_FILE_CHS="換臉第二工作流(搜索匹配整理)_API.json"
WF3_FILE_CHS="换脸-MINTS_API.json"

# 本地儲存檔名（英文大意翻譯）
WF1_NAME="Face-Swap_01_Embed-Vector_API.json"
WF2_NAME="Face-Swap_02_Search-Match-Organize_API.json"
WF3_NAME="Face-Swap_MINTS_API.json"

# 以動態 URL encode 產生 raw 連結
WF1_URL="${RAW_BASE}/$(uriencode "${WF_DIR_API}/${WF1_FILE_CHS}")"
WF2_URL="${RAW_BASE}/$(uriencode "${WF_DIR_API}/${WF2_FILE_CHS}")"
WF3_URL="${RAW_BASE}/$(uriencode "${WF_DIR_API}/${WF3_FILE_CHS}")"

# 下載工作流程檔案
WORKFLOW_JSONS=()
download_wf () {
  local url="$1"
  local name="$2"
  echo "📥 下載工作流程：$name"
  if wget -O "${WORKFLOWS_DIR}${name}" "$url"; then
      echo "✅ 已下載到 '${WORKFLOWS_DIR}${name}'"
      WORKFLOW_JSONS+=("${WORKFLOWS_DIR}${name}")
  else
      echo "❌ 檔案下載失敗：$name"
      exit 1
  fi
}
download_wf "$WF1_URL" "$WF1_NAME"
download_wf "$WF2_URL" "$WF2_NAME"
download_wf "$WF3_URL" "$WF3_NAME"

# --- 1. 調整 ComfyUI-Manager 安全等級（原樣保留） ---
echo "🔧 設定 ComfyUI-Manager 安全等級..."
CONFIG_FILE="$COMFYUI_DIR/user/default/ComfyUI-Manager/config.ini"
mkdir -p "$(dirname "$CONFIG_FILE")"

if [ -f "$CONFIG_FILE" ]; then
    sed -i 's/security_level = .*/security_level = weak/' "$CONFIG_FILE"
    echo "✅ 安全等級已調整為 weak"
else
    echo "⚠️  配置檔案不存在，建立預設配置"
    cat > "$CONFIG_FILE" << EOF
[DEFAULT]
security_level = weak
EOF
fi

# --- 2. 先安裝你自寫的節點（在安裝依賴「之前」） ---
echo "📥 安裝自製節點到 custom_nodes（優先）..."
CUSTOM_NODES_DIR="$COMFYUI_DIR/custom_nodes"
mkdir -p "$CUSTOM_NODES_DIR"

TENSOR_NODE_URL="${RAW_BASE}/$(uriencode "自定義節點/TensorToListFloat_nodes.py")"
QDRANT_NODE_URL="${RAW_BASE}/$(uriencode "自定義節點/qdrant_comfyui_node.py")"

if wget -O "$CUSTOM_NODES_DIR/TensorToListFloat_nodes.py" "$TENSOR_NODE_URL"; then
    echo "✅ TensorToListFloat_nodes.py 已安裝"
else
    echo "❌ TensorToListFloat_nodes.py 下載失敗"
    exit 1
fi

if wget -O "$CUSTOM_NODES_DIR/qdrant_comfyui_node.py" "$QDRANT_NODE_URL"; then
    echo "✅ qdrant_comfyui_node.py 已安裝"
else
    echo "❌ qdrant_comfyui_node.py 下載失敗"
    exit 1
fi

# --- 3. 使用 comfy-cli 安裝缺失節點並修復（針對每個 workflow） ---
echo "📦 安裝工作流程依賴..."
for WF_JSON in "${WORKFLOW_JSONS[@]}"; do
    echo "➡️  安裝依賴：$WF_JSON"
    if comfy --here node install-deps --workflow="$WF_JSON"; then
        echo "✅ 依賴安裝完成：$WF_JSON"
    else
        echo "⚠️  依賴安裝可能有問題，但繼續執行"
    fi
done

# 嘗試更新所有節點
echo "🔧 更新節點..."
if comfy --here node update all; then
    echo "✅ 節點更新完成"
else
    echo "⚠️  節點更新可能有問題，但繼續執行"
fi

# --- 4. InstantID antelopev2 修復（原樣保留） ---
echo "📥 下載 InstantID antelopev2 模型..."
INSIGHT_DIR="$COMFYUI_DIR/models/insightface/models"
mkdir -p "$INSIGHT_DIR"
cd "$INSIGHT_DIR"

rm -rf antelopev2 antelopev2.zip
if wget -O antelopev2.zip "https://github.com/deepinsight/insightface/releases/download/v0.7/antelopev2.zip"; then
    if unzip -o antelopev2.zip; then
        rm antelopev2.zip
        echo "✅ antelopev2 模型安裝完成"
    else
        echo "❌ 解壓縮失敗"
        exit 1
    fi
else
    echo "❌ antelopev2 下載失敗"
    exit 1
fi

# 回到原目錄
cd "$COMFYUI_DIR"

# --- 5. 模型下載（原樣保留） ---
echo "📥 下載必要模型..."
CHECKPOINTS="$COMFYUI_DIR/models/checkpoints"
CONTROLNET="$COMFYUI_DIR/models/controlnet"
UPSCALE="$COMFYUI_DIR/models/upscale_models"
INSTANTID="$COMFYUI_DIR/models/instantid"

mkdir -p "$CHECKPOINTS" "$CONTROLNET" "$UPSCALE" "$INSTANTID"

download_model() {
    local url="$1"
    local dest="$2"
    local name="$3"
    echo "下載 $name..."
    if wget -nc -P "$dest" "$url" || wget -nc -O "$dest/$(basename "$url")" "$url"; then
        echo "✅ $name 下載完成"
        return 0
    else
        echo "❌ $name 下載失敗"
        return 1
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

echo "下載 Upscale 模型..."
if wget -nc -O "$UPSCALE/2xNomosUni_span_multijpg_ldl.safetensors" \
    "https://huggingface.co/Phips/2xNomosUni_span_multijpg_ldl/resolve/main/2xNomosUni_span_multijpg_ldl.safetensors"; then
    echo "✅ Upscale 模型下載完成"
else
    echo "❌ Upscale 模型下載失敗"
fi

echo ""
echo "🎉 安裝腳本執行完成！"
echo ""
echo "📋 安裝摘要："
echo "- 工作流目錄: $WORKFLOWS_DIR"
for WF_JSON in "${WORKFLOW_JSONS[@]}"; do
  echo "  * $WF_JSON"
done
echo "- ComfyUI-Manager 安全等級已設為 weak"
echo "- InstantID antelopev2 模型已安裝"
echo "- 各類 AI 模型已下載"
echo "- 使用 comfy-cli 進行依賴安裝與節點更新"
echo "- 已安裝自製節點："
echo "  * $CUSTOM_NODES_DIR/TensorToListFloat_nodes.py"
echo "  * $CUSTOM_NODES_DIR/qdrant_comfyui_node.py"
echo ""
echo "⚠️  重要提醒："
echo "1) 請重新啟動 ComfyUI 以載入新的節點和模型"
echo "2) 若節點仍異常，可再執行：comfy --here node update all"
echo ""
echo "🚀 啟動 ComfyUI："
echo "comfy --here launch"
