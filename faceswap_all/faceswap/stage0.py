#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

"""
stage0.py — 工作流數據庫創建階段（照抄 stage1 邏輯）

功能：
1) 掃描來源目錄下所有數字批次資料夾（01, 02, 03...）
2) 為每個批次的 Target 和 Face 子資料夾分別用工作流創建數據庫
3) 照抄 stage1 的工作流調用邏輯，但只做數據庫初始化
4) 確保後續階段有完整的 collection 基礎
"""

import json
import time
import re
from pathlib import Path
from typing import List, Dict, Any

from .config_loader import StageMapping, PathsConfig, PipelineConfig
from .scheduler import Task, run_queue
from .comfy_api import ComfyAPI


# -------------------------------
# 小工具函數（照抄 stage1）
# -------------------------------
def _deepcopy_prompt(prompt: Dict[str, Any]) -> Dict[str, Any]:
    return json.loads(json.dumps(prompt))


def _ensure_trailing_slash(p: str) -> str:
    return p if p.endswith("/") else p + "/"


def _set_stringish_input(node: Dict[str, Any], value: str) -> None:
    """
    設定字串型節點的值
    """
    node.setdefault("inputs", {})
    for key in ("string", "text", "value"):
        if key in node["inputs"]:
            node["inputs"][key] = value
            return
    node["inputs"]["string"] = value


def _set_node_string(wf: Dict[str, Any], node_id: str, value: str, label: str) -> None:
    node = wf.get(str(node_id))
    if not node:
        raise KeyError(f"[stage0] 指定的 {label} 節點 id='{node_id}' 不存在於 workflow JSON")
    _set_stringish_input(node, value)


def _find_numeric_batches(source_root: Path) -> List[str]:
    """
    掃描來源目錄，找出所有數字命名的批次資料夾
    """
    if not source_root.is_dir():
        return []
    
    numeric_pattern = re.compile(r'^(?!0+$)\d{1,4}$')
    batches = []
    
    for child in source_root.iterdir():
        if child.is_dir() and numeric_pattern.match(child.name):
            batches.append(child.name)
    
    batches.sort(key=lambda x: int(x))
    return batches


def _has_subfolder_with_images(batch_dir: Path, subfolder: str) -> bool:
    """檢查指定子資料夾是否存在且有圖片"""
    subfolder_path = batch_dir / subfolder
    if not subfolder_path.is_dir():
        return False
    
    img_extensions = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tiff"}
    for file_path in subfolder_path.iterdir():
        if file_path.is_file() and file_path.suffix.lower() in img_extensions:
            return True
    return False


def _set_qdrant_collection(wf: Dict[str, Any], collection: str) -> List[str]:
    """
    設定 Qdrant 類節點的 collection 名稱
    """
    touched: List[str] = []
    for nid, node in wf.items():
        ctype = str(node.get("class_type", ""))
        if "Qdrant" not in ctype:
            continue
        ins = node.setdefault("inputs", {})
        set_any = False
        if "collection" in ins and not isinstance(ins["collection"], list):
            ins["collection"] = collection
            set_any = True
        if "collection_name" in ins and not isinstance(ins["collection_name"], list):
            ins["collection_name"] = collection
            set_any = True
        if not set_any:
            ins["collection"] = collection
            set_any = True
        if set_any:
            touched.append(str(nid))
    return touched


