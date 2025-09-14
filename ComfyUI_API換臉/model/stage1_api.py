# 只改 PrimitiveString 節點，並用 comfy_api.post_prompt 送出第一階段工作流
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable
import copy
import json

# 你的薄 REST：需提供 post_prompt(graph: dict) -> str
import comfy_api  # 假定你已有 comfy_api.py


# ====== 這裡唯一列出會被修改的「字串節點」ID/欄位 ======
# 只動以下 5 個節點/欄位，其它一律不動
STRING_NODE_ADDRS = {
    "SOURCE_ROOT": ("117:0", "inputs.value"),   # 來源根路徑  e.g. r"C:\...\模擬用途(用於測試工作流)\"
    "SERIAL":      ("117:1", "inputs.value"),   # 數字序列    e.g. "01"
    "PATH_SEP":    ("117:2", "inputs.value"),   # 路徑分隔符  e.g. "\\" 或 "/"
    "CATEGORY":    ("117:3", "inputs.value"),   # "Target" 或 "Face"
    "COLL_PREFIX": ("122",   "inputs.value"),   # Qdrant collection 前綴（原工作流帶前導空白）
}


@dataclass(frozen=True)
class PrimitiveStringsConfig:
    """第一階段只需的 5 個字串值。"""
    source_root: str           # r"C:\...\模擬用途(用於測試工作流)\"
    serial_folder: str         # "01"、"02"...
    path_sep: str              # "\\"（Windows）或 "/"（Linux）
    category: str              # "Target" 或 "Face"
    collection_prefix: str     # "Face_Changing"（原工作流預期前面要一個空白）

    # 是否自動在 collection_prefix 前加一個前導空白，以符合你現有工作流的 Text Concatenate（clean_whitespace=true）
    add_leading_space_for_prefix: bool = True


def _load_graph(graph_or_path: Dict[str, Any] | str | Path) -> Dict[str, Any]:
    if isinstance(graph_or_path, (str, Path)):
        with open(graph_or_path, "r", encoding="utf-8") as f:
            return json.load(f)
    # assume dict
    return copy.deepcopy(graph_or_path)


def _ensure_path(obj: Dict[str, Any], dotted: str) -> Dict[str, Any]:
    """確保 dotted path 之前的層級存在，回傳最後一層的 dict。"""
    parts = dotted.split(".")
    cur: Dict[str, Any] = obj
    for k in parts[:-1]:
        if k not in cur or not isinstance(cur[k], dict):
            cur[k] = {}
        cur = cur[k]
    return cur


def _apply_string_updates(graph: Dict[str, Any], cfg: PrimitiveStringsConfig) -> Dict[str, Any]:
    """只把 5 個 PrimitiveString 寫回去。其餘節點完全不動。"""
    g = copy.deepcopy(graph)

    assignments = {
        "SOURCE_ROOT": str(cfg.source_root),
        "SERIAL":      str(cfg.serial_folder),
        "PATH_SEP":    str(cfg.path_sep),
        "CATEGORY":    str(cfg.category),
        "COLL_PREFIX": (" " + cfg.collection_prefix) if cfg.add_leading_space_for_prefix
                       else str(cfg.collection_prefix),
    }

    for key, value in assignments.items():
        node_id, field_path = STRING_NODE_ADDRS[key]
        if node_id not in g:
            raise KeyError(f"[stage1_strings] Node '{node_id}' not found in graph.")
        node_obj = g[node_id]
        parent = _ensure_path(node_obj, field_path)
        last_key = field_path.split(".")[-1]
        parent[last_key] = value

    return g


# ====== 對 main.py 提供的唯一入口 ======
def run_stage1_strings_only(
    base_graph: Dict[str, Any] | str | Path,
    cfg: PrimitiveStringsConfig,
) -> str:
    """
    - 載入/複製 base_graph
    - 只更新 5 個 PrimitiveString 節點
    - 呼叫 comfy_api.post_prompt(...) 送進 ComfyUI 佇列
    - 回傳 prompt_id
    """
    graph_dict = _load_graph(base_graph)
    graph_ready = _apply_string_updates(graph_dict, cfg)
    prompt_id = comfy_api.post_prompt(graph_ready)
    return prompt_id