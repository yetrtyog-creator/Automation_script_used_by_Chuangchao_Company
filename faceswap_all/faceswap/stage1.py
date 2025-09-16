#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

"""
stage1.py — 依照「修正後的 config.yaml」改寫版本（只改字串節點）

功能與修正
---------
1) 路徑拼字走四個「PrimitiveString/Text」節點：
   - base_dir_node (text_a)      -> "/workspace/來源/"（尾端自動補 '/')
   - batch_name_node (text_b)    -> "01"
   - separator_node (text_c)     -> "/"   ← 修正重點
   - subfolder_node (text_d)     -> "Target" 或 "Face"
   * 上述四者餵給 GUI 內的 Text Concatenate（例如節點 37）

2) Qdrant collection 名稱
   - 由 extras.collection_name_prefix（或 pipeline.collection_name） + batch + subfolder
   - 例如 "Face_Changing" + "01" + "Face" => "Face_Changing01Face"
   - 直接覆寫 Qdrant 節點的 inputs.collection / inputs.collection_name 為字串（避免被「路徑」連線污染）

3) Load Image Batch
   - 直接設定 inputs.index = 0..N-1
   - 若 extras.mode / extras.pattern 存在則一併覆寫（保持一致性）

4) ensure_sink_from / sink_type
   - 若指定，如 "46:0" + SaveImage，則在每個任務加上一個 SaveImage 匯出節點以便 history 追蹤

5) 送單策略
   - per_dir_submit=True 時對每個資料夾（01/Target、01/Face...）各自送完
   - unbounded_queue=True 時，以 None 交給 run_queue（由 ComfyUI 端排隊）
"""

import json
import random
import time
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple, Iterable

from .config_loader import StageMapping, PathsConfig, PipelineConfig
from .folder_rules import ensure_source_layout, list_images
from .scheduler import Task, run_queue
from .comfy_api import ComfyAPI


# -------------------------------
# 小工具
# -------------------------------
def _deepcopy_prompt(prompt: Dict[str, Any]) -> Dict[str, Any]:
    return json.loads(json.dumps(prompt))


def _norm_subfolder(name: str) -> str:
    s = name.strip()
    if s.lower() == "face":
        return "Face"
    if s.lower() == "target":
        return "Target"
    return s


def _ensure_trailing_slash(p: str) -> str:
    return p if p.endswith("/") else p + "/"


def _set_stringish_input(node: Dict[str, Any], value: str) -> None:
    """
    盡量相容 ComfyUI 的字串型節點：常見欄位有 'string' / 'text' / 'value'
    若都沒有，預設寫入 'string'
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
        raise KeyError(f"[stage1] 指定的 {label} 節點 id='{node_id}' 不存在於 workflow JSON")
    _set_stringish_input(node, value)


def _find_nodes_by_class_contains(wf: Dict[str, Any], substrs: Iterable[str]) -> List[str]:
    out: List[str] = []
    for nid, node in wf.items():
        ctype = str(node.get("class_type", ""))
        for s in substrs:
            if s in ctype:
                out.append(str(nid))
                break
    return out


def _set_load_image_batch_fields(wf: Dict[str, Any], index: int, *,
                                 mode: Optional[str] = None,
                                 pattern: Optional[str] = None) -> List[str]:
    """
    尋找所有「Load Image Batch」類節點並設定 index / mode / pattern
    回傳被設定到的節點 id 清單
    """
    touched: List[str] = []
    for nid, node in wf.items():
        ctype = str(node.get("class_type", ""))
        # 容錯：有些包可能寫成 "LoadImageBatch"
        if "Load Image Batch" in ctype or "LoadImageBatch" in ctype:
            node.setdefault("inputs", {})
            node["inputs"]["index"] = index
            if mode is not None:
                node["inputs"]["mode"] = mode
            if pattern is not None:
                node["inputs"]["pattern"] = pattern
            touched.append(str(nid))
    if not touched:
        raise RuntimeError("[stage1] 找不到 Load Image Batch 類節點，請檢查工作流或節點命名")
    return touched


def _set_qdrant_collection(wf: Dict[str, Any], collection: str) -> List[str]:
    """
    直接把 Qdrant 類節點的 collection/collection_name 設為字串
    （避免被任何 Text Concatenate 或路徑連線覆蓋）
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
        # 若欄位不存在，仍強制寫入 "collection"
        if not set_any:
            ins["collection"] = collection
            set_any = True
        if set_any:
            touched.append(str(nid))
    # 不硬性要求一定要有 Qdrant 節點（容許你工作流已經把 collection 固定）
    return touched


