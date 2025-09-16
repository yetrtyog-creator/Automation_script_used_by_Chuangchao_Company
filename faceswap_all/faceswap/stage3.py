#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

"""
stage3.py — 完善版本，讀取暫存目錄並輸出到最終目錄

功能描述：
1. 從 /workspace/暫存/ 讀取 stage2 處理後的結果
2. 找到每個批次的 Target 和 Face 圖片（第一張）
3. 執行換臉工作流
4. 輸出到 /workspace/輸出/
"""

import json
import time
from pathlib import Path
from typing import List, Dict, Any, Optional

from .config_loader import StageMapping, PathsConfig, PipelineConfig
from .folder_rules import ensure_staging_layout, list_images
from .scheduler import Task, run_queue
from .comfy_api import ComfyAPI


# -------------------------------
# 工具函數
# -------------------------------
def _deepcopy_prompt(prompt: Dict[str, Any]) -> Dict[str, Any]:
    """深拷貝工作流模板"""
    return json.loads(json.dumps(prompt))


def _set_stringish_input(node: Dict[str, Any], value: str) -> None:
    """設定字串型節點的值"""
    node.setdefault("inputs", {})
    for key in ("value", "string", "text"):
        if key in node["inputs"]:
            node["inputs"][key] = value
            return
    node["inputs"]["value"] = value


def _set_load_image_path(node: Dict[str, Any], image_path: str) -> None:
    """專門設定 LoadImage 節點的圖片路徑"""
    node.setdefault("inputs", {})
    # LoadImage 節點通常使用 "image" 欄位存放檔案名（相對於 ComfyUI 輸入目錄）
    # 但某些自定義節點可能使用絕對路徑
    node["inputs"]["image"] = image_path
    
    # 某些 LoadImage 變體可能還需要設定這些欄位
    if "upload" in node["inputs"]:
        node["inputs"]["upload"] = "image"


def _set_node_string(wf: Dict[str, Any], node_id: str, value: str, label: str) -> None:
    """設定指定節點的字串值"""
    node = wf.get(str(node_id))
    if not node:
        raise KeyError(f"[stage3] 指定的 {label} 節點 id='{node_id}' 不存在於 workflow JSON")
    
    # 檢查是否為 LoadImage 節點
    class_type = node.get("class_type", "")
    if "LoadImage" in class_type:
        print(f"[stage3][debug] 設定 LoadImage 節點 {node_id}: {value}")
        _set_load_image_path(node, value)
    else:
        print(f"[stage3][debug] 設定一般節點 {node_id} ({class_type}): {value}")
        _set_stringish_input(node, value)


def _ensure_trailing_slash(p: str) -> str:
    """確保路徑以斜線結尾"""
    return p if p.endswith("/") else p + "/"


def first_image(directory: Path) -> Optional[Path]:
    """取得目錄中的第一張圖片"""
    if not directory.is_dir():
        return None
    
    images = list_images(directory, recursive=False)
    return images[0] if images else None


def ensure_staging_layout(staging_root: Path) -> List[Path]:
    """
    確保暫存目錄結構正確，並回傳批次目錄列表
    預期結構：/workspace/暫存/01/Target/ 和 /workspace/暫存/01/Face/
    """
    if not staging_root.is_dir():
        raise FileNotFoundError(f"[stage3] 暫存根目錄不存在：{staging_root}")
    
    batches = []
    for batch_dir in staging_root.iterdir():
        if not batch_dir.is_dir():
            continue
        
        # 檢查是否為批次目錄（數字命名）
        if not batch_dir.name.isdigit() and not batch_dir.name.startswith("0"):
            continue
            
        target_dir = batch_dir / "Target"
        face_dir = batch_dir / "Face"
        
        if target_dir.is_dir() and face_dir.is_dir():
            batches.append(batch_dir)
            print(f"[stage3] 發現有效批次：{batch_dir.name}")
        else:
            print(f"[stage3][skip] 批次 {batch_dir.name} 缺少 Target 或 Face 目錄")
    
    return sorted(batches, key=lambda x: x.name)


