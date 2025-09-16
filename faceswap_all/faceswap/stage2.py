#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

"""
stage2.py — 按照最新版 config.yaml 重新實現

按照更清楚的設定檔執行：
- subfolder_Target_node: "152" - Target 子資料夾節點  
- subfolder_Face_node: "154" - Face 子資料夾節點
- 其他節點按照設定檔的映射執行
"""

import json
import time
from pathlib import Path
from typing import List, Dict, Any

from .config_loader import StageMapping, PathsConfig, PipelineConfig
from .folder_rules import ensure_source_layout, list_images
from .scheduler import Task, run_queue
from .comfy_api import ComfyAPI


# -------------------------------
# 工具函數
# -------------------------------
def _deepcopy_prompt(prompt: Dict[str, Any]) -> Dict[str, Any]:
    """深拷貝工作流模板"""
    return json.loads(json.dumps(prompt))


def _ensure_trailing_slash(p: str) -> str:
    """確保路徑以斜線結尾"""
    return p if p.endswith("/") else p + "/"


def _set_stringish_input(node: Dict[str, Any], value: str) -> None:
    """設定字串型節點的值"""
    node.setdefault("inputs", {})
    for key in ("value", "string", "text"):
        if key in node["inputs"]:
            node["inputs"][key] = value
            return
    node["inputs"]["value"] = value


def _set_node_string(wf: Dict[str, Any], node_id: str, value: str, label: str) -> None:
    """設定指定節點的字串值"""
    node = wf.get(str(node_id))
    if not node:
        raise KeyError(f"[stage2] 指定的 {label} 節點 id='{node_id}' 不存在於 workflow JSON")
    _set_stringish_input(node, value)


def _has_target_images(batch_path: Path) -> bool:
    """檢查批次目錄下的 Target 資料夾是否有圖片"""
    target_dir = batch_path / "Target"
    if not target_dir.is_dir():
        return False
    
    img_extensions = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tiff"}
    for file_path in target_dir.iterdir():
        if file_path.is_file() and file_path.suffix.lower() in img_extensions:
            return True
    return False


