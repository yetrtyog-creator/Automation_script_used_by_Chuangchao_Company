#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations
"""
stage1.py — 批次模式：為每個資料夾的所有圖片送出連續任務
"""
import json, os, random, string
from pathlib import Path
from typing import List, Dict, Any, Tuple

from .config_loader import StageMapping, PathsConfig, PipelineConfig
from .folder_rules import ensure_source_layout, list_images
from .workflow_patch import patch_by_map
from .scheduler import Task, run_queue
from .comfy_api import ComfyAPI

def _rand_id(k: int = 6) -> str:
    return "".join(random.choice(string.hexdigits.lower()) for _ in range(k))

def run_stage1(api: ComfyAPI, mapping: StageMapping, paths: PathsConfig, pipe: PipelineConfig) -> None:
    print("[stage1] 驗證來源結構 ...")
    batches = ensure_source_layout(Path(paths.source_root))
    print(f"[stage1] 發現 {len(batches)} 個批次：{[b.name for b in batches]}")

    wf_path = Path(mapping.file)
    prompt_template: Dict[str, Any] = json.loads(wf_path.read_text(encoding="utf-8"))

    tasks: List[Task] = []

    extras = mapping.extras or {}
    recursive = bool(extras.get("recursive", True))

    # 必填節點 ID（修正格式）
    base_node = mapping.mappings.get("base_dir_node", "").split(":")[0]  # 移除 :0
    batch_node = mapping.mappings.get("batch_name_node", "").split(":")[0]
    separator_node = mapping.mappings.get("separator_node", "").split(":")[0] if mapping.mappings.get("separator_node") else None
    sub_node = mapping.mappings.get("subfolder_node", "").split(":")[0]
    
    if not all([base_node, batch_node, sub_node]):
        raise KeyError("stage1.mappings 需要 base_dir_node / batch_name_node / subfolder_node")

    for b in batches:
        for sub in ("Target", "Face"):
            data_dir = Path(paths.source_root) / b.name / sub
            images = list_images(data_dir, recursive=recursive)
            num = len(images)
            
            if num <= 0:
                print(f"[stage1][skip] 無圖片：{data_dir}")
                continue
            
            print(f"[stage1] {b.name}/{sub} 有 {num} 張圖片")
            
            # Load Image Batch 的 incremental_image 模式會記住狀態
            # 所以我們需要為每張圖片送出一個任務
            for img_idx in range(num):
                wf = json.loads(json.dumps(prompt_template))
                
                # 確保路徑格式正確
                source_root_str = str(Path(paths.source_root))
                if not source_root_str.endswith(('/', '\\')):
                    source_root_str += "/"
                
                # 設定節點值
                patch = {
                    base_node: {"value": source_root_str},
                    batch_node: {"value": b.name},
                    sub_node: {"value": sub},
                }
                
                if separator_node:
                    patch[separator_node] = {"value": "/"}
                
                patch_by_map(wf, patch)
                
                # 設定 Load Image Batch 的 index
                for node_id, node_data in wf.items():
                    if "Load Image Batch" in str(node_data.get("class_type", "")):
                        node_data.setdefault("inputs", {})
                        # 重要：incremental_image 模式下，每次執行會自動遞增
                        # 所以第一次設 0，後續 ComfyUI 會自己管理
                        if img_idx == 0:
                            node_data["inputs"]["index"] = 0
                            node_data["inputs"]["seed"] = random.randint(0, 2**32-1)
                
                tasks.append(
                    Task(
                        name=f"{b.name}/{sub} #{img_idx+1}/{num}",
                        workflow=wf,
                        max_retries=pipe.max_retries,
                    )
                )

    print(f"[stage1] 準備送出 {len(tasks)} 個任務 ...")
    
    # 執行所有任務
    results = run_queue(
        api,
        tasks,
        max_inflight=pipe.max_inflight,
        poll_interval=pipe.poll_interval_sec,
    )
    
    # 統計結果
    success = len([r for r in results if "error" not in r])
    failed = len([r for r in results if "error" in r])
    
    print(f"[stage1] 完成：成功 {success}，失敗 {failed}")
    
    if failed > 0:
        print("[stage1][warn] 部分任務失敗，檢查日誌")
        for r in results:
            if "error" in r:
                print(f"  - {r['task'].name}: {r['error']}")
    
    return results