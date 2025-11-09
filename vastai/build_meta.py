#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
build_meta.py
讀取 YAML 設定，輸出一段 JSON（供 shell 腳本使用），
同原腳本中的 Python here-doc，但移出成獨立檔案。
"""
import sys, json, os, re
from pathlib import Path

try:
    import yaml  # PyYAML
except Exception as e:
    sys.stderr.write("Missing PyYAML. Please install: python3 -m pip install --user PyYAML\n")
    sys.exit(1)

def needs_quote(s: str) -> bool:
    return any(ch in s for ch in [' ', '"', '[', ']', ','])

def fmt_name(s: str) -> str:
    if needs_quote(s):
        t = s.replace('\\', '\\\\').replace('"', '\\"')
        return f'"{t}"'
    return s

def main():
    if len(sys.argv) < 2:
        print("Usage: build_meta.py <config.yaml>", file=sys.stderr)
        sys.exit(2)

    cfg_path = Path(sys.argv[1])
    if not cfg_path.exists():
        print(f"Config not found: {cfg_path}", file=sys.stderr)
        sys.exit(2)

    cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}

    tokens = []

    # --- GPU 名稱規則 ---
    names = cfg.get('gpu_names') or []
    if isinstance(names, (list, tuple)):
        names = [str(x).strip() for x in names if str(x).strip()]
    elif names:
        names = [str(names).strip()]
    else:
        names = []

    if names:
        if len(names) == 1 and not needs_quote(names[0]):
            tokens.append(f"gpu_name={names[0]}")
        else:
            tokens.append("gpu_name in [" + ",".join(fmt_name(n) for n in names) + "]")

    # --- 地區 ---
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
        add_bool_if_present(key)

    query = " ".join(tokens).strip()
    # 把「gpu_name in [單一值]」強制改成「gpu_name=值」
    _single_in = re.compile(r'(?<!\w)gpu_name\s+in\s+\[([A-Za-z0-9_]+)\]')
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

if __name__ == "__main__":
    main()