# -------------------------------
# 主流程（照抄 stage1 架構）
# -------------------------------
def run_stage0(api: ComfyAPI, mapping: StageMapping, paths: PathsConfig, pipe: PipelineConfig) -> List[Dict[str, Any]]:
    """
    Stage0 主流程：為所有批次創建數據庫 collections
    """
    print("[stage0] 掃描數字批次資料夾...")
    source_root = Path(paths.source_root)
    batches = _find_numeric_batches(source_root)
    
    if not batches:
        print("[stage0] 沒有找到任何數字批次資料夾")
        return []
    
    print(f"[stage0] 發現 {len(batches)} 個批次：{batches}")

    # 載入工作流模板
    wf_path = Path(mapping.file)
    prompt_template: Dict[str, Any] = json.loads(wf_path.read_text(encoding="utf-8"))

    mp = mapping.mappings or {}
    extras = mapping.extras or {}

    # 讀取節點 ID（stage0 只需要 batch_name_node 和 subfolder_node）
    batch_name_node = mp.get("batch_name_node")  # 126
    subfolder_node = mp.get("subfolder_node")    # 127

    if not all([batch_name_node, subfolder_node]):
        raise KeyError("[stage0] config.yaml 缺少 batch_name_node/subfolder_node 設定")

    # 設定值
    collection_prefix = extras.get("collection_name_prefix", "Face_Changing")
    source_root_str = _ensure_trailing_slash(str(source_root))
    per_dir_submit = bool(extras.get("per_dir_submit", True))
    unbounded_queue = bool(extras.get("unbounded_queue", True))
    queue_limit = None if unbounded_queue else pipe.max_inflight

    all_results: List[Dict[str, Any]] = []

    def make_task_for_collection(batch_name: str, subfolder: str) -> Task:
        """為單一 collection 創建一個任務"""
        wf: Dict[str, Any] = _deepcopy_prompt(prompt_template)

        # 只設定兩個字串節點（126 和 127）
        _set_node_string(wf, batch_name_node, batch_name, "batch_name_node")  # 126
        _set_node_string(wf, subfolder_node, subfolder, "subfolder_node")      # 127

        # 設定 Qdrant collection 名稱
        collection = f"{collection_prefix}{batch_name}{subfolder}"
        touched_qdrant = _set_qdrant_collection(wf, collection)

        print(f"[stage0] 創建 collection: {collection}")
        if touched_qdrant:
            print(f"[stage0][debug] Qdrant 節點已設 collection='{collection}' -> {touched_qdrant}")

        return Task(
            name=f"CreateDB-{batch_name}/{subfolder}",
            workflow=wf,
            max_retries=pipe.max_retries,
        )

    # 為每個批次的 Target 和 Face 創建任務
    if per_dir_submit:
        # 逐批次送出
        for batch_name in batches:
            batch_dir = source_root / batch_name
            batch_tasks = []
            
            # 檢查並創建 Target collection
            if _has_subfolder_with_images(batch_dir, "Target"):
                batch_tasks.append(make_task_for_collection(batch_name, "Target"))
            else:
                print(f"[stage0][skip] {batch_name}/Target 無圖片，跳過創建")
            
            # 檢查並創建 Face collection  
            if _has_subfolder_with_images(batch_dir, "Face"):
                batch_tasks.append(make_task_for_collection(batch_name, "Face"))
            else:
                print(f"[stage0][skip] {batch_name}/Face 無圖片，跳過創建")
            
            if not batch_tasks:
                continue
                
            print(f"[stage0] 送出 {len(batch_tasks)} 個數據庫創建任務（批次 {batch_name}）...")
            t0 = time.time()
            results = run_queue(
                api, batch_tasks,
                max_inflight=queue_limit,
                poll_interval=pipe.poll_interval_sec,
            )
            dt = time.time() - t0
            ok = sum(1 for r in results if "error" not in r)
            fail = len(results) - ok
            print(f"[stage0] 批次 {batch_name} 完成：成功 {ok}，失敗 {fail}（{dt:.1f}s）")
            all_results.extend(results)
    else:
        # 一次送出所有任務
        all_tasks = []
        for batch_name in batches:
            batch_dir = source_root / batch_name
            if _has_subfolder_with_images(batch_dir, "Target"):
                all_tasks.append(make_task_for_collection(batch_name, "Target"))
            if _has_subfolder_with_images(batch_dir, "Face"):
                all_tasks.append(make_task_for_collection(batch_name, "Face"))
        
        if all_tasks:
            print(f"[stage0] 送出 {len(all_tasks)} 個數據庫創建任務...")
            all_results = run_queue(
                api, all_tasks,
                max_inflight=queue_limit,
                poll_interval=pipe.poll_interval_sec,
            )

    # 總結
    total_success = sum(1 for r in all_results if "error" not in r)
    total_failed = len(all_results) - total_success
    print(f"[stage0] 數據庫創建完成：總成功 {total_success}，總失敗 {total_failed}")
    
    if total_failed > 0:
        print("[stage0][warn] 部分數據庫創建失敗（詳見上方錯誤列印）")

    return all_results