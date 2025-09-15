#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations
import json
from pathlib import Path
from typing import List
from .config_loader import StageMapping, PathsConfig, PipelineConfig
from .folder_rules import ensure_staging_layout, first_image
from .workflow_patch import patch_by_map
from .scheduler import Task, run_queue
from .comfy_api import ComfyAPI

def run_stage3(api: ComfyAPI, mapping: StageMapping, paths: PathsConfig, pipe: PipelineConfig) -> None:
    print("[stage3] 驗證暫存結構 ...")
    batches = ensure_staging_layout(Path(paths.staging_root))
    print(f"[stage3] 發現 {len(batches)} 個批次")

    wf_path = Path(mapping.file)
    prompt_template = json.loads(wf_path.read_text(encoding="utf-8"))

    tasks: List[Task] = []
    for b in batches:
        tgt_dir = b / "Target"
        face_dir = b / "Face"
        tgt = first_image(tgt_dir)
        fac = first_image(face_dir)

        patch = {}
        tgt_node = mapping.mappings.get("target_path_node")
        face_node = mapping.mappings.get("face_path_node")
        out_root_node = mapping.mappings.get("output_root_node")
        batch_node = mapping.mappings.get("batch_name_node")  # optional

        if not all([tgt_node, face_node, out_root_node]):
            raise KeyError("stage3.mappings 需要 target_path_node / face_path_node / output_root_node 皆有")

        patch[tgt_node] = {"value": str(tgt)}
        patch[face_node] = {"value": str(fac)}
        patch[out_root_node] = {"value": str(Path(paths.output_root))}
        if batch_node:
            patch[batch_node] = {"value": b.name}

        wf = json.loads(json.dumps(prompt_template))
        patch_by_map(wf, patch)
        tasks.append(Task(name=f"{b.name}", workflow=wf, max_retries=pipe.max_retries))

    print(f"[stage3] 準備送出 {len(tasks)} 個任務 ...")
    _ = run_queue(api, tasks, max_inflight=pipe.max_inflight, poll_interval=pipe.poll_interval_sec)
    print("[stage3] 完成")
