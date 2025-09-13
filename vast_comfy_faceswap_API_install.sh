#!/usr/bin/env bash
set -Eeuo pipefail

# ---------------------------
# 基本環境與變數
# ---------------------------
export LANG=C.UTF-8
export LC_ALL=C.UTF-8

COMFYUI_DIR="/workspace/ComfyUI"
WORKFLOWS_DIR="$COMFYUI_DIR/user/default/workflows"
CUSTOM_NODES_DIR="$COMFYUI_DIR/custom_nodes"
REPO_URL="https://github.com/yetrtyog-creator/Automation_script_used_by_Chuangchao_Company.git"
REPO_DIR="/tmp/Automation_script_used_by_Chuangchao_Company"

# 確保常見的 user/local 路徑在 PATH 內（comfy-cli 安裝位置常見）
export PATH="$HOME/.local/bin:/usr/local/bin:/usr/bin:$PATH"

echo "[0/10] 檢查 ComfyUI 目錄 ..."
if [[ ! -d "$COMFYUI_DIR" ]]; then
  echo "!! ComfyUI 目錄不存在：$COMFYUI_DIR"
  echo "   請先安裝 ComfyUI 或檢查路徑是否正確"
  exit 1
fi

# ---------------------------
# 系統工具（git/wget/unzip）
# ---------------------------
echo "[1/10] 檢查並安裝系統必要工具 (git, wget, unzip) ..."
need_pkg() { command -v "$1" >/dev/null 2>&1 || return 0 && return 1; }
install_pkg() {
  if command -v apt-get >/dev/null 2>&1; then
    sudo apt-get update -y && sudo apt-get install -y "$@"
  elif command -v yum >/devnull 2>&1; then
    sudo yum install -y "$@"
  elif command -v dnf >/dev/null 2>&1; then
    sudo dnf install -y "$@"
  elif command -v pacman >/dev/null 2>&1; then
    sudo pacman -Sy --noconfirm "$@"
  else
    echo "!! 找不到套件管理器安裝 $*，請手動安裝後重跑。"
  fi
}
MISSING=()
for c in git wget unzip; do
  if ! command -v "$c" >/dev/null 2>&1; then MISSING+=("$c"); fi
