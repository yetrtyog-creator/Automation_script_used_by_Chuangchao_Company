# stage1_api.py
# -*- coding: utf-8 -*-
"""
Stage 1 Driver — 來源影像 → 嵌入向量（Qdrant） 的請求產生與送出（只改「字串」欄位）

- 自動設定 title=「字符串」的節點：
  122（collection 前綴）、117:0（來源根）、117:1（批次）、117:2（分隔 " // "）、117:3（Target/Face）
- 不處理 4 併發；請在 main.py 裡控管。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Optional, Sequence, Tuple, Dict, Any
import copy
import json
import uuid

from comfy_api import ComfyAPI, PipelineError
from dir_schema import check_source_schema, SchemaError
from path_utils import (
    list_images,
    get_parent_name,
    get_grandparent_name,
    extract_index_from_name,
)
from model.settings import get_settings

Assignment = Tuple[str | int, str, str | Callable[[Dict[str, Any]], str]]

@dataclass(frozen=True)
class Stage1Job:
    image_path: Path
    prompt_mapping: Dict[str, Any]
    client_id: str
    base_url: str

    def payload(self) -> Dict[str, Any]:
        return {"prompt": self.prompt_mapping, "client_id": self.client_id}

    def submit(self) -> str:
        api = ComfyAPI(self.base_url)
        api.set_prompt(self.prompt_mapping)
        return api.post_prompt(client_id=self.client_id)

def _parse_path_segments(path: str) -> list[str | int]:
    out: list[str | int] = []
    buf = ""
    i = 0
    while i < len(path):
        c = path[i]
        if c == '.':
            if buf:
                out.append(buf); buf = ""
            i += 1
        elif c == '[':
            if buf:
                out.append(buf); buf = ""
            j = path.find(']', i + 1)
            if j == -1:
                raise PipelineError(f"path syntax error: missing ']' in {path!r}")
            idx_str = path[i + 1:j].strip()
            if not idx_str.isdigit():
                raise PipelineError(f"path index must be integer: {path!r}")
            out.append(int(idx_str))
            i = j + 1
        else:
            buf += c; i += 1
    if buf:
        out.append(buf)
    return out

def _set_by_path(root: dict, node_id: int | str, path: str, value: Any) -> None:
    nid = str(node_id)
    if nid not in root:
        raise PipelineError(f"node_id={nid} not found in prompt mapping")
    cur: Any = root[nid]
    segs = _parse_path_segments(path)
    for k in segs[:-1]:
        if isinstance(k, int):
            if not isinstance(cur, list):
                raise PipelineError(f"path leads to list where current is not list: node {nid}, path {path}")
            if k >= len(cur):
                cur.extend([None] * (k - len(cur) + 1))
            if cur[k] is None:
                cur[k] = {}
            cur = cur[k]
        else:
            if not isinstance(cur, dict):
                raise PipelineError(f"path traversal expects dict at {k!r}: node {nid}, path {path}")
            if k not in cur or cur[k] is None:
                cur[k] = {}
            cur = cur[k]
    last = segs[-1]
    sval = str(value)
    if isinstance(last, int):
        if not isinstance(cur, list):
            raise PipelineError(f"path final expects list for index write: node {nid}, path {path}")
        if last >= len(cur):
            cur.extend([None] * (last - len(cur) + 1))
        cur[last] = sval
    else:
        if not isinstance(cur, dict):
            raise PipelineError(f"path final expects dict for key write: node {nid}, path {path}")
        cur[last] = sval

def _render_value(tpl_or_func: str | Callable[[Dict[str, Any]], str], ctx: Dict[str, Any]) -> str:
    if callable(tpl_or_func):
        return str(tpl_or_func(ctx))
    return str(tpl_or_func).format(**ctx)

def _build_ctx(img: Path, *, source_root: Path, collection_name: str) -> Dict[str, Any]:
    parent = get_parent_name(img) or ""      # 'Face' / 'Target'
    grandp = get_grandparent_name(img) or "" # '01'
    try:
        rel = str(img.resolve().relative_to(source_root.resolve()))
    except Exception:
        rel = img.name
    return {
        "abs_path": str(img.resolve()),
        "rel_path": rel,
        "dir": str(img.parent.resolve()),
        "name": img.name,
        "stem": img.stem,
        "suffix": img.suffix.lstrip(".").lower(),
        "parent": parent,      # 'Face' / 'Target'
        "grandparent": grandp, # '01'
        "batch": grandp,
        "kind": parent,
        "collection": collection_name,
        "collection_sub": f"{collection_name}/{parent}" if parent else collection_name,
        "file_index": extract_index_from_name(img.stem) or "",
        "batch_index": extract_index_from_name(grandp) or "",
    }

def _apply_assignments(prompt: Dict[str, Any],
                       assignments: Sequence[Assignment],
                       ctx: Dict[str, Any]) -> None:
    for (node_id, path, tpl) in assignments:
        val = _render_value(tpl, ctx)
        _set_by_path(prompt, node_id, path, val)

def _default_string_nodes_assignments(
    *,
    source_root: Path,
    collection_name: str
) -> tuple[list[Assignment], list[Assignment]]:
    """
    內建“字符串”自動填值：
      - 117:0 -> inputs.value = <source_root POSIX，結尾強制 '/' >
      - 117:1 -> inputs.value = {batch}  (如 '01')
      - 117:2 -> inputs.value = '//'     (Linux)
      - 117:3 -> inputs.value = {kind}   ('Target' 或 'Face')
      - 122   -> inputs.value = ' ' + collection_name   （保留示例中的前置空白）
    """
    # 確保 117:0 末尾只有一個 '/'
    src_posix = source_root.as_posix().rstrip('/') + '/'
    constant = [
        ("117:0", "inputs.value", src_posix),
        ("117:2", "inputs.value", "//"),
        ("122",   "inputs.value", f" {collection_name}"),
    ]
    per_image = [
        ("117:1", "inputs.value", "{batch}"),
        ("117:3", "inputs.value", "{kind}"),
    ]
    return constant, per_image

def prepare_stage1_jobs(
    *,
    base_url: Optional[str] = None,
    source_root: Optional[Path | str] = None,
    workflow_path: Optional[Path | str] = None,
    collection_name: Optional[str] = None,
    require_images: bool = True,
    recursive_image_search: bool = True,
    follow_symlinks: bool = False,
    constant_assignments: Sequence[Assignment] = (),
    per_image_assignments: Sequence[Assignment] = (),
    client_id: Optional[str] = None,
    config_path: Optional[str | Path] = None,
) -> list[Stage1Job]:
    # 1) 設定來源（未提供則從設定抓）
    if base_url is None:
        port = get_settings('comfyui.port', config_path=config_path, as_dict=False)
        base_url = f"http://127.0.0.1:{port}"
    if workflow_path is None:
        workflow_path = get_settings('workflow_paths.stage1', config_path=config_path, as_dict=False)
    if source_root is None:
        source_root = get_settings('paths_source_root', config_path=config_path, as_dict=False)
    if collection_name is None:
        collection_name = get_settings('pipeline.collection_name', config_path=config_path, as_dict=False)

    source_root = Path(source_root)
    workflow_path = Path(workflow_path)

    # 2) 規範檢查
    try:
        check_source_schema(
            source_root,
            require_images=require_images,
            recursive_image_search=recursive_image_search,
        )
    except SchemaError as e:
        raise PipelineError(f"[stage1] source schema invalid: {e}") from e

    # 3) 讀 API 版 prompt 映射
    try:
        with open(workflow_path, "r", encoding="utf-8") as f:
            base_prompt: Dict[str, Any] = json.load(f)
        if not isinstance(base_prompt, dict):
            raise ValueError("stage1 workflow JSON is not a mapping")
    except Exception as e:
        raise PipelineError(f"[stage1] failed to load stage1 prompt JSON: {e}") from e

    # 4) 列舉影像
    imgs = list(list_images(source_root, recursive=recursive_image_search, follow_symlinks=follow_symlinks, sort=True))
    if not imgs:
        raise PipelineError("[stage1] no images found under source_root")

    # 5) 若使用者未提供 assignments，啟用內建“字符串”寫入
    if not constant_assignments and not per_image_assignments:
        c_def, p_def = _default_string_nodes_assignments(
            source_root=source_root,
            collection_name=collection_name,
        )
        constant_assignments = c_def
        per_image_assignments = p_def

    cid = client_id or str(uuid.uuid4())
    jobs: list[Stage1Job] = []

    for img in imgs:
        ctx = _build_ctx(img, source_root=source_root, collection_name=collection_name)
        prompt_i = copy.deepcopy(base_prompt)
        if constant_assignments:
            _apply_assignments(prompt_i, constant_assignments, ctx)
        if per_image_assignments:
            _apply_assignments(prompt_i, per_image_assignments, ctx)
        jobs.append(Stage1Job(
            image_path=img,
            prompt_mapping=prompt_i,
            client_id=cid,
            base_url=base_url,
        ))
    return jobs

def submit_stage1_serial(jobs: Iterable[Stage1Job], *, check_ready: bool = True) -> list[str]:
    prompt_ids: list[str] = []
    api: Optional[ComfyAPI] = None
    for job in jobs:
        if check_ready:
            if api is None:
                api = ComfyAPI(job.base_url)
            if not api.is_ready():
                raise PipelineError(f"[stage1] ComfyUI not ready at {job.base_url}")
        prompt_ids.append(job.submit())
    return prompt_ids
