#!/bin/bash
set -e

# --- 基本設定 ---
COMFYUI_DIR="/workspace/ComfyUI"

# 檢查 ComfyUI 是否存在
if [ ! -d "$COMFYUI_DIR" ]; then
    echo "❌ ComfyUI 目錄不存在: $COMFYUI_DIR"
    echo "請先安裝 ComfyUI 或檢查路徑是否正確"
    exit 1
fi

# 設定 Python 路徑並測試
export PYTHONPATH="$COMFYUI_DIR:$PYTHONPATH"
cd "$COMFYUI_DIR"

# 嘗試導入 ComfyUI 模組（使用更安全的方式）
echo "🔍 檢查 ComfyUI 環境..."
if python3 -c "import sys; sys.path.insert(0, '$COMFYUI_DIR'); import comfy.cli_args; print('ComfyUI 模組檢查通過')" 2>/dev/null; then
    echo "✅ ComfyUI 環境正常"
else
    echo "⚠️  ComfyUI 模組檢查失敗，但繼續執行..."
    echo "這可能表示 ComfyUI 尚未完全安裝或路徑問題"
fi

# 工作流程檔案設定
DEST_DIR="$COMFYUI_DIR/user/default/workflows/"
FILE_NAME="Face-changing-MINTS.json"
FILE_URL="https://raw.githubusercontent.com/yetrtyog-creator/Automation_script_used_by_Chuangchao_Company/main/%E6%8D%A2%E8%84%B8-MINTS.json"
WORKFLOW_JSON="${DEST_DIR}${FILE_NAME}"

# 下載工作流程檔案
echo "📥 下載工作流程檔案..."
mkdir -p "$DEST_DIR"
if wget -O "${DEST_DIR}${FILE_NAME}" "$FILE_URL"; then
    echo "✅ 檔案已下載到 '${DEST_DIR}${FILE_NAME}'"
else
    echo "❌ 檔案下載失敗"
    exit 1
fi

# ComfyUI Manager CLI 路徑
CM_CLI="$COMFYUI_DIR/custom_nodes/ComfyUI-Manager/cm-cli.py"

# 檢查 ComfyUI Manager 是否存在
if [ ! -f "$CM_CLI" ]; then
    echo "❌ ComfyUI-Manager 未找到: $CM_CLI"
    echo "請先安裝 ComfyUI-Manager"
    exit 1
fi

# 模型目錄設定
CHECKPOINTS="$COMFYUI_DIR/models/checkpoints"
CONTROLNET="$COMFYUI_DIR/models/controlnet"
UPSCALE="$COMFYUI_DIR/models/upscale_models"
INSTANTID="$COMFYUI_DIR/models/instantid"

# --- 1. 調整 ComfyUI-Manager 安全等級 ---
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

# --- 2. 安裝缺失節點並修復 ---
echo "📦 安裝工作流程依賴..."
cd "$COMFYUI_DIR"

# 安裝 workflow 依賴（添加錯誤處理）
if python3 "$CM_CLI" install-deps --workflow="$WORKFLOW_JSON"; then
    echo "✅ 依賴安裝完成"
else
    echo "⚠️  依賴安裝可能有問題，但繼續執行"
fi

# 嘗試修復所有節點
echo "🔧 修復節點..."
if python3 "$CM_CLI" fix all --workflow="$WORKFLOW_JSON"; then
    echo "✅ 節點修復完成"
else
    echo "⚠️  節點修復可能有問題，但繼續執行"
fi

# --- 3. InstantID antelopev2 修復 ---
echo "📥 下載 InstantID antelopev2 模型..."
INSIGHT_DIR="$COMFYUI_DIR/models/insightface/models"
mkdir -p "$INSIGHT_DIR"
cd "$INSIGHT_DIR"

# 清理舊檔案
rm -rf antelopev2 antelopev2.zip

# 下載並解壓
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

# --- 4. 模型下載 ---
echo "📥 下載必要模型..."
mkdir -p "$CHECKPOINTS" "$CONTROLNET" "$UPSCALE" "$INSTANTID"

# 定義下載函式
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

# 下載各種模型
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

# 特殊處理 upscale 模型（需要重新命名）
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
echo "- 工作流程檔案: $WORKFLOW_JSON"
echo "- ComfyUI-Manager 安全等級已設為 weak"
echo "- InstantID antelopev2 模型已安裝"
echo "- 各類 AI 模型已下載"
echo ""
echo "⚠️  重要提醒："
echo "1. 請重新啟動 ComfyUI 以載入新的節點和模型"
echo "2.
