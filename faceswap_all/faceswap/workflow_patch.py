#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations
from typing import Dict, Any, Optional, Tuple

# === utilities ===

def _normalize_nid(nid: str) -> str:
    """
    將 "117:0" / "117:any" / "117 " 等寫法規整為 "117"
    """
    if not isinstance(nid, str):
        nid = str(nid)
    nid = nid.strip()
    if ":" in nid:
        nid = nid.split(":", 1)[0].strip()
    return nid

def _resolve_node(workflow: Dict[str, Any], nid: str) -> Tuple[Optional[str], Optional[Dict[str, Any]], list]:
    """
    嘗試以多種方式解析 node-id，回傳 (實際鍵, node物件, 候選鍵list)
    候選鍵用於錯誤訊息提示。
    """
    if not isinstance(workflow, dict) or not workflow:
        return None, None, []

    keys = list(workflow.keys())
    # 1) 原樣
    if nid in workflow:
        return nid, workflow[nid], []
    # 2) 規整後（移除 :0）
    nid_norm = _normalize_nid(nid)
    if nid_norm in workflow:
        return nid_norm, workflow[nid_norm], []
    # 3) 模糊找：開頭/結尾匹配
    candidates = []
    for k in keys:
        if k == nid_norm or k.startswith(nid_norm) or nid_norm.startswith(k):
            candidates.append(k)
    if candidates:
        # 優先選精確等值，再選長度最接近者
        candidates.sort(key=lambda x: (x != nid_norm, abs(len(x) - len(nid_norm))))
        pick = candidates[0]
        return pick, workflow.get(pick), candidates
    # 4) 沒找到
    return None, None, []

# === patch helpers ===

def set_input(node: Dict[str, Any], key: str, value: Any) -> None:
    node.setdefault("inputs", {})
    node["inputs"][key] = value

def set_string_like(node: Dict[str, Any], value: str) -> None:
    """
    盡量投遞到對的字串欄位：
      - 若已有 value/text/string/path/input/filename/folder 其中任一鍵，優先覆寫那個
      - 否則新增 'value'
    """
    node.setdefault("inputs", {})
    inputs = node["inputs"]
    pref_keys = ("value", "text", "string", "path", "input", "filename", "folder")
    # 已存在者優先
    for k in pref_keys:
        if k in inputs:
            inputs[k] = value
            return
    # 皆不存在 → 放 value
    inputs["value"] = value

def patch_by_map(
    workflow: Dict[str, Any],
    mapping: Dict[str, Any],
    *,
    strict: bool = False,
    verbose: bool = True,
) -> None:
    """
    mapping 例：
      {
        "117:0": {"value": "/workspace/來源/"},
        "118:0": {"value": "01"},
        "119:0": {"value": "Target"},
        "240:0": {"text": "/workspace/暫存"}
      }
    - 支援 "117" 與 "117:0" 兩種寫法（自動規整）
    - 找不到節點時：
        strict=False → 輸出 warn 並略過該項，流程不中斷
        strict=True  → 立刻拋出 KeyError
    """
    if not isinstance(workflow, dict):
        raise TypeError("workflow 應為 dict（ComfyUI 工作流 JSON）")

    for nid_raw, inputs in mapping.items():
        # 解析 node
        key, node, candidates = _resolve_node(workflow, str(nid_raw))
        if node is None:
            msg = f"[patch] 找不到節點 ID：{nid_raw}；工作流鍵樣本（前 10 個）：{list(workflow.keys())[:10]}"
            # 補充 class_type 快速診斷
            classes = []
            for k in list(workflow.keys())[:20]:
                ct = workflow.get(k, {}).get("class_type")
                classes.append(f"{k}:{ct}")
            if verbose:
                print(msg)
                if candidates:
                    print(f"       可能候選：{candidates[:5]}")
                print(f"       節點類型樣本：{classes}")
            if strict:
                raise KeyError(f"工作流中找不到節點 ID：{nid_raw}")
            else:
                continue  # 忽略這個鍵，處理其它鍵

        # 實際投遞
        if isinstance(inputs, dict):
            node.setdefault("inputs", {})
            node["inputs"].update(inputs)
        else:
            set_string_like(node, str(inputs))
