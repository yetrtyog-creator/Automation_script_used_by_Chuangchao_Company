#!/usr/bin/env bash
set -euo pipefail

# vast_search.sh — 主程式（已重構為多檔案）
# 使用：
#   ./vast_search.sh -c config.yaml [--dry-run] [--list-ids] [--print-full-cmd]
#
# 只會依照 config.yaml 內容來組合查詢，不會自作主張加入 external/rentable/verified/rented 等條件。

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LIB_DIR="$SCRIPT_DIR/lib"

# shellcheck source=lib/utils.sh
. "$LIB_DIR/utils.sh"

CONFIG="config.yaml"
DRY_RUN="false"
LIST_IDS="false"
PRINT_CMD="false"

while [[ $# -gt 0 ]]; do
  case "$1" in
    -c|--config) CONFIG="$2"; shift 2 ;;
    --dry-run) DRY_RUN="true"; shift ;;
    --list-ids) LIST_IDS="true"; shift ;;
    --print-full-cmd) PRINT_CMD="true"; shift ;;
    *) echo "Unknown arg: $1" >&2; exit 1 ;;
  esac
done

[[ -f "$CONFIG" ]] || { echo "Config not found: $CONFIG" >&2; exit 1; }

ensure_cli
ensure_tools
ensure_pyyaml

# 產生 META_JSON
META_JSON="$("$SCRIPT_DIR/build_meta.py" "$CONFIG")"

# 取設定
QUERY="$(jq -r '.query' <<<"$META_JSON")"
ORDER="$(jq -r '.order' <<<"$META_JSON")"
USE_TOTAL="$(jq -r '.price.use_dph_total' <<<"$META_JSON")"
P_MIN="$(jq -r '.price.min' <<<"$META_JSON")"
P_MAX="$(jq -r '.price.max' <<<"$META_JSON")"
BW_DOWN_MIN="$(jq -r '.bandwidth_min.down' <<<"$META_JSON")"
BW_UP_MIN="$(jq -r '.bandwidth_min.up' <<<"$META_JSON")"
API_KEY="$(jq -r '.auth.api_key' <<<"$META_JSON")"
API_PERSIST="$(jq -r '.auth.persist' <<<"$META_JSON")"
API_METHOD="$(jq -r '.auth.method' <<<"$META_JSON")"
TPL_MODE="$(jq -r '.template.mode' <<<"$META_JSON")"
DISK_GB="$(jq -r '.template.disk_gb' <<<"$META_JSON")"
SSH_FLAG="$(jq -r '.template.ssh' <<<"$META_JSON")"
DIRECT_FLAG="$(jq -r '.template.direct' <<<"$META_JSON")"
IMAGE=""
if [[ "$TPL_MODE" == "docker_image" ]]; then
  IMAGE="$(jq -r '.template.docker_image' <<<"$META_JSON")"
fi

# 準備全域 CLI 參數（--api-key）
CLI_GLOBAL=()
if [[ -n "$API_KEY" && "$API_KEY" != "null" ]]; then
  CLI_GLOBAL+=(--api-key "$API_KEY")
fi

# 需要時把 key 永久寫入
if [[ -n "$API_KEY" && ( "$API_PERSIST" == "true" || "$API_METHOD" == "cli" || "$API_METHOD" == "both" ) ]]; then
  echo "==> Persisting API key into Vast.ai config ..."
  $CLI "${CLI_GLOBAL[@]}" set api-key "$API_KEY" >/dev/null 2>&1 || true
fi

echo "==> Query:"
printf "%s\n" "$QUERY"
echo "==> Order: $ORDER"

if [[ "$PRINT_CMD" == "true" ]]; then
  echo "==> Full command:"
  printf "%s " "$CLI" "search" "offers"
  printf '"%s" ' "$QUERY"
  printf -- "--order=%s --raw\n" "$ORDER"
fi

# --- 查詢 offers ---
RAW="$($CLI "${CLI_GLOBAL[@]}" search offers "$QUERY" --order="$ORDER" --raw 2>&1 || true)"