def _ensure_sink(wf: Dict[str, Any], src: str, sink_type: str = "SaveImage") -> str:
    """
    依 extras.ensure_sink_from="46:0" 建立 Sink 節點（預設 SaveImage）
    回傳新節點 id
    """
    if ":" not in src:
        raise ValueError(f"[stage1] ensure_sink_from 格式需為 'node_id:output_index'，取得: {src}")
    src_id, out_idx_s = src.split(":", 1)
    src_id = str(src_id)
    out_idx = int(out_idx_s)

    # 產生新 id
    numeric = [int(k) for k in wf.keys() if str(k).isdigit()]
    new_id = str(max(numeric) + 1 if numeric else 10000)

    # 依 SaveImage 約定：inputs.images = ["<src_id>", <out_idx>]
    wf[new_id] = {
        "class_type": sink_type,
        "inputs": {"images": [src_id, out_idx]},
    }
    return new_id


# -------------------------------
# 主流程
# -------------------------------
def run_stage1(api: ComfyAPI, mapping: StageMapping, paths: PathsConfig, pipe: PipelineConfig) -> List[Dict[str, Any]]:
    """
    依 config.yaml：workflows.stage1.mappings 的四個字串節點來組路徑，
    並設定 Qdrant collection 名稱（Face_Changing01Face）。
    """
    print("[stage1] 驗證來源結構 ...")
    batches = ensure_source_layout(Path(paths.source_root))
    print(f"[stage1] 發現 {len(batches)} 個批次：{[b.name for b in batches]}")

    wf_path = Path(mapping.file)
    prompt_template: Dict[str, Any] = json.loads(wf_path.read_text(encoding="utf-8"))

    mp = mapping.mappings or {}
    extras = mapping.extras or {}

    # 讀取字串節點 id
    base_dir_node = mp.get("base_dir_node")
    batch_name_node = mp.get("batch_name_node")
    separator_node = mp.get("separator_node")
    subfolder_node = mp.get("subfolder_node")

    if not all([base_dir_node, batch_name_node, separator_node, subfolder_node]):
        raise KeyError("[stage1] config.yaml 缺少 base_dir_node/batch_name_node/separator_node/subfolder_node 任一設定")

    recursive = bool(extras.get("recursive", True))
    per_dir_submit = bool(extras.get("per_dir_submit", True))
    unbounded_queue = bool(extras.get("unbounded_queue", True))
    queue_limit = None if unbounded_queue else pipe.max_inflight

    pattern = extras.get("pattern")
    mode = extras.get("mode")

    # 集合前綴
    collection_prefix = (
        extras.get("collection_name_prefix")
        or getattr(pipe, "collection_name", None)
        or "Face_Changing"
    )

    ensure_sink_from = extras.get("ensure_sink_from")  # 例如 "46:0"
    sink_type = extras.get("sink_type", "SaveImage")

    # 統一來源根路徑尾斜線
    source_root_str = _ensure_trailing_slash(str(Path(paths.source_root)))

    all_results: List[Dict[str, Any]] = []

    def make_tasks_for_dir(batch_name: str, sub: str) -> List[Task]:
        sub_norm = _norm_subfolder(sub)
        data_dir = Path(paths.source_root) / batch_name / sub_norm
        images = list_images(data_dir, recursive=recursive)  # 若你的 list_images 支援 pattern，可自行加參數
        num = len(images)
        if num <= 0:
            print(f"[stage1][skip] 無圖片：{data_dir}")
            return []

        print(f"[stage1] {batch_name}/{sub_norm} 有 {num} 張圖片")
        expected_path = f"{source_root_str}{batch_name}/{sub_norm}"

        dir_tasks: List[Task] = []
        for img_idx in range(num):
            wf: Dict[str, Any] = _deepcopy_prompt(prompt_template)

            # 1) 設定四個字串節點
            _set_node_string(wf, base_dir_node, source_root_str, "base_dir_node")
            _set_node_string(wf, batch_name_node, batch_name, "batch_name_node")
            _set_node_string(wf, separator_node, "/", "separator_node")   # ← 修正重點
            _set_node_string(wf, subfolder_node, sub_norm, "subfolder_node")

            # 2) 設定 Load Image Batch 的 index / mode / pattern
            touched_libb = _set_load_image_batch_fields(wf, index=img_idx, mode=mode, pattern=pattern)

            # 3) 設定 Qdrant collection = prefix + batch + sub
            collection = f"{collection_prefix}{batch_name}{sub_norm}"
            touched_qdrant = _set_qdrant_collection(wf, collection)

            # 4) 需要輸出 sink（可追蹤 history）
            if ensure_sink_from:
                try:
                    _ensure_sink(wf, ensure_sink_from, sink_type=sink_type)
                except Exception as e:
                    print(f"[stage1][warn] ensure_sink_from 建立失敗：{e}")

            # 調試輸出（每個資料夾的第一張）
            if img_idx == 0:
                print(f"[stage1][debug] 目錄期望路徑: {expected_path}")
                print(f"[stage1][debug] 組合應為   : {source_root_str}{batch_name}/" + sub_norm)
                print(f"[stage1][debug] Load Image Batch 節點  : {touched_libb}")
                if touched_qdrant:
                    print(f"[stage1][debug] Qdrant 節點已設 collection='{collection}' -> {touched_qdrant}")
                else:
                    print(f"[stage1][debug] 找不到 Qdrant 節點可寫入 collection（若工作流已固定也可忽略）")

            dir_tasks.append(
                Task(
                    name=f"{batch_name}/{sub_norm} #{img_idx+1}/{num}",
                    workflow=wf,
                    max_retries=pipe.max_retries,
                )
            )
        return dir_tasks

    # 送單
    if per_dir_submit:
        for b in batches:
            for sub in ("Target", "Face"):
                tasks = make_tasks_for_dir(b.name, sub)
                if not tasks:
                    continue
                print(f"[stage1] 送出 {len(tasks)} 個任務（{b.name}/{sub}）...")
                print(f"[stage1] 使用設定：max_inflight={queue_limit}, poll_interval={pipe.poll_interval_sec}")
                t0 = time.time()
                results = run_queue(
                    api, tasks,
                    max_inflight=queue_limit,
                    poll_interval=pipe.poll_interval_sec,
                )
                dt = time.time() - t0
                ok = sum(1 for r in results if "error" not in r)
                fail = len(results) - ok
                print(f"[stage1] {b.name}/{sub} 完成：成功 {ok}，失敗 {fail}（{dt:.1f}s）")
                all_results.extend(results)
    else:
        tasks: List[Task] = []
        for b in batches:
            for sub in ("Target", "Face"):
                tasks.extend(make_tasks_for_dir(b.name, sub))
        print(f"[stage1] 送出 {len(tasks)} 個任務...")
        all_results = run_queue(
            api, tasks,
            max_inflight=queue_limit,
            poll_interval=pipe.poll_interval_sec,
        )

    # 總結
    total_success = sum(1 for r in all_results if "error" not in r)
    total_failed = len(all_results) - total_success
    print(f"[stage1] 全部完成：總成功 {total_success}，總失敗 {total_failed}")
    if total_failed > 0:
        print("[stage1][warn] 部分任務失敗（詳見上方錯誤列印）")

    return all_results
