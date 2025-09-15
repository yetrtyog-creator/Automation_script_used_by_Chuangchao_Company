#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations
from typing import Dict, Any

# The workflow JSON is a dict keyed by node-id string -> {"class_type": ..., "inputs": {...}}
# We provide helpers to set string-like inputs in a forgiving manner.

def set_input(node: Dict[str, Any], key: str, value: Any) -> None:
    node.setdefault("inputs", {})
    node["inputs"][key] = value

def set_string_like(node: Dict[str, Any], value: str) -> None:
    """Try common string input keys if caller doesn't know the exact one."""
    node.setdefault("inputs", {})
    for k in ("value", "text", "string", "path", "input", "filename", "folder"):
        if k in node["inputs"] or not node["inputs"]:
            node["inputs"][k] = value
            return
    # As fallback, set 'value'
    node["inputs"]["value"] = value

def patch_by_map(workflow: Dict[str, Any], mapping: Dict[str, Any]) -> None:
    """
    mapping example:
    {
      "117:0": {"value": "/workspace/來源/"},
      "117:1": {"value": "01"},
      "117:3": {"value": "Target"},
      "240:0": {"text": "/workspace/暫存"}
    }
    """
    for nid, inputs in mapping.items():
        node = workflow.get(nid)
        if not node:
            raise KeyError(f"工作流中找不到節點 ID：{nid}")
        if isinstance(inputs, dict):
            if inputs:
                # exact inputs
                node.setdefault("inputs", {})
                node["inputs"].update(inputs)
            else:
                # if empty dict → no-op
                pass
        else:
            # treat as string-like
            set_string_like(node, str(inputs))