done
if [[ ${#MISSING[@]} -gt 0 ]]; then
  echo "   缺少：${MISSING[*]}，嘗試安裝..."
  install_pkg "${MISSING[@]}" || true
fi

# ---------------------------
# Python / pip
# ---------------------------
echo "[2/10] 升級 pip/setuptools/wheel ..."
PYBIN="$(command -v python3 || true)"
if [[ -z "${PYBIN}" ]]; then
  echo "!! 找不到 python3，請先安裝 Python 3"; exit 1
fi
"${PYBIN}" -m pip install --upgrade pip setuptools wheel

# ---------------------------
# 安裝 Python 依賴（qdrant-client、comfy-cli）
# ---------------------------
echo "[3/10] 安裝 qdrant-client 與 comfy-cli ..."
"${PYBIN}" -m pip install --upgrade "qdrant-client>=1.9.0" "comfy-cli>=0.8.0" || "${PYBIN}" -m pip install --user --upgrade "qdrant-client>=1.9.0" "comfy-cli>=0.8.0"

# ---------------------------
# comfy-cli 檢查與定位
# ---------------------------
echo "[4/10] 檢查 comfy-cli 指令 ..."
COMFY_BIN="$(command -v comfy || true)"
if [[ -z "${COMFY_BIN}" ]]; then
  # 常見安裝位置補一次 PATH
  export PATH="$HOME/.local/bin:/usr/local/bin:/usr/bin:$PATH"
  COMFY_BIN="$(command -v comfy || true)"
fi
if [[ -z "${COMFY_BIN}" ]]; then
  echo "!! 無法找到 comfy 指令（comfy-cli）。請確認 pip 安裝是否成功，或將 ~/.local/bin 加入 PATH 後重試。"
  exit 1
fi

# ---------------------------
# 取得 Repo（避免中文 URL 問題）
# ---------------------------
echo "[5/10] 取得工作流與自訂節點來源倉庫 ..."
if [[ -d "$REPO_DIR/.git" ]]; then
  git -C "$REPO_DIR" fetch --depth=1 origin main
  git -C "$REPO_DIR" reset --hard origin/main
else
  rm -rf "$REPO_DIR"
  git clone --depth=1 "$REPO_URL" "$REPO_DIR"
fi

# ---------------------------
# 建立目錄與複製自製節點
# ---------------------------
echo "[6/10] 準備 custom_nodes 與 workflows 目錄 ..."
mkdir -p "$WORKFLOWS_DIR" "$CUSTOM_NODES_DIR"

echo "       複製自製節點（優先放入，避免後續依賴解析不到）..."
COPIED_ANY=0
if [[ -f "$REPO_DIR/自定義節點/TensorToListFloat_nodes.py" ]]; then
  cp -f "$REPO_DIR/自定義節點/TensorToListFloat_nodes.py" \
        "$CUSTOM_NODES_DIR/TensorToListFloat_nodes.py"
  COPIED_ANY=1
else
  echo "  !! 找不到 TensorToListFloat_nodes.py（將略過，但工作流若依賴可能失敗）"
fi
if [[ -f "$REPO_DIR/自定義節點/qdrant_comfyui_node.py" ]]; then
  cp -f "$REPO_DIR/自定義節點/qdrant_comfyui_node.py" \
        "$CUSTOM_NODES_DIR/qdrant_comfyui_node.py"
  COPIED_ANY=1
else
  echo "  !! 找不到 qdrant_comfyui_node.py（將略過，但工作流若依賴可能失敗）"
fi
if [[ "$COPIED_ANY" -eq 0 ]]; then
  echo "  !! 無自製節點被複製，請檢查 repo 目錄結構。"
fi

# ---------------------------
# 複製三個工作流
# ---------------------------
WF1_SRC="$REPO_DIR/Face_Swap_API/換臉第一工作流(生成嵌入向量)_API.json"
WF2_SRC="$REPO_DIR/Face_Swap_API/換臉第二工作流(搜索匹配整理)_API.json"
WF3_SRC="$REPO_DIR/Face_Swap_API/换脸-MINTS_API.json"

WF1_DST="$WORKFLOWS_DIR/Face-Swap_01_Embed-Vector_API.json"
WF2_DST="$WORKFLOWS_DIR/Face-Swap_02_Search-Match-Organize_API.json"
WF3_DST="$WORKFLOWS_DIR/Face-Swap_MINTS_API.json"

echo "[7/10] 複製工作流到 ComfyUI workflows ..."
copy_wf() {
  local src="$1"; local dst="$2"; local name="$3"
  if [[ -f "$src" ]]; then
    cp -f "$src" "$dst"
    echo "   - $name -> $dst"
  else
    echo "  !! 找不到來源工作流：$src"
  fi
}
copy_wf "$WF1_SRC" "$WF1_DST" "第一工作流 (Embed-Vector)"
copy_wf "$WF2_SRC" "$WF2_DST" "第二工作流 (Search-Match-Organize)"
copy_wf "$WF3_SRC" "$WF3_DST" "MINTS"

# ---------------------------
# comfy-cli：安裝依賴與更新節點
# ---------------------------
echo "[8/10] 使用 comfy-cli 依工作流安裝節點依賴 ..."
cd "$COMFYUI_DIR"
export COMFYUI_PATH="$COMFYUI_DIR"

for WF in "$WF1_DST" "$WF2_DST" "$WF3_DST"; do
  if [[ -f "$WF" ]]; then
    echo "   - comfy node install-deps --workflow='$WF'"
    if ! comfy --here node install-deps --workflow="$WF"; then
      echo "     !! 警告：此工作流依賴安裝部分失敗，將繼續流程"
    fi
  fi
done

echo "       嘗試更新所有節點（非致命） ..."
if ! comfy --here node update all; then
  echo "     !! 警告：節點更新可能部分失敗，將繼續流程"
fi

# ComfyUI-Manager 安全等級（若存在 manager 子命令）
if comfy --here manager --help >/dev/null 2>&1; then
  comfy --here manager set-security-level weak || true
fi

# ---------------------------
# 安裝 InsightFace / ONNXRuntime
# ---------------------------
echo "[9/10] 安裝 InsightFace 與 ONNXRuntime ..."
if command -v nvidia-smi >/dev/null 2>&1; then
  echo "   偵測到 GPU，優先安裝 onnxruntime-gpu ..."
  if ! "${PYBIN}" -m pip install --upgrade onnxruntime-gpu; then
    echo "   onnxruntime-gpu 安裝失敗，改裝 onnxruntime（CPU）"
    "${PYBIN}" -m pip install --upgrade onnxruntime || true
  fi
else
  "${PYBIN}" -m pip install --upgrade onnxruntime || true
fi
# InsightFace 本身會抓取相依（含 onnxruntime），這裡保守升級
"${PYBIN}" -m pip install --upgrade "insightface>=0.7.3" || true

# ---------------------------
# 下載 / 安裝模型
# ---------------------------
echo "[10/10] 下載 InstantID / antelopev2 與其他模型 ..."
INSIGHT_DIR="$COMFYUI_DIR/models/insightface/models"
CHECKPOINTS="$COMFYUI_DIR/models/checkpoints"
CONTROLNET="$COMFYUI_DIR/models/controlnet"
UPSCALE="$COMFYUI_DIR/models/upscale_models"
INSTANTID="$COMFYUI_DIR/models/instantid"

mkdir -p "$INSIGHT_DIR" "$CHECKPOINTS" "$CONTROLNET" "$UPSCALE" "$INSTANTID"

echo "   - 下載 antelopev2 ..."
(
  set -Eeuo pipefail
  cd "$INSIGHT_DIR"
  rm -rf antelopev2 antelopev2.zip || true
  wget -q --show-progress -O antelopev2.zip \
    "https://github.com/deepinsight/insightface/releases/download/v0.7/antelopev2.zip" || true
  if [[ -f antelopev2.zip ]]; then
    unzip -o antelopev2.zip >/dev/null && rm -f antelopev2.zip || true
  else
    echo "     !! antelopev2.zip 下載失敗（可稍後手動補）"
  fi
)

download_model() {
  local url="$1"; local dest="$2"; local name="$3"
  mkdir -p "$dest"
  echo "   - 下載 $name ..."
  # 優先使用 -nc 斷點續傳；失敗則再試一次
  if ! wget -q --show-progress -nc -P "$dest" "$url"; then
    wget -q --show-progress -O "$dest/$(basename "$url")" "$url" || true
  fi
  if [[ -f "$dest/$(basename "$url")" ]]; then
    echo "     -> 完成：$name"
  else
    # 有些 HF 會以 content-disposition 命名；再嘗試一次不指定名稱
    if wget -q --show-progress --content-disposition -P "$dest" "$url"; then
      echo "     -> 完成（以 Content-Disposition 命名）：$name"
    else
      echo "     !! 失敗：$name（先略過）"
    fi
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

# Upscale（容錯）
wget -q --show-progress -nc -O "$UPSCALE/2xNomosUni_span_multijpg_ldl.safetensors" \
  "https://huggingface.co/Phips/2xNomosUni_span_multijpg_ldl/resolve/main/2xNomosUni_span_multijpg_ldl.safetensors" || true

echo ""
echo "=== 完成 ==="
echo "- 工作流:"
[[ -f "$WF1_DST" ]] && echo "  * $WF1_DST" || echo "  * (缺少) $WF1_DST"
[[ -f "$WF2_DST" ]] && echo "  * $WF2_DST" || echo "  * (缺少) $WF2_DST"
[[ -f "$WF3_DST" ]] && echo "  * $WF3_DST" || echo "  * (缺少) $WF3_DST"
echo "- 自製節點:"
[[ -f "$CUSTOM_NODES_DIR/TensorToListFloat_nodes.py" ]] && echo "  * $CUSTOM_NODES_DIR/TensorToListFloat_nodes.py" || echo "  * (缺少) TensorToListFloat_nodes.py"
[[ -f "$CUSTOM_NODES_DIR/qdrant_comfyui_node.py" ]] && echo "  * $CUSTOM_NODES_DIR/qdrant_comfyui_node.py" || echo "  * (缺少) qdrant_comfyui_node.py"
echo ""
echo "若要啟動 ComfyUI（於 $COMFYUI_DIR）:"
echo "  cd \"$COMFYUI_DIR\" && comfy --here launch"
