#!/usr/bin/env bash
# v2 — robust comfy-cli detection, no fragile "which", safer piping, idempotent

set -Eeuo pipefail
umask 022
export LC_ALL=C.UTF-8 LANG=C.UTF-8
trap 'echo "❌ 失敗（行 $LINENO）: $BASH_COMMAND" >&2' ERR

# --- 基本設定 ---
COMFYUI_DIR="${COMFYUI_DIR:-/workspace/ComfyUI}"
WORKFLOWS_DIR="$COMFYUI_DIR/user/default/workflows"
CUSTOM_NODES_DIR="$COMFYUI_DIR/custom_nodes"
REPO_URL="${REPO_URL:-https://github.com/yetrtyog-creator/Automation_script_used_by_Chuangchao_Company.git}"
REPO_DIR="${REPO_DIR:-/tmp/Automation_script_used_by_Chuangchao_Company}"

# --- 前置檢查 ---
if [[ ! -d "$COMFYUI_DIR" ]]; then
  echo "ComfyUI 目錄不存在: $COMFYUI_DIR"
  echo "請先安裝 ComfyUI 或檢查路徑是否正確"
  exit 1
fi

# 找到 python 與 pip
PY="$(command -v python3 || true)"
[[ -z "${PY}" ]] && PY="$(command -v python || true)"
if [[ -z "${PY}" ]]; then
  echo "找不到 python3/python，請先安裝 Python。"; exit 1
fi
PIP="$PY -m pip"

# 小工具確保可用（若環境允許 apt）
ensure_tool() {
  local bin="$1"
  if ! command -v "$bin" >/dev/null 2>&1; then
    if command -v apt-get >/dev/null 2>&1; then
      apt-get update -y && apt-get install -y "$bin" || true
    fi
  fi
}
ensure_tool git
ensure_tool wget
ensure_tool unzip

echo "[1/8] 安裝/更新 Python 套件（qdrant-client, comfy-cli）..."
$PIP install --upgrade --no-input qdrant-client comfy-cli
hash -r || true  # 刷新 shell command hash

# 以 module 方式執行 comfy-cli，比直接呼叫 'comfy' 更穩健
if command -v comfy >/dev/null 2>&1; then
  COMFY="comfy"
else
  COMFY="$PY -m comfy_cli"
fi

cd "$COMFYUI_DIR"
export COMFYUI_PATH="$COMFYUI_DIR"

echo "[2/8] 檢查 comfy-cli 可用性..."
# 用 --version / --help 作煙霧測試，避免不存在的子命令造成非零碼退出
$COMFY --version >/dev/null 2>&1 || $COMFY --help >/dev/null

echo "[3/8] 取得工作流與自訂節點來源倉庫..."
if [[ -d "$REPO_DIR/.git" ]]; then
  git -C "$REPO_DIR" fetch --depth=1 origin main
  git -C "$REPO_DIR" reset --hard origin/main
else
  rm -rf "$REPO_DIR"
  git clone --depth=1 "$REPO_URL" "$REPO_DIR"
fi

echo "[4/8] 建立目錄並安裝自製節點..."
mkdir -p "$WORKFLOWS_DIR" "$CUSTOM_NODES_DIR"

copy_if_exists() {
  local src="$1"; local dst="$2"
  if [[ -f "$src" ]]; then
    cp -f "$src" "$dst"
    echo "  + $(basename "$dst")"
  else
    echo "  - 缺檔：$src（略過）"
  fi
}

copy_if_exists "$REPO_DIR/自定義節點/TensorToListFloat_nodes.py" "$CUSTOM_NODES_DIR/TensorToListFloat_nodes.py"
copy_if_exists "$REPO_DIR/自定義節點/qdrant_comfyui_node.py"      "$CUSTOM_NODES_DIR/qdrant_comfyui_node.py"

echo "[5/8] 複製工作流（API 版本：第零、第一、第二、MINTS）..."
# API 版
WF0_API_DST="$WORKFLOWS_DIR/Face-Swap_00_Create-Database_API.json"
WF1_API_DST="$WORKFLOWS_DIR/Face-Swap_01_Embed-Vector_API.json"
WF2_API_DST="$WORKFLOWS_DIR/Face-Swap_02_Search-Match-Organize_API.json"
WF3_API_DST="$WORKFLOWS_DIR/Face-Swap_MINTS_API.json"
copy_if_exists "$REPO_DIR/Face_Swap_API/第零階段工作流_API.json"                     "$WF0_API_DST"
copy_if_exists "$REPO_DIR/Face_Swap_API/換臉第一工作流(生成嵌入向量)_API.json"       "$WF1_API_DST"
copy_if_exists "$REPO_DIR/Face_Swap_API/換臉第二工作流(搜索匹配整理)_API.json"         "$WF2_API_DST"
copy_if_exists "$REPO_DIR/Face_Swap_API/换脸-MINTS_API.json"                            "$WF3_API_DST"

