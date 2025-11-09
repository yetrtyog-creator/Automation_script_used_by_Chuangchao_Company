#!/usr/bin/env bash
# lib/utils.sh

set -euo pipefail

ensure_cli() {
  if command -v vastai >/dev/null 2>&1; then
    CLI="vastai"
  elif command -v vast >/dev/null 2>&1; then
    CLI="vast"
  else
    echo "Missing dependency: vastai (or vast). Install: pip install --user vastai" >&2
    return 1
  fi
}

ensure_tools() {
  for bin in python3 jq; do
    command -v "$bin" >/dev/null 2>&1 || { echo "Missing dependency: $bin" >&2; return 1; }
  done
}

ensure_pyyaml() {
  if ! python3 -c "import yaml" >/dev/null 2>&1; then
    echo "PyYAML not found, installing to --user ..." >&2
    python3 -m pip install --user --quiet PyYAML
  fi
}

preview_offers() {
  local json="$1"
  local count="$(jq 'length' <<<"$json")" || count=0
  echo "==> 匹配到 $count 台（前 20 行預覽）"
  jq -r '
    ( "ID\tGeo\tGPU\tVerified\tRentable\t$DPH\t$Total\tDown(Mbps)\tUp(Mbps)\tDisk(GB)" ),
    ( .[] | "\(.id)\t\(.geolocation // .country // \"N/A\")\t\(.gpu_name // .gpu_type)\t\(((.verification==\"verified\") or (.verified==true))|tostring)\t\(((.rentable==true) and (.rented!=true))|tostring)\t\(.dph // 0)\t\(.dph_total // 0)\t\(.inet_down // 0)\t\(.inet_up // 0)\t\(.disk_space // 0)" )
  ' <<<"$json" | head -n 21 | column -ts $'\t'
}

choose_cheapest() { jq -r '.[0].id' <<<"$1"; }
choose_max_down() { jq -r 'max_by((.inet_down // 0)) | .id' <<<"$1"; }
choose_max_up()   { jq -r 'max_by((.inet_up   // 0)) | .id' <<<"$1"; }