# 調試：檢查回應
if [[ ${#RAW} -eq 0 ]]; then
  echo "警告：API 回應為空"
elif [[ ${#RAW} -lt 100 ]]; then
  echo "警告：API 回應很短（${#RAW} 字元）"
  echo "回應內容："
  echo "$RAW"
fi

# 嘗試解析；容忍前後雜訊
OFFERS="$(
printf '%s' "$RAW" | python3 - <<'PY'
import sys, json, re
data = sys.stdin.buffer.read().decode('utf-8', 'replace')
m = re.search(r'(\[|\{)', data)
if not m:
    print("[]"); sys.exit(0)
text = data[m.start():]
for _ in range(3):
    try:
        obj = json.loads(text)
        if isinstance(obj, dict) and "offers" in obj:
            print(json.dumps(obj["offers"])); sys.exit(0)
        elif isinstance(obj, list):
            print(json.dumps(obj)); sys.exit(0)
        else:
            print(json.dumps([obj])); sys.exit(0)
    except Exception:
        last = max(text.rfind(']'), text.rfind('}'))
        if last != -1:
            text = text[:last+1]
        else:
            break
print("[]")
PY
)"

COUNT="$(jq 'length' <<<"$OFFERS")" || COUNT=0
if [[ "$COUNT" -eq 0 ]]; then
  echo "沒有符合條件的機器。"
  exit 2
fi

# 二次基於 dph / dph_total 過濾
if [[ "$USE_TOTAL" == "true" ]]; then
  OFFERS="$(jq --argjson min "${P_MIN:-0}" --argjson max "${P_MAX:-1e9}" '
    map(select((.dph_total // .dph // 0) >= $min and (.dph_total // .dph // 0) <= $max))
    | sort_by(.dph_total // .dph // 0)
  ' <<<"$OFFERS")"
else
  OFFERS="$(jq --argjson min "${P_MIN:-0}" --argjson max "${P_MAX:-1e9}" '
    map(select((.dph // 0) >= $min and (.dph // 0) <= $max))
    | sort_by(.dph)
  ' <<<"$OFFERS")"
fi

# 依帶寬門檻再過濾
OFFERS="$(jq --argjson dn "${BW_DOWN_MIN:-0}" --argjson up "${BW_UP_MIN:-0}" '
  map(select((.inet_down // 0) >= $dn and (.inet_up // 0) >= $up))
' <<<"$OFFERS")"

COUNT="$(jq 'length' <<<"$OFFERS")" || COUNT=0
if [[ "$COUNT" -eq 0 ]]; then
  echo "經過價格/帶寬過濾後，沒有符合的機器。"
  exit 2
fi

if [[ "$LIST_IDS" == "true" ]]; then
  jq -r '.[].id' <<<"$OFFERS"
  exit 0
fi

preview_offers "$OFFERS"

# 預設選擇最便宜
OFFER_ID="$(choose_cheapest "$OFFERS")"

echo
echo "==> 準備建立實例："
echo "offer:   $OFFER_ID"
[[ -n "$IMAGE" && "$IMAGE" != "null" ]] && echo "image:   $IMAGE"
echo "disk:    $DISK_GB"
echo "ssh:     $SSH_FLAG"
echo "direct:  $DIRECT_FLAG"
echo

if [[ "$DRY_RUN" == "true" ]]; then
  echo "[DRY-RUN] $CLI ${CLI_GLOBAL[*]} create instance \"$OFFER_ID\" ${IMAGE:+--image \"$IMAGE\"} --disk \"$DISK_GB\" $([ "$SSH_FLAG" == "true" ] && echo --ssh) $([ "$DIRECT_FLAG" == "true" ] && echo --direct)"
  exit 0
fi

args=(create instance "$OFFER_ID")
[[ -n "$IMAGE" && "$IMAGE" != "null" ]] && args+=("--image" "$IMAGE")
args+=("--disk" "$DISK_GB")
[[ "$SSH_FLAG" == "true" ]] && args+=("--ssh")
[[ "$DIRECT_FLAG" == "true" ]] && args+=("--direct")

echo "+ $CLI ${CLI_GLOBAL[*]} ${args[*]}"
"$CLI" "${CLI_GLOBAL[@]}" "${args[@]}"