# GUI 版（保持原有的三個工作流）
WF1_GUI_SRC="$REPO_DIR/換臉第一工作流(生成嵌入向量).json"
WF2_GUI_SRC="$REPO_DIR/換臉第二工作流(搜索匹配整理).json"
WF3_GUI_SRC="$REPO_DIR/换脸-MINTS.json"
WF1_GUI_DST="$WORKFLOWS_DIR/Face-Swap_01_Embed-Vector_GUI.json"
WF2_GUI_DST="$WORKFLOWS_DIR/Face-Swap_02_Search-Match-Organize_GUI.json"
WF3_GUI_DST="$WORKFLOWS_DIR/Face-Swap_MINTS_GUI.json"
copy_if_exists "$WF1_GUI_SRC" "$WF1_GUI_DST"
copy_if_exists "$WF2_GUI_SRC" "$WF2_GUI_DST"
copy_if_exists "$WF3_GUI_SRC" "$WF3_GUI_DST"

echo "[6/8] 安裝 GUI 工作流依賴（comfy-cli）..."
install_deps() {
  local wf="$1"
  [[ -f "$wf" ]] || { echo "  - 跳過（找不到）：$wf"; return 0; }
  local tries=3
  for ((i=1;i<=tries;i++)); do
    echo "  - install-deps: $wf (嘗試 $i/$tries)"
    # 用 here-string 餵入 Enter，避免 yes/pipe 在嚴格模式下造成非零退出
    if $COMFY --here node install-deps --workflow "$wf" <<< $'\n'; then
      return 0
    fi
    sleep 2
  done
  echo "  - 仍無法安裝依賴：$wf"; return 1
}
install_deps "$WF1_GUI_DST"
install_deps "$WF2_GUI_DST"
install_deps "$WF3_GUI_DST"

echo "[7/8] 嘗試更新所有節點（comfy-cli）..."
$COMFY --here node update all || echo "  - 節點更新失敗（忽略）"

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
  wget -q -O antelopev2.zip "https://github.com/deepinsight/insightface/releases/download/v0.7/antelopev2.zip"
  unzip -o antelopev2.zip
  rm -f antelopev2.zip
)

download_model() {
  local url="$1"; local dest="$2"; local name="$3"
  echo "  - 下載 $name ..."
  mkdir -p "$dest"
  if wget -q -nc -P "$dest" "$url" || wget -q -nc -O "$dest/$(basename "$url")" "$url"; then
    echo "    -> 完成：$name"
  else
    echo "    -> 失敗：$name（略過）"
  fi
}

download_model "https://huggingface.co/AiWise/Juggernaut-XL-V9-GE-RDPhoto2-Lightning_4S/resolve/main/juggernautXL_v9Rdphoto2Lightning.safetensors" "$CHECKPOINTS" "Juggernaut XL"
download_model "https://huggingface.co/InstantX/InstantID/resolve/main/ip-adapter.bin" "$INSTANTID" "InstantID IP-Adapter"
download_model "https://huggingface.co/InstantX/InstantID/resolve/main/ControlNetModel/diffusion_pytorch_model.safetensors" "$CONTROLNET" "InstantID ControlNet"
download_model "https://huggingface.co/TTPlanet/TTPLanet_SDXL_Controlnet_Tile_Realistic/resolve/main/TTPLANET_Controlnet_Tile_realistic_v2_fp16.safetensors" "$CONTROLNET" "TTPLANET Tile ControlNet"
wget -q -nc -O "$UPSCALE/2xNomosUni_span_multijpg_ldl.safetensors" \
  "https://huggingface.co/Phips/2xNomosUni_span_multijpg_ldl/resolve/main/2xNomosUni_span_multijpg_ldl.safetensors" || true

echo
echo "=== 完成 ==="
echo "- GUI 工作流："
echo "  * $WF1_GUI_DST"
echo "  * $WF2_GUI_DST"
echo "  * $WF3_GUI_DST"
echo "- API 工作流："
echo "  * $WF0_API_DST"
echo "  * $WF1_API_DST"
echo "  * $WF2_API_DST"
echo "  * $WF3_API_DST"
echo "- 自製節點："
echo "  * $CUSTOM_NODES_DIR/TensorToListFloat_nodes.py"
echo "  * $CUSTOM_NODES_DIR/qdrant_comfyui_node.py"
echo
echo "可啟動 GUI：$COMFY --here launch"
