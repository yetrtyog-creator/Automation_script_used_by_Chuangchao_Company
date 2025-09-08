#!/bin/bash
set -e

DEST_DIR=/workspace/ComfyUI/user/default/workflows/
FILE_NAME=Face-changing-MINTS.json
FILE_URL=https://raw.githubusercontent.com/yetrtyog-creator/Automation_script_used_by_Chuangchao_Company/main/%E6%8D%A2%E8%84%B8-MINTS.json

mkdir -p "$DEST_DIR"
wget -O "${DEST_DIR}${FILE_NAME}" "$FILE_URL"
echo "檔案已下載到 '${DEST_DIR}${FILE_NAME}'"

COMFYUI_DIR="/workspace/ComfyUI"
CM_CLI="$COMFYUI_DIR/custom_nodes/ComfyUI-Manager/cm-cli.py"
WORKFLOW_JSON="${DEST_DIR}${FILE_NAME}"

# 模型目錄
CHECKPOINTS="$COMFYUI_DIR/models/checkpoints"
CONTROLNET="$COMFYUI_DIR/models/controlnet"
UPSCALE="$COMFYUI_DIR/models/upscale_models"
INSTANTID="$COMFYUI_DIR/models/instantid"

# --- 1. 調整 ComfyUI-Manager 安全等級 ---
CONFIG_FILE="$COMFYUI_DIR/user/default/ComfyUI-Manager/config.ini"
if [ -f "$CONFIG_FILE" ]; then
    sed -i 's/security_level = .*/security_level = weak/' "$CONFIG_FILE"
fi

# --- 2. 安裝缺失節點 (GitHub clone) ---
git clone https://github.com/melMass/comfy_mtb "$COMFYUI_DIR/custom_nodes/comfy_mtb" || true
git clone https://github.com/evanspearman/ComfyMath "$COMFYUI_DIR/custom_nodes/ComfyMath" || true
git clone https://github.com/Suzie1/ComfyUI_Comfyroll_CustomNodes "$COMFYUI_DIR/custom_nodes/ComfyUI_Comfyroll_CustomNodes" || true
git clone https://github.com/jamesWalker55/comfyui-various "$COMFYUI_DIR/custom_nodes/comfyui-various" || true

# --- 3. 自動解析 workflow JSON 安裝節點 ---
echo "--- 自動解析 workflow 安裝缺失節點 ---"
python3 - <<PYTHON
import json, subprocess

workflow_file = "${WORKFLOW_JSON}"
with open(workflow_file, 'r', encoding='utf-8') as f:
    wf = json.load(f)

nodes = set()
for node in wf.get("nodes", []):
    node_type = node.get("type")
    if node_type:
        nodes.add(node_type)

if nodes:
    print("將安裝以下節點:", nodes)
    subprocess.run(["python3", "${CM_CLI}", "install", *nodes])
    subprocess.run(["python3", "${CM_CLI}", "fix", "all"])
else:
    print("未發現節點")
PYTHON

# --- 4. InstantID antelopev2 修復 ---
INSIGHT_DIR="$COMFYUI_DIR/models/insightface/models"
mkdir -p "$INSIGHT_DIR"
cd "$INSIGHT_DIR"
rm -rf antelopev2 antelopev2.zip
wget -O antelopev2.zip "https://github.com/deepinsight/insightface/releases/download/v0.7/antelopev2.zip"
unzip -o antelopev2.zip
rm antelopev2.zip
cd -

# --- 5. 模型下載 ---
mkdir -p "$CHECKPOINTS" "$CONTROLNET" "$UPSCALE" "$INSTANTID"
wget -nc -P "$CHECKPOINTS" "https://huggingface.co/AiWise/Juggernaut-XL-V9-GE-RDPhoto2-Lightning_4S/resolve/main/juggernautXL_v9Rdphoto2Lightning.safetensors"
wget -nc -P "$INSTANTID" "https://huggingface.co/InstantX/InstantID/resolve/main/ip-adapter.bin"
wget -nc -P "$CONTROLNET" "https://huggingface.co/InstantX/InstantID/resolve/main/ControlNetModel/diffusion_pytorch_model.safetensors"
wget -nc -P "$CONTROLNET" "https://huggingface.co/TTPlanet/TTPLanet_SDXL_Controlnet_Tile_Realistic/resolve/main/TTPLANET_Controlnet_Tile_realistic_v2_fp16.safetensors"
wget -nc -O "$UPSCALE/2xNomosUni_span_multijpg_ldl.safetensors" "https://huggingface.co/Phips/2xNomosUni_span_multijpg_ldl/resolve/main/2xNomosUni_span_multijpg_ldl.safetensors"

echo "✅ 節點與模型已安裝/修復完成，請重新啟動 ComfyUI"
