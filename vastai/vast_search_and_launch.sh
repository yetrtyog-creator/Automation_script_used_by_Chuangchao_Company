#!/usr/bin/env bash
set -euo pipefail

# vast_search_and_launch.sh — 精準版
# 依使用者指示：
# - 查詢語法與示範保持一致（gpu_name=RTX_5090；地區清單含 FO，不含 FA）
# - 不主動添加 external/rentable/verified/rented 等條件，除非 config.yaml 明確指定
# - 不輸出多餘空白行
# - 價格、磁碟、排序依 config.yaml

CONFIG="config.yaml"
DRY_RUN="false"

while [[ $# -gt 0 ]]; do
  case "$1" in
    -c|--config) CONFIG="$2"; shift 2 ;;
    --dry-run)   DRY_RUN="true"; shift ;;
    *) echo "Unknown arg: $1" >&2; exit 1 ;;
  esac
done

[[ -f "$CONFIG" ]] || { echo "Config not found: $CONFIG" >&2; exit 1; }

# --- pick CLI: vastai or vast ---
if command -v vastai >/dev/null 2>&1; then
  CLI="vastai"
elif command -v vast >/dev/null 2>&1; then
  CLI="vast"
else
  echo "Missing dependency: vastai (or vast). Install: pip install --user vastai" >&2
  exit 1
fi

# --- other deps ---
for bin in python3 jq; do
  command -v "$bin" >/dev/null 2>&1 || { echo "Missing dependency: $bin" >&2; exit 1; }
done

# 確保有 PyYAML
if ! python3 -c "import yaml" 2>/dev/null; then
  echo "PyYAML not found, installing to --user ..." >&2
  python3 -m pip install --user --quiet PyYAML
fi

# 讀 YAML → META_JSON
META_JSON="$(python3 - "$CONFIG" <<'PY'
import sys, json, re, os
from pathlib import Path
import yaml

cfg = yaml.safe_load(Path(sys.argv[1]).read_text(encoding='utf-8')) or {}

tokens = []

# --- GPU 名稱規則 ---
# 若只有一個名稱且不含空白，使用等號：gpu_name=RTX_5090
# 若有多個名稱或名稱含空白，使用 in 並為含空白的值加引號。
names = cfg.get('gpu_names') or []
if isinstance(names, (list, tuple)):
    names = [str(x).strip() for x in names if str(x).strip()]
else:
    names = [str(names).strip()] if names else []

def needs_quote(s: str) -> bool:
    return any(ch in s for ch in [' ', '"', '[', ']', ','])

def fmt_name(s: str) -> str:
    return f'"{s.replace("\\", "\\\\").replace('"', r'\"')}"' if needs_quote(s) else s

if names:
    if len(names) == 1 and not needs_quote(names[0]):
        tokens.append(f"gpu_name={names[0]}")
    else:
        tokens.append("gpu_name in [" + ",".join(fmt_name(n) for n in names) + "]")

# --- 地區（兩碼）；不改動使用者提供的順序與內容，但會轉大寫與擷取前兩碼 ---
countries = cfg.get('geolocations') or cfg.get('countries') or []
if isinstance(countries, (list, tuple)) and countries:
    cc = [str(c).strip().upper()[:2] for c in countries if str(c).strip()]
    if cc:
        tokens.append("geolocation in [" + ",".join(cc) + "]")

# --- 磁碟下限 ---
disk_min = cfg.get('disk_space_gb_min')
if disk_min is not None:
    tokens.append(f"disk_space>={int(disk_min)}")

# --- 價格區間 ---
price = cfg.get('price') or {}
use_total = bool(price.get('use_dph_total', False))
minp = price.get('min_dph')
maxp = price.get('max_dph')
if minp is not None:
    tokens.append(("dph_total" if use_total else "dph") + f">={float(minp)}")
if maxp is not None:
    tokens.append(("dph_total" if use_total else "dph") + f"<={float(maxp)}")

# --- 網速門檻（可選）---
bw = cfg.get('bandwidth_min') or {}
def _f(x, d=0.0):
    try: return float(x)
    except: return d
dn = _f(bw.get('down_mbps'), 0.0)
up = _f(bw.get('up_mbps'), 0.0)
if dn > 0: tokens.append(f"inet_down>={int(dn)}")
if up > 0: tokens.append(f"inet_up>={int(up)}")

# --- Bool 欄位（僅在 config 指定時才加入；不做預設）---
def add_bool_if_present(key, field=None):
    if key in cfg and cfg.get(key) is not None:
        v = bool(cfg.get(key))
        tokens.append(f"{field or key}={'true' if v else 'false'}")

for key in ('rentable','verified','rented','external'):
    # external=true/false 直接映射；若要「排除 external」，請在 config.yaml explicitly 設 external: false
    add_bool_if_present(key)

query = " ".join(tokens).strip()
# 後處理：把「gpu_name in [單一值]」強制改成「gpu_name=值」
import re as _re
_single_in = _re.compile(r'(?<!\w)gpu_name\s+in\s+\[([A-Za-z0-9_]+)\]')
query = _single_in.sub(r'gpu_name=\1', query)


# 其他設定
order_by = (cfg.get('order_by') or "dph")

tpl = cfg.get('template') or {}
mode = (tpl.get('mode') or 'docker_image')
docker_image = tpl.get('docker_image') or 'ghcr.io/comfyanonymous/comfyui:latest'
disk_gb      = int(tpl.get('disk_gb', 300))
ssh_flag     = bool(tpl.get('ssh', True))
direct_flag  = bool(tpl.get('direct', True))

pick = cfg.get('pick') or {}
strategy = (pick.get('strategy') or 'cheapest')
weights  = (pick.get('weights') or {})
w_price  = float(weights.get('price', 1.0))
w_down   = float(weights.get('down', 0.0))
w_up     = float(weights.get('up', 0.0))

