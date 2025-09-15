#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations
"""
stage2.py — 修正版：處理向量搜索和匹配
"""
import json
from pathlib import Path
from typing import List, Dict, Any

from .config_loader import StageMapping, PathsConfig, PipelineConfig
from .folder_rules import ensure_source_layout, prepare_staging_dirs
from .workflow_patch import patch_by_map
from .scheduler import Task, run_queue
from .comfy_api import ComfyAPI

def run_stage2(api: ComfyAPI, mapping: StageMapping, paths: PathsConfig, pipe: PipelineConfig) -> None:
    print("[stage2] 驗證來源結構 ...")
    batches = ensure_source_layout(Path(paths.source_root))
    
    print("[stage2] 預先建立暫存目錄 ...")
    batch_names = [b.name for b in batches]
    staging_dirs = prepare_staging_dirs(Path(paths.staging_root), batch_names)
    
    wf_path = Path(mapping.file)
    prompt_template: Dict[str, Any] = json.loads(wf_path.read_text(encoding="utf-8"))
    
    tasks: List[Task] = []
    
    # 必填節點 ID
    base_node = mapping.mappings.get("base_dir_node")
    batch_node = mapping.mappings.get("batch_name_node")
    sub_node = mapping.mappings.get("subfolder_node")
    output_node = mapping.mappings.get("output_root_node")
    
    # 可選節點（分隔符）
    separator_node = mapping.mappings.get("separator_node")
    
    if not all([base_node, batch_node, sub_node, output_node]):
        raise KeyError("stage2.mappings 需要 base_dir_node / batch_name_node / subfolder_node / output_root_node")
    
    # 確保路徑格式正確
    source_root_str = str(Path(paths.source_root))
    staging_root_str = str(Path(paths.staging_root))
    
    # Windows 路徑處理（如果需要）
    if "\\" in source_root_str:
        if not source_root_str.endswith("\\"):
            source_root_str += "\\"
        if not staging_root_str.endswith("\\"):
            staging_root_str += "\\"
        path_sep = "\\"
    else:
        if not source_root_str.endswith("/"):
            source_root_str += "/"
        if not staging_root_str.endswith("/"):
            staging_root_str += "/"
        path_sep = "/"
    
    for b in batches:
        for sub in ("Target", "Face"):
            wf = json.loads(json.dumps(prompt_template))
            
            # 設定節點值
            patch = {
                base_node: {"value": source_root_str},
                batch_node: {"value": b.name},
                sub_node: {"value": sub},
                output_node: {"value": staging_root_str},
            }
            
            # 設定分隔符（如果有）
            if separator_node:
                patch[separator_node] = {"value": path_sep}
            
            # 應用補丁
            try:
                patch_by_map(wf, patch)
            except KeyError as e:
                print(f"[stage2][error] 節點不存在：{e}")
                print(f"  嘗試設定的節點：{patch.keys()}")
                print(f"  工作流中的節點：{list(wf.keys())[:10]}...")
                raise
            
            tasks.append(
                Task(
                    name=f"{b.name}/{sub}",
                    workflow=wf,
                    max_retries=pipe.max_retries,
                )
            )
    
    print(f"[stage2] 準備送出 {len(tasks)} 個任務 ...")
    
    results = run_queue(
        api,
        tasks,
        max_inflight=pipe.max_inflight,
        poll_interval=pipe.poll_interval_sec,
    )
    
    # 統計結果
    success = len([r for r in results if "error" not in r])
    failed = len([r for r in results if "error" in r])
    
    print(f"[stage2] 完成：成功 {success}，失敗 {failed}")
    
    if failed > 0:
        print("[stage2][warn] 部分任務失敗")
        for r in results:
            if "error" in r:
                print(f"  - {r['task'].name}: {r['error']}")
    
    return results