# -------------------------------
# 主流程
# -------------------------------
def run_stage3(api: ComfyAPI, mapping: StageMapping, paths: PathsConfig, pipe: PipelineConfig) -> List[Dict[str, Any]]:
    """
    Stage3 主流程：換臉處理
    從暫存目錄讀取 Target 和 Face 圖片，執行換臉並輸出到最終目錄
    """
    print("[stage3] ==> 開始第三階段：換臉處理")
    print("[stage3] 驗證暫存結構...")
    
    # 檢查暫存目錄
    staging_root = Path(paths.staging_root)
    batches = ensure_staging_layout(staging_root)
    
    if not batches:
        print("[stage3] 暫存目錄中沒有找到任何有效批次")
        return []
    
    print(f"[stage3] 發現 {len(batches)} 個有效批次：{[b.name for b in batches]}")

    # 載入工作流模板
    wf_path = Path(mapping.file)
    if not wf_path.exists():
        raise FileNotFoundError(f"[stage3] 工作流檔案不存在：{wf_path}")
        
    prompt_template: Dict[str, Any] = json.loads(wf_path.read_text(encoding="utf-8"))

    mp = mapping.mappings or {}
    extras = mapping.extras or {}

    # 檢查必要的節點映射
    required_nodes = [
        "target_path_node",      # "17" - 目標圖節點輸入
        "face_path_node",        # "95" - 目標臉節點輸入  
        "output_root_node",      # "100" - 輸出根目錄
        "output_subfolder_node", # "101" - 輸出子資料夾
    ]
    
    missing_nodes = []
    for node_key in required_nodes:
        if node_key not in mp:
            missing_nodes.append(node_key)
    
    if missing_nodes:
        raise KeyError(f"[stage3] config.yaml 缺少必要的節點設定：{missing_nodes}")

    # 讀取設定值
    per_dir_submit = bool(extras.get("per_dir_submit", True))
    unbounded_queue = bool(extras.get("unbounded_queue", True))
    queue_limit = None if unbounded_queue else pipe.max_inflight

    # 輸出路徑設定
    output_root_str = _ensure_trailing_slash(str(Path(paths.output_root)))
    
    print(f"[stage3] 從暫存讀取：{staging_root}")
    print(f"[stage3] 輸出到：{output_root_str}")

    # 確保輸出目錄存在
    Path(paths.output_root).mkdir(parents=True, exist_ok=True)

    all_results: List[Dict[str, Any]] = []

    def make_tasks_for_batch(batch_path: Path) -> List[Task]:
        """為指定批次建立所有換臉任務"""
        batch_name = batch_path.name
        target_dir = batch_path / "Target"
        face_dir = batch_path / "Face"
        
        # 找到第一張 Face 圖片（所有 Target 都用同一張 Face）
        face_img = first_image(face_dir)
        if not face_img:
            print(f"[stage3][skip] {batch_name} 的 Face 目錄沒有圖片")
            return []
        
        # 獲取 Target 目錄中的所有圖片
        target_images = list_images(target_dir, recursive=False)
        if not target_images:
            print(f"[stage3][skip] {batch_name} 的 Target 目錄沒有圖片")
            return []
            
        print(f"[stage3] {batch_name}: 發現 {len(target_images)} 張 Target 圖片，使用 Face={face_img.name}")
        
        batch_tasks: List[Task] = []
        
        for i, target_img in enumerate(target_images):
            print(f"[stage3] 處理 {batch_name}: Target={target_img.name} ({i+1}/{len(target_images)})")
            
            # 深拷貝工作流模板
            wf: Dict[str, Any] = _deepcopy_prompt(prompt_template)
        
            # 設定所有節點
            changes = []
            
            # 對 LoadImage 節點使用絕對路徑
            target_path = str(target_img.resolve())  # 絕對路徑
            face_path = str(face_img.resolve())      # 絕對路徑
            
            # 輸入圖片路徑設定
            _set_node_string(wf, mp["target_path_node"], target_path, "target_path_node")
            changes.append(f"target_path_node[{mp['target_path_node']}] = '{target_path}'")
            
            _set_node_string(wf, mp["face_path_node"], face_path, "face_path_node")
            changes.append(f"face_path_node[{mp['face_path_node']}] = '{face_path}'")
            
            # 輸出路徑設定
            _set_node_string(wf, mp["output_root_node"], output_root_str, "output_root_node")
            changes.append(f"output_root_node[{mp['output_root_node']}] = '{output_root_str}'")
            
            _set_node_string(wf, mp["output_subfolder_node"], batch_name, "output_subfolder_node")
            changes.append(f"output_subfolder_node[{mp['output_subfolder_node']}] = '{batch_name}'")
            
            # 調試輸出（只在第一張圖片時顯示詳細資訊）
            if i == 0:
                print(f"[stage3][debug] 批次 {batch_name} 節點設定：")
                for change in changes:
                    print(f"[stage3][debug]   {change}")
                
                # 驗證關鍵節點的設定
                target_node = wf.get(mp["target_path_node"])
                face_node = wf.get(mp["face_path_node"])
                
                if target_node:
                    print(f"[stage3][verify] Target 節點 {mp['target_path_node']} ({target_node.get('class_type', 'unknown')})")
                if face_node:
                    print(f"[stage3][verify] Face 節點 {mp['face_path_node']} ({face_node.get('class_type', 'unknown')})")
                
                print(f"[stage3][debug] 最終輸出路徑：{output_root_str}{batch_name}/")
            
            # 檢查圖片檔案是否存在（每張都檢查）
            if not target_img.exists():
                raise FileNotFoundError(f"[stage3] Target 圖片不存在：{target_img}")
            if not face_img.exists():
                raise FileNotFoundError(f"[stage3] Face 圖片不存在：{face_img}")
            
            # 建立任務 - 使用目標圖片的檔名來區分不同任務
            target_stem = target_img.stem  # 不含副檔名的檔名
            task_name = f"stage3:{batch_name}/{target_stem}"
            
            batch_tasks.append(Task(
                name=task_name,
                workflow=wf,
                max_retries=pipe.max_retries,
            ))
        
        print(f"[stage3] 批次 {batch_name} 共建立 {len(batch_tasks)} 個任務")
        return batch_tasks

    # 建立所有任務
    all_tasks: List[Task] = []
    for batch_path in batches:
        batch_tasks = make_tasks_for_batch(batch_path)
        all_tasks.extend(batch_tasks)
    
    if not all_tasks:
        print("[stage3] 沒有任何有效的任務可執行")
        return []

    print(f"[stage3] 總共準備 {len(all_tasks)} 個換臉任務...")
    
    # 執行送單策略
    if per_dir_submit:
        # 按批次分組執行
        current_batch = None
        current_batch_tasks = []
        
        for task in all_tasks:
            # 從任務名稱提取批次名（格式：stage3:01/filename）
            task_batch = task.name.split(':')[1].split('/')[0]
            
            if current_batch != task_batch:
                # 處理前一批次的任務
                if current_batch_tasks:
                    print(f"[stage3] 送出批次 {current_batch} 的 {len(current_batch_tasks)} 個任務...")
                    print(f"[stage3] 佇列設定：max_inflight={queue_limit}, poll_interval={pipe.poll_interval_sec}s")
                    
                    t0 = time.time()
                    batch_results = run_queue(
                        api, current_batch_tasks,
                        max_inflight=queue_limit,
                        poll_interval=pipe.poll_interval_sec,
                    )
                    dt = time.time() - t0
                    
                    ok = sum(1 for r in batch_results if "error" not in r)
                    fail = len(batch_results) - ok
                    print(f"[stage3] 批次 {current_batch} 完成：成功 {ok}，失敗 {fail}（耗時 {dt:.1f}s）")
                    
                    all_results.extend(batch_results)
                
                # 開始新批次
                current_batch = task_batch
                current_batch_tasks = [task]
            else:
                current_batch_tasks.append(task)
        
        # 處理最後一批次
        if current_batch_tasks:
            print(f"[stage3] 送出批次 {current_batch} 的 {len(current_batch_tasks)} 個任務...")
            
            t0 = time.time()
            batch_results = run_queue(
                api, current_batch_tasks,
                max_inflight=queue_limit,
                poll_interval=pipe.poll_interval_sec,
            )
            dt = time.time() - t0
            
            ok = sum(1 for r in batch_results if "error" not in r)
            fail = len(batch_results) - ok
            print(f"[stage3] 批次 {current_batch} 完成：成功 {ok}，失敗 {fail}（耗時 {dt:.1f}s）")
            
            all_results.extend(batch_results)
            
    else:
        # 一次送出所有任務
        print(f"[stage3] 一次送出所有 {len(all_tasks)} 個任務...")
        print(f"[stage3] 佇列設定：max_inflight={queue_limit}, poll_interval={pipe.poll_interval_sec}s")
        
        t0 = time.time()
        all_results = run_queue(
            api, all_tasks,
            max_inflight=queue_limit,
            poll_interval=pipe.poll_interval_sec,
        )
        dt = time.time() - t0
        
        total_success = sum(1 for r in all_results if "error" not in r)
        total_failed = len(all_results) - total_success
        print(f"[stage3] 全部完成：成功 {total_success}，失敗 {total_failed}（耗時 {dt:.1f}s）")
    
    # 結果統計
    total_success = sum(1 for r in all_results if "error" not in r)
    total_failed = len(all_results) - total_success
    
    print(f"\n[stage3] === 第三階段完成 ===")
    print(f"[stage3] 總任務數：{len(all_results)}")
    print(f"[stage3] 成功：✅ {total_success}")
    print(f"[stage3] 失敗：❌ {total_failed}")
    print(f"[stage3] 總耗時：{dt:.1f}s")
    
    # 顯示失敗任務詳情
    if total_failed > 0:
        print(f"[stage3][error] 失敗任務詳情：")
        for i, r in enumerate(all_results):
            if "error" in r:
                task_name = tasks[i].name if i < len(tasks) else f"Task #{i+1}"
                print(f"[stage3][error]   {task_name}: {r.get('error', 'Unknown error')}")
        print(f"[stage3][warn] 有 {total_failed} 個任務失敗，請檢查上方錯誤訊息")
    else:
        print(f"[stage3][info] 🎉 所有換臉任務成功完成！")
        print(f"[stage3][info] 結果已輸出到：{output_root_str}")
    
    return all_results