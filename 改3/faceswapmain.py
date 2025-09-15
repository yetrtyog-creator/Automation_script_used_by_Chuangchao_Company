#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
主控腳本：
  1) 啟動/檢查 ComfyUI (8199)
  2) 來源資料夾檢查
  3) 依序執行 stage1 → stage2 → stage3
"""
from __future__ import annotations
import argparse
from pathlib import Path

from faceswap.config_loader import load_config
from faceswap.comfy_manager import ensure_up
from faceswap.comfy_api import ComfyAPI
from faceswap.stage1 import run_stage1
from faceswap.stage2 import run_stage2
from faceswap.stage3 import run_stage3

def parse_args():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=str(Path.cwd() / "config.yaml"), help="設定檔（YAML/JSON）路徑")
    ap.add_argument("--just", choices=["all", "stage1", "stage2", "stage3"], default="all", help="只執行某一階段或全部")
    return ap.parse_args()

def main():
    args = parse_args()
    cfg = load_config(args.config)
    print("[main] 設定：")
    print(f"  - comfyui.dir: {cfg.comfyui.dir}")
    print(f"  - comfyui.port: {cfg.comfyui.port}")
    print(f"  - paths.source_root: {cfg.paths.source_root}")
    print(f"  - paths.staging_root: {cfg.paths.staging_root}")
    print(f"  - paths.output_root: {cfg.paths.output_root}")
    print(f"  - pipeline.max_inflight: {cfg.pipeline.max_inflight}")
    print(f"  - pipeline.max_retries: {cfg.pipeline.max_retries}")
    print(f"  - pipeline.poll_interval_sec: {cfg.pipeline.poll_interval_sec}")

    # 1) Ensure ComfyUI up
    ensure_up(cfg.comfyui)

    # 2) API handle
    api = ComfyAPI(cfg.comfyui.base_url, poll_interval=cfg.pipeline.poll_interval_sec)

    # 3) Stages
    if args.just in ("all", "stage1"):
        run_stage1(api, cfg.workflows["stage1"], cfg.paths, cfg.pipeline)
    if args.just in ("all", "stage2"):
        run_stage2(api, cfg.workflows["stage2"], cfg.paths, cfg.pipeline)
    if args.just in ("all", "stage3"):
        run_stage3(api, cfg.workflows["stage3"], cfg.paths, cfg.pipeline)

    print("[main] 全部完成")

if __name__ == "__main__":
    main()