# -------------------------------
# 主流程
# -------------------------------
def run_stage2(api: ComfyAPI, mapping: StageMapping, paths: PathsConfig, pipe: PipelineConfig) -> List[Dict[str, Any]]:
    """
    Stage2 主流程：搜索匹配處理
    按照更清楚的 config.yaml 執行
    """
    print("[stage2] ==> 開始第二階段：搜索匹配處理")
    print("[stage2] 驗證來源結構...")
    
    batches = ensure_source_layout(Path(paths.source_root))
    
    # 過濾只有 Target 圖片的批次
    valid_batches = []
    for batch in batches:
        if _has_target_images(batch):
            valid_batches.append(batch)
        else:
            print(f"[stage2][skip] 批次 {batch.name} 的 Target 資料夾無圖片")
    
    if not valid_batches:
        print("[stage2] 沒有找到任何有效的批次")
        return []
    
    print(f"[stage2] 發現 {len(valid_batches)} 個有效批次：{[b.name for b in valid_batches]}")

    # 載入工作流模板
    wf_path = Path(mapping.file)
    if not wf_path.exists():
        raise FileNotFoundError(f"[stage2] 工作流檔案不存在：{wf_path}")
        
    prompt_template: Dict[str, Any] = json.loads(wf_path.read_text(encoding="utf-8"))

    mp = mapping.mappings or {}
    extras = mapping.extras or {}

    # 檢查必要的節點映射（按照更清楚的設定檔）
    required_nodes = [
        "output_root_node",          # "147" - 輸出根目錄 (輸出到暫存目錄)
        "output_separator_node",     # "149" - 路徑分隔符 "/" 
        "output_batch_name_node",    # "150" - 批次名稱 (01, 02 等)
        "subfolder_Target_node",     # "152" - Target 子資料夾節點
        "subfolder_Face_node",       # "154" - Face 子資料夾節點
        "base_dir_node",            # "162" - 輸入根目錄 (從來源目錄讀取)
        "batch_name_node",          # "163" - 輸入批次名稱
        "separator_node",           # "164" - 路徑分隔符"/" 
        "output_subfolder_node",    # "165" - 輸出子資料夾
        "collction_name_face",      # "171" - Face collection
        "collction_name_target",    # "172" - Target collection
    ]
    
    missing_nodes = []
    for node_key in required_nodes:
        if node_key not in mp:
            missing_nodes.append(node_key)
    
    if missing_nodes:
        raise KeyError(f"[stage2] config.yaml 缺少必要的節點設定：{missing_nodes}")

    # 讀取設定值
    recursive = bool(extras.get("recursive", True))
    per_dir_submit = bool(extras.get("per_dir_submit", True))
    unbounded_queue = bool(extras.get("unbounded_queue", True))
    queue_limit = None if unbounded_queue else pipe.max_inflight
    collection_prefix = extras.get("collection_name_prefix", "Face_Changing")

    # 路徑設定
    source_root_str = _ensure_trailing_slash(str(Path(paths.source_root)))      # 從來源讀取
    staging_root_str = _ensure_trailing_slash(str(Path(paths.staging_root)))    # 輸出到暫存

    print(f"[stage2] 從來源讀取：{source_root_str}")
    print(f"[stage2] 輸出到暫存：{staging_root_str}")

    all_results: List[Dict[str, Any]] = []

    def make_tasks_for_batch(batch_name: str) -> List[Task]:
        """為指定批次的 Target 資料夾建立任務列表"""
        target_dir = Path(paths.source_root) / batch_name / "Target"
        images = list_images(target_dir, recursive=recursive)
        num = len(images)
        
        if num <= 0:
            print(f"[stage2][skip] 無圖片：{target_dir}")
            return []

        print(f"[stage2] {batch_name}/Target 有 {num} 張圖片")

        batch_tasks: List[Task] = []
        for img_idx in range(num):
            wf: Dict[str, Any] = _deepcopy_prompt(prompt_template)

            # === 按照最新版 config.yaml 設定所有節點 ===
            changes = []
            
            # 輸出路徑設定（輸出到暫存目錄）
            _set_node_string(wf, mp["output_root_node"], staging_root_str, "output_root_node")
            changes.append(f"output_root_node[{mp['output_root_node']}] = '{staging_root_str}'")
            
            _set_node_string(wf, mp["output_separator_node"], "/", "output_separator_node")
            changes.append(f"output_separator_node[{mp['output_separator_node']}] = '/'")
            
            _set_node_string(wf, mp["output_batch_name_node"], batch_name, "output_batch_name_node")
            changes.append(f"output_batch_name_node[{mp['output_batch_name_node']}] = '{batch_name}'")
            
            # 子資料夾設定（按照更清楚的節點命名）
            _set_node_string(wf, mp["subfolder_Face_node"], "Face", "subfolder_Face_node")
            changes.append(f"subfolder_Face_node[{mp['subfolder_Face_node']}] = 'Face'")
            
            _set_node_string(wf, mp["subfolder_Target_node"], "Target", "subfolder_Target_node")
            changes.append(f"subfolder_Target_node[{mp['subfolder_Target_node']}] = 'Target'")
            
            # 輸入路徑設定（從來源目錄讀取）
            _set_node_string(wf, mp["base_dir_node"], source_root_str, "base_dir_node")
            changes.append(f"base_dir_node[{mp['base_dir_node']}] = '{source_root_str}'")
            
            _set_node_string(wf, mp["batch_name_node"], batch_name, "batch_name_node")
            changes.append(f"batch_name_node[{mp['batch_name_node']}] = '{batch_name}'")
            
            _set_node_string(wf, mp["separator_node"], "/", "separator_node")
            changes.append(f"separator_node[{mp['separator_node']}] = '/'")
            
            _set_node_string(wf, mp["output_subfolder_node"], "Target", "output_subfolder_node")
            changes.append(f"output_subfolder_node[{mp['output_subfolder_node']}] = 'Target'")
            
            # Collection 名稱設定
            face_collection = f"{collection_prefix}{batch_name}Face"
            target_collection = f"{collection_prefix}{batch_name}Target"
            
            _set_node_string(wf, mp["collction_name_face"], face_collection, "collction_name_face")
            changes.append(f"collction_name_face[{mp['collction_name_face']}] = '{face_collection}'")
            
            _set_node_string(wf, mp["collction_name_target"], target_collection, "collction_name_target")
            changes.append(f"collction_name_target[{mp['collction_name_target']}] = '{target_collection}'")

            # 調試輸出（每個批次的第一張）
            if img_idx == 0:
                print(f"[stage2][debug] 批次 {batch_name} 節點設定：")
                for change in changes:
                    print(f"[stage2][debug]   {change}")
                
                # 顯示路徑邏輯
                input_path = f"{source_root_str}{batch_name}/Target"
                output_path = f"{staging_root_str}{batch_name}/"
                print(f"[stage2][debug] 輸入路徑：{input_path}")
                print(f"[stage2][debug] 輸出路徑：{output_path}")
                print(f"[stage2][debug] Face Collection：{face_collection}")
                print(f"[stage2][debug] Target Collection：{target_collection}")

            batch_tasks.append(
                Task(
                    name=f"stage2:{batch_name}/Target#{img_idx+1:03d}",
                    workflow=wf,
                    max_retries=pipe.max_retries,
                )
            )
        
        return batch_tasks

    # 執行送單策略
    if per_dir_submit:
        # 逐批次送出
        for batch in valid_batches:
            tasks = make_tasks_for_batch(batch.name)
            if not tasks:
                continue
                
            print(f"[stage2] 送出 {len(tasks)} 個任務（{batch.name}/Target）...")
            print(f"[stage2] 佇列設定：max_inflight={queue_limit}, poll_interval={pipe.poll_interval_sec}s")
            
            t0 = time.time()
            results = run_queue(
                api, tasks,
                max_inflight=queue_limit,
                poll_interval=pipe.poll_interval_sec,
            )
            dt = time.time() - t0
            
            ok = sum(1 for r in results if "error" not in r)
            fail = len(results) - ok
            print(f"[stage2] {batch.name}/Target 完成：✅ {ok} 成功，❌ {fail} 失敗（耗時 {dt:.1f}s）")
            
            # 顯示失敗任務詳情
            if fail > 0:
                print(f"[stage2][error] {batch.name} 失敗任務：")
                for i, r in enumerate(results):
                    if "error" in r:
                        print(f"[stage2][error]   任務 #{i+1}: {r.get('error', 'Unknown error')}")
            
            all_results.extend(results)
    else:
        # 一次送出所有任務
        all_tasks: List[Task] = []
        for batch in valid_batches:
            all_tasks.extend(make_tasks_for_batch(batch.name))
        
        if all_tasks:
            print(f"[stage2] 一次送出 {len(all_tasks)} 個任務...")
            print(f"[stage2] 佇列設定：max_inflight={queue_limit}, poll_interval={pipe.poll_interval_sec}s")
            
            t0 = time.time()
            all_results = run_queue(
                api, all_tasks,
                max_inflight=queue_limit,
                poll_interval=pipe.poll_interval_sec,
            )
            dt = time.time() - t0
            
            total_success = sum(1 for r in all_results if "error" not in r)
            total_failed = len(all_results) - total_success
            print(f"[stage2] 全部完成：✅ {total_success} 成功，❌ {total_failed} 失敗（耗時 {dt:.1f}s）")

    # 最終總結
    total_success = sum(1 for r in all_results if "error" not in r)
    total_failed = len(all_results) - total_success
    
    print(f"\n[stage2] === 第二階段完成 ===")
    print(f"[stage2] 總任務數：{len(all_results)}")
    print(f"[stage2] 成功：✅ {total_success}")
    print(f"[stage2] 失敗：❌ {total_failed}")
    
    if total_failed > 0:
        print(f"[stage2][warn] 有 {total_failed} 個任務失敗，請檢查上方錯誤訊息")
    else:
        print(f"[stage2][info] 所有任務成功完成！")

    return all_results