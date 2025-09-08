#!/bin/bash
set -e

DEST_DIR=/workspace/ComfyUI/user/default/workflows/ 
FILE_NAME=換臉-MINTS.json
FILE_URL=https://raw.githubusercontent.com/yetrtyog-creator/Automation_script_used_by_Chuangchao_Company/main/%E6%8D%A2%E8%84%B8-MINTS.json
# 創建文件夾且檢查確認
if [ ! -d "$DEST_DIR" ]; then
  mkdir -p "$DEST_DIR"
  echo "資料夾 '$DEST_DIR' 不存在，已自動創建。"
fi
# 下載工作流並到指定位置
wget -O "${DEST_DIR}${FILE_NAME}" "$FILE_URL"
echo "檔案已下載到 '${DEST_DIR}${FILE_NAME}'"
# --- 基本設定 ---
COMFYUI_DIR="/workspace/ComfyUI"
CM_CLI="$COMFYUI_DIR/custom_nodes/ComfyUI-Manager/cm-cli.py"
WORKFLOW_JSON="/workspace/ComfyUI/user/default/workflows/換臉-MINTS.json"

# 模型目錄
CHECKPOINTS="$COMFYUI_DIR/models/checkpoints"
CONTROLNET="$COMFYUI_DIR/models/controlnet"
UPSCALE="$COMFYUI_DIR/models/upscale_models"
INSTANTID="$COMFYUI_DIR/models/instantid"

# --- 1. 調整 ComfyUI-Manager 安全等級 ---
CONFIG_FILE="$COMFYUI_DIR/user/default/ComfyUI-Manager/config.ini"
if [ -f "$CONFIG_FILE" ]; then
    echo "--- 修改 ComfyUI-Manager 安全等級為 weak ---"
    sed -i 's/security_level = .*/security_level = weak/' "$CONFIG_FILE"
fi

# --- 2. 安裝缺失節點 (GitHub) ---
echo "--- 安裝缺失節點 ---"

# Note Plus (mtb)
git clone https://github.com/melMass/comfy_mtb "$COMFYUI_DIR/custom_nodes/comfy_mtb" || true

# ComfyMath (CM_* 節點)
git clone https://github.com/evanspearman/ComfyMath "$COMFYUI_DIR/custom_nodes/ComfyMath" || true

# Comfyroll (CR_* 節點)
git clone https://github.com/Suzie1/ComfyUI_Comfyroll_CustomNodes "$COMFYUI_DIR/custom_nodes/ComfyUI_Comfyroll_CustomNodes" || true

# JW Various (JW* 節點)
git clone https://github.com/jamesWalker55/comfyui-various "$COMFYUI_DIR/custom_nodes/comfyui-various" || true

# --- 3. 透過 cm-cli 安裝 workflow 依賴並修復 ---
echo "--- cm-cli 安裝 workflow 依賴 ---"
python3 "$CM_CLI" install --workflow "$WORKFLOW_JSON" || true

echo "--- cm-cli 嘗試修復 ---"
python3 "$CM_CLI" fix all || true

# --- 4. InstantID antelopev2 修復 ---
echo "--- 修復 InstantID antelopev2 ---"
INSIGHT_DIR="$COMFYUI_DIR/models/insightface/models"
mkdir -p "$INSIGHT_DIR"
cd "$INSIGHT_DIR"
rm -rf antelopev2 antelopev2.zip
wget -O antelopev2.zip "https://github.com/deepinsight/insightface/releases/download/v0.7/antelopev2.zip"
unzip -o antelopev2.zip
rm antelopev2.zip
cd -

# --- 5. 模型下載 ---
echo "--- 下載模型 ---"
mkdir -p "$CHECKPOINTS" "$CONTROLNET" "$UPSCALE" "$INSTANTID"

# Checkpoint: JuggernautXL
wget -nc -P "$CHECKPOINTS" \
"https://huggingface.co/AiWise/Juggernaut-XL-V9-GE-RDPhoto2-Lightning_4S/resolve/main/juggernautXL_v9Rdphoto2Lightning.safetensors"

# InstantID: ip-adapter
wget -nc -P "$INSTANTID" \
"https://huggingface.co/InstantX/InstantID/resolve/main/ip-adapter.bin"

# InstantID: ControlNet model
wget -nc -P "$CONTROLNET" \
"https://huggingface.co/InstantX/InstantID/resolve/main/ControlNetModel/diffusion_pytorch_model.safetensors"

# TTPLANET ControlNet Tile
wget -nc -P "$CONTROLNET" \
"https://huggingface.co/TTPlanet/TTPLanet_SDXL_Controlnet_Tile_Realistic/resolve/main/TTPLANET_Controlnet_Tile_realistic_v2_fp16.safetensors"

# Upscaler (注意副檔名修正為 .safetensors)
wget -nc -O "$UPSCALE/2xNomosUni_span_multijpg_ldl.safetensors" \
"https://huggingface.co/Phips/2xNomosUni_span_multijpg_ldl/resolve/main/2xNomosUni_span_multijpg_ldl.safetensors"

echo "--- 模型下載完成 ---"

# --- 6. 提示完成 ---
echo "✅ 節點與模型已安裝/修復完成，請重新啟動 ComfyUI"
