#!/usr/bin/env python3
# check_config_v3.py
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

try:
    from model.settings import load_config, get_settings
except Exception as e:
    print(f"[FAIL] 無法匯入 model.settings：{type(e).__name__}: {e}", file=sys.stderr)
    sys.exit(2)


def main() -> int:
    ap = argparse.ArgumentParser(description="檢查 config.yaml 與 ComfyUI 工作流位置（schema v3）")
    ap.add_argument("-c", "--config", default=None, help="config.yaml 路徑（預設自動尋找）")
    args = ap.parse_args()

    try:
        cfg = load_config(args.config)
    except Exception as e:
        print(f"[FAIL] 設定載入失敗：{type(e).__name__}: {e}", file=sys.stderr)
        return 2

    print("[OK] 設定載入成功")
    print(f"  schema_version        = {cfg.schema_version}")
    print(f"  comfyui.dir           = {cfg.comfyui.dir}")
    print(f"  comfyui.port          = {cfg.comfyui.port}")
    print(f"  comfyui.workflows_dir = {cfg.comfyui.workflows_dir}")
    print(f"  source_root           = {cfg.paths_source_root}")
    print(f"  staging_root          = {cfg.paths_staging_root}")
    print(f"  output_root           = {cfg.paths_output_root}")
    print(f"  run stages            = {[cfg.pipeline.run_stage1, cfg.pipeline.run_stage2, cfg.pipeline.run_stage3]}")
    print(f"  collection            = {cfg.pipeline.collection_name}")
    print(f"  inflight/retries/poll = {cfg.pipeline.max_inflight}/{cfg.pipeline.max_retries}/{cfg.pipeline.poll_interval_sec}")

    # 顯示工作流「名稱」與「解析後的實際路徑」
    print("\n[Workflows]")
    print(f"  stage1 name -> {cfg.workflows.stage1}")
    print(f"  stage2 name -> {cfg.workflows.stage2}")
    print(f"  stage3 name -> {cfg.workflows.stage3}")
    print(f"  stage1 path -> {cfg.workflow_paths.stage1}")
    print(f"  stage2 path -> {cfg.workflow_paths.stage2}")
    print(f"  stage3 path -> {cfg.workflow_paths.stage3}")

    # 基本 JSON 語法檢查
    rc = 0
    print("\n[Check] 解析 workflow JSON：")
    for name, p in [
        ("stage1", cfg.workflow_paths.stage1),
        ("stage2", cfg.workflow_paths.stage2),
        ("stage3", cfg.workflow_paths.stage3),
    ]:
        try:
            _ = json.loads(Path(p).read_text(encoding="utf-8"))
            print(f"  [OK] {name}: {p}")
        except Exception as e:
            print(f"  [WARN] {name}: {p} JSON 解析失敗：{type(e).__name__}: {e}", file=sys.stderr)
            # 若要視為致命錯誤，改成 rc = 3
    # 示範點號選擇器
    part = get_settings(["comfyui.port", "comfyui.workflows_dir", "workflows.stage3", "workflow_paths.stage3"],
                        config_path=args.config)
    print("\n[Partial via get_settings]:")
    for k, v in part.items():
        print(f"  {k} = {v}")

    return rc


if __name__ == "__main__":
    sys.exit(main())