# 認證
auth = cfg.get('auth') or {}
api_key = auth.get('api_key') or os.environ.get('VASTAI_API_KEY') or ""
persist = bool(auth.get('persist', False))
method  = (auth.get('method') or 'arg')

out = {
    "query": query,
    "order": order_by,
    "price": {
        "use_dph_total": use_total,
        "min": (float(minp) if minp is not None else None),
        "max": (float(maxp) if maxp is not None else None)
    },
    "bandwidth_min": {"down": float(dn), "up": float(up)},
    "template": {
        "mode": mode,
        "docker_image": docker_image,
        "disk_gb": disk_gb,
        "ssh": ssh_flag,
        "direct": direct_flag
    },
    "pick": {
        "strategy": strategy,
        "weights": {"price": w_price, "down": w_down, "up": w_up}
    },
    "auth": {
        "api_key": api_key,
        "persist": persist,
        "method": method
    }
}
print(json.dumps(out, ensure_ascii=False))
PY
)"

# 取設定
QUERY="$(jq -r '.query' <<<"$META_JSON")"
ORDER="$(jq -r '.order' <<<"$META_JSON")"
USE_TOTAL="$(jq -r '.price.use_dph_total' <<<"$META_JSON")"
P_MIN="$(jq -r '.price.min' <<<"$META_JSON")"
P_MAX="$(jq -r '.price.max' <<<"$META_JSON")"
BW_DOWN_MIN="$(jq -r '.bandwidth_min.down' <<<"$META_JSON")"
BW_UP_MIN="$(jq -r '.bandwidth_min.up' <<<"$META_JSON")"
PICK_STRATEGY="$(jq -r '.pick.strategy' <<<"$META_JSON")"
W_PRICE="$(jq -r '.pick.weights.price' <<<"$META_JSON")"
W_DOWN="$(jq -r '.pick.weights.down' <<<"$META_JSON")"
W_UP="$(jq -r '.pick.weights.up' <<<"$META_JSON")"
API_KEY="$(jq -r '.auth.api_key' <<<"$META_JSON")"
API_PERSIST="$(jq -r '.auth.persist' <<<"$META_JSON")"
API_METHOD="$(jq -r '.auth.method' <<<"$META_JSON")"

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

# 調試：顯示完整命令（僅供參考顯示，不含轉義副作用）
echo "==> Full command:"
printf "%s " "$CLI" "search" "offers"
printf '"%s" ' "$QUERY"
printf -- "--order=%s --raw\n" "$ORDER"
echo

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

# 預覽（前 20）
echo "==> 匹配到 $COUNT 台（前 20 行預覽）"
jq -r '
  ( "ID\tGeo\tGPU\tVerified\tRentable\t$DPH\t$Total\tDown(Mbps)\tUp(Mbps)\tDisk(GB)" ),
  ( .[] | "\(.id)\t\(.geolocation // .country // \"N/A\")\t\(.gpu_name // .gpu_type)\t\(((.verification==\"verified\") or (.verified==true))|tostring)\t\(((.rentable==true) and (.rented!=true))|tostring)\t\(.dph // 0)\t\(.dph_total // 0)\t\(.inet_down // 0)\t\(.inet_up // 0)\t\(.disk_space // 0)" )
' <<<"$OFFERS" | head -n 21 | column -ts $'\t'

choose_cheapest() { jq -r '.[0].id' <<<"$OFFERS"; }
choose_max_down() { jq -r 'max_by((.inet_down // 0)) | .id' <<<"$OFFERS"; }
choose_max_up()   { jq -r 'max_by((.inet_up // 0))   | .id' <<<"$OFFERS"; }

OFFER_ID="$(choose_cheapest)"  # 預設最便宜；不做自作主張的複雜打分

# 模板/映像
TPL_MODE="$(jq -r '.template.mode' <<<"$META_JSON")"
DISK_GB="$(jq -r '.template.disk_gb' <<<"$META_JSON")"
SSH_FLAG="$(jq -r '.template.ssh' <<<"$META_JSON")"
DIRECT_FLAG="$(jq -r '.template.direct' <<<"$META_JSON")"

if [[ "$TPL_MODE" == "docker_image" ]]; then
  IMAGE="$(jq -r '.template.docker_image' <<<"$META_JSON")"
else
  echo "不使用模板搜索（按 config 設定）"
  IMAGE=""
fi

echo
echo "==> 準備建立實例："
echo "offer:   $OFFER_ID"
[[ -n "$IMAGE" && "$IMAGE" != "null" ]] && echo "image:   $IMAGE"
echo "disk:    $DISK_GB"
echo "ssh:     $SSH_FLAG"
echo "direct:  $DIRECT_FLAG"
echo

if [[ "$DRY_RUN" == "true" ]]; then
  echo "[DRY-RUN] $CLI ${CLI_GLOBAL[*]} create instance "$OFFER_ID" ${IMAGE:+--image "$IMAGE"} --disk "$DISK_GB" $([[ $SSH_FLAG == "true" ]] && echo --ssh) $([[ $DIRECT_FLAG == "true" ]] && echo --direct)"
  exit 0
fi

args=(create instance "$OFFER_ID")
[[ -n "$IMAGE" && "$IMAGE" != "null" ]] && args+=("--image" "$IMAGE")
args+=("--disk" "$DISK_GB")
[[ "$SSH_FLAG" == "true" ]] && args+=("--ssh")
[[ "$DIRECT_FLAG" == "true" ]] && args+=("--direct")

echo "+ $CLI ${CLI_GLOBAL[*]} ${args[*]}"
"$CLI" "${CLI_GLOBAL[@]}" "${args[@]}"
