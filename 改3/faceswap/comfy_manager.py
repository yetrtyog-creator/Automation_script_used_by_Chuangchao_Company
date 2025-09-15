#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations
import subprocess, time, os
from pathlib import Path
import requests
from .config_loader import ComfyConfig

def _is_alive(base_url: str, timeout: float = 1.5) -> bool:
    try:
        r = requests.get(base_url + "/object_info", timeout=timeout)
        return r.status_code == 200 and isinstance(r.json(), dict)
    except Exception:
        return False

def ensure_up(cfg: ComfyConfig, wait_timeout: float = 120.0) -> None:
    base_url = cfg.base_url
    if _is_alive(base_url):
        print(f"[comfy] 已就緒：{base_url}")
        return
    # Try to start
    main_py = Path(cfg.dir) / "main.py"
    if not main_py.exists():
        raise FileNotFoundError(f"找不到 ComfyUI main.py：{main_py}")
    log_path = Path("/tmp/comfyui.log")
    cmd = ["python3", str(main_py), "--listen", cfg.host, "--port", str(cfg.port)]
    extra_args = cfg.start_args.strip().split() if cfg.start_args else []
    cmd += extra_args
    print(f"[comfy] 啟動中：{' '.join(cmd)}")
    with open(log_path, "ab") as lf:
        subprocess.Popen(cmd, cwd=str(cfg.dir), stdout=lf, stderr=lf, env=os.environ.copy(), start_new_session=True)
    # Wait loop with heartbeat
    t0 = time.time()
    while time.time() - t0 < wait_timeout:
        if _is_alive(base_url):
            print(f"[comfy] 就緒：{base_url}（用時 {int(time.time()-t0)}s）")
            return
        print("[comfy][hb] 等待 ComfyUI 就緒 ...")
        time.sleep(2.5)
    # Final check
    if not _is_alive(base_url):
        raise TimeoutError(f"在 {wait_timeout}s 內 ComfyUI 未就緒，請檢查 /tmp/comfyui.log")
