# -*- coding: utf-8 -*-
"""
Stage 1 Driver — 來源影像 → 嵌入向量（Qdrant） 的請求產生與送出

重點功能
--------
1) 自動設定「字串類節點」：
   - Collection（單一字串）：預設挑 PrimitiveString/單字串輸入節點（可用環境變數覆寫）
   - Title 四段（來源根/批次/分隔符/Target|Face）：
       a. 優先使用「字串列表」節點（inputs.strings / inputs.values / inputs.list / inputs.texts / inputs.items）
       b. 若找不到列表，fallback 為「四獨立 PrimitiveString 節點」群組（如 117:0,1,2,3）
   - 所有鍵位與節點皆可環境變數覆寫（見下方「環境變數」）

2) Debug/預檢
   - COMFY_STAGE1_DEBUG=1：在送出前列印本次圖片、四段字串、collection 與 staged 寫入清單
   - COMFY_STAGE1_BREAK_BEFORE_POST=1：只預覽 staged 寫入，不送出到 ComfyUI（乾跑）
   - COMFY_STAGE1_STRICT=1：檢查檔案是否存在，不存在則立即丟錯

3) 綁定載圖節點（建議開啟）
   - 預設會把「Load Image Batch」節點綁定到當前 job 的資料夾與檔名，避免上游拿不到圖導致 NoneType
   - 可用 COMFY_STAGE1_BIND_LOADER=0 關閉；COMFY_STAGE1_LOADER_NODE 指定載圖節點 ID（預設 29）

4) 任務流
   - prepare_stage1_jobs()：讀設定與 workflow，檢查來源結構，為每張圖建立一個 Stage1Job
   - submit_stage1_jobs(jobs, ...)：以固定上限送入 ComfyUI /prompt 佇列並輪詢 /history，處理重試

環境變數（可選）
----------------
- COMFYUI_PORT=8199                # 若設定檔未提供 base_url/port，可用此指定
- COMFY_STAGE1_DEBUG=1             # 列印預檢資訊
- COMFY_STAGE1_BREAK_BEFORE_POST=1 # 僅預覽 staged 寫入，不送出
- COMFY_STAGE1_STRICT=1            # 檔案不存在即丟錯
- COMFY_STAGE1_BIND_LOADER=1       # 是否綁定載圖節點（預設 1）
- COMFY_STAGE1_LOADER_NODE=29      # 載圖節點 id（例如 Load Image Batch）

# 覆寫「字串類節點」解析
- COMFY_STAGE1_COLLECTION_NODE=122
- COMFY_STAGE1_COLLECTION_KEY=value              # 寫入 inputs.value
- COMFY_STAGE1_STRINGS_NODE=XXX
- COMFY_STAGE1_STRINGS_KEY=strings               # 寫入 inputs.<KEY>[i]
- COMFY_STAGE1_SINGLES_IDS="117:0,117:1,117:2,117:3"
- COMFY_STAGE1_SINGLES_KEY=value                 # 寫入 inputs.value
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Any, List, Tuple, Iterable, Optional, Union, Literal
import copy
import json
import os
import re
import time
import uuid

# ===== 相依：comfy_api（模組級便捷函式）=====
try:
    # 假設 comfy_api.py 暴露 post_prompt/get_history 與 PipelineError
    from .comfy_api import post_prompt as comfy_post_prompt, get_history as comfy_get_history, PipelineError  # type: ignore
except Exception:
    from comfy_api import post_prompt as comfy_post_prompt, get_history as comfy_get_history, PipelineError  # type: ignore

# ===== 相依：settings / dir_schema / path_utils =====
try:
    from .settings import get_settings  # type: ignore
except Exception:
    from settings import get_settings  # type: ignore

try:
    from .dir_schema import check_source_schema, SchemaError  # type: ignore
except Exception:
    class SchemaError(Exception): ...
    def check_source_schema(root: Path, **_: Any) -> List[str]:
        # 極簡後備：挑選 root 下符合 \d{1,4} 的資料夾名（且有 Target/Face 子夾）
        num_re = re.compile(r"^(?!0+$)\d{1,4}$")
        batches: List[str] = []
        for p in sorted([d for d in root.iterdir() if d.is_dir()], key=lambda x: x.name):
            if not num_re.match(p.name):
                continue
            if (p / "Target").is_dir() and (p / "Face").is_dir():
                batches.append(p.name)
        if not batches:
            raise SchemaError("來源根目錄下未找到合法批次資料夾（需要數字命名且含 Target/Face）。")
        return batches

try:
    from .path_utils import list_images  # type: ignore
except Exception:
    def list_images(root: Path, recursive: bool = False, sort: bool = True) -> List[Path]:
        exts = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
        if recursive:
            items = [p for p in root.rglob("*") if p.suffix.lower() in exts]
        else:
            items = [p for p in root.iterdir() if p.is_file() and p.suffix.lower() in exts]
        return sorted(items) if sort else items

# =========================================================
# 設定便捷
# =========================================================

def _get_base_url() -> str:
    # 優先設定檔 comfyui.base_url
    try:
        base_url = get_settings("comfyui.base_url", as_dict=False)
        if base_url:
            return str(base_url).rstrip("/")
    except Exception:
        pass
    # 再取 comfyui.port
    try:
        port = int(get_settings("comfyui.port", as_dict=False) or 8199)
    except Exception:
        port = int(os.getenv("COMFYUI_PORT", "8199"))
    return f"http://127.0.0.1:{port}"

def _get_source_root() -> Path:
    for k in ("source_root", "paths_source_root"):
        try:
            v = get_settings(k, as_dict=False)
            if v:
                return Path(str(v))
        except Exception:
            pass
    raise PipelineError("設定缺少 source_root/paths_source_root。")

def _get_collection_name() -> str:
    for k in ("pipeline.collection_name", "collection", "pipeline.collection"):
        try:
            v = get_settings(k, as_dict=False)
            if v:
                return str(v)
        except Exception:
            pass
    # 後備
    return os.getenv("FS_COLLECTION", "Face_Changing")

def _load_stage1_prompt() -> Dict[str, Any]:
    # 優先使用解析好的 workflow_paths.stage1
    try:
        p = get_settings("workflow_paths.stage1", as_dict=False)
        if p:
            return json.loads(Path(str(p)).read_text(encoding="utf-8"))
    except Exception:
        pass
    # 後備：workflows.stage1 + comfyui.workflows_dir
    try:
        name = get_settings("workflows.stage1", as_dict=False)
        wdir = get_settings("comfyui.workflows_dir", as_dict=False)
        if name and wdir:
            p = Path(str(wdir)) / str(name)
            return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        pass
    raise PipelineError("無法讀取 Stage1 workflow（缺 workflow_paths.stage1 或 workflows.stage1/comfyui.workflows_dir）")

# =========================================================
# 字符串節點解析
# =========================================================

_SINGLE_STR_KEYS = ("string", "text", "value", "prompt", "label", "name")
_LIST_STR_KEYS   = ("strings", "values", "list", "texts", "items")

@dataclass(frozen=True)
class ResolvedTargets:
    mode: Literal["list", "singles"]
    collection_id: Union[int, str]
    collection_key: str                 # "inputs.xxx"
    # list 模式
    strings_id: Optional[Union[int, str]] = None
    strings_key: Optional[str] = None   # "inputs.xxx"
    # singles 模式
    single_ids: Optional[List[Union[int, str]]] = None
    single_key: Optional[str] = None    # "inputs.xxx"

def _env_override_id(name: str) -> Optional[Union[int, str]]:
    v = os.getenv(name)
    if not v:
        return None
    return int(v) if v.isdigit() else v

def _env_override_key(name: str) -> Optional[str]:
    v = os.getenv(name)
    if not v:
        return None
    return str(v).strip()

def _discover_string_targets(prompt: Dict[str, Any]) -> ResolvedTargets:
    """
    首選：collection(單一字串) + strings(字串列表)。
    後備：若列表不存在，偵測四獨立 PrimitiveString（例如 '117:0..3'）為 singles。
    亦支援環境變數覆寫 singles：
      - COMFY_STAGE1_SINGLES_IDS="A,B,C,D"
      - COMFY_STAGE1_SINGLES_KEY="value"  -> 寫 "inputs.value"
    """
    # 環境覆寫（collection / list）
    env_c_id  = _env_override_id("COMFY_STAGE1_COLLECTION_NODE")
    env_c_key = _env_override_key("COMFY_STAGE1_COLLECTION_KEY")
    env_s_id  = _env_override_id("COMFY_STAGE1_STRINGS_NODE")
    env_s_key = _env_override_key("COMFY_STAGE1_STRINGS_KEY")

    # 環境覆寫（singles）
    env_singles_ids = _env_override_key("COMFY_STAGE1_SINGLES_IDS")
    env_singles_key = _env_override_key("COMFY_STAGE1_SINGLES_KEY") or "value"

    # 收集候選
    cand_c: List[Tuple[Union[int, str], str, str]] = []            # (node, key, class)
    cand_s: List[Tuple[Union[int, str], str, str, int]] = []       # (node, key, class, list_len)
    cand_single: List[str] = []                                     # node ids whose inputs.value is str or None

    for nid_str, node in prompt.items():
        try:
            nid: Union[int, str] = int(nid_str) if str(nid_str).isdigit() else nid_str
            cls = str(node.get("class_type", ""))
            inputs = node.get("inputs", {})
            if not isinstance(inputs, dict):
                continue

            # 單一字串（作為 collection 候選）
            for k in _SINGLE_STR_KEYS:
                if k in inputs and isinstance(inputs[k], str):
                    cand_c.append((nid, k, cls))
                    break

            # 字串列表（作為 strings-list 候選）
            for k in _LIST_STR_KEYS:
                if k in inputs and isinstance(inputs[k], list):
                    lst = inputs[k]
                    if isinstance(lst, list):
                        cand_s.append((nid, k, cls, len(lst)))
                        break

            # singles 候選（PrimitiveString + inputs.value 是字串或 None）
            if cls == "PrimitiveString":
                if "value" in inputs and isinstance(inputs["value"], (str, type(None))):
                    cand_single.append(str(nid))
        except Exception:
            continue

    # 先挑 collection
    def pick_collection(cands: List[Tuple[Union[int, str], str, str]]) -> Optional[Tuple[Union[int, str], str]]:
        if not cands:
            return None
        key_rank = {k: i for i, k in enumerate(["string", "text", "value", "prompt", "label", "name"])}
        cands_sorted = sorted(
            cands,
            key=lambda t: (key_rank.get(t[1], 99), int(t[0]) if str(t[0]).isdigit() else 1_000_000)
        )
        return (cands_sorted[0][0], cands_sorted[0][1])

    c_sel = pick_collection(cand_c)

    # 再挑 strings-list
    def pick_strings(cands: List[Tuple[Union[int, str], str, str, int]]) -> Optional[Tuple[Union[int, str], str]]:
        if not cands: return None
        key_rank = {k: i for i, k in enumerate(["strings", "values", "list", "texts", "items"])}
        cands_sorted = sorted(
            cands,
            key=lambda t: (key_rank.get(t[1], 99), -t[3], int(t[0]) if str(t[0]).isdigit() else 1_000_000)
        )
        return (cands_sorted[0][0], cands_sorted[0][1])

    s_sel = pick_strings(cand_s)

    # 套用 collection/list 覆寫
    if env_c_id is not None:
        c_sel = (env_c_id, c_sel[1] if c_sel else (env_c_key or "value")) if env_c_key is None else (env_c_id, env_c_key)
    elif env_c_key is not None and c_sel:
        c_sel = (c_sel[0], env_c_key)

    if env_s_id is not None:
        s_sel = (env_s_id, s_sel[1] if s_sel else (env_s_key or "strings")) if env_s_key is None else (env_s_id, env_s_key)
    elif env_s_key is not None and s_sel:
        s_sel = (s_sel[0], env_s_key)

    # 若 strings-list 已找到：直接回傳 list 模式
    if c_sel and s_sel:
        return ResolvedTargets(
            mode="list",
            collection_id=c_sel[0],
            collection_key=f"inputs.{c_sel[1]}",
            strings_id=s_sel[0],
            strings_key=f"inputs.{s_sel[1]}",
        )

    # ---- fallback: singles 模式 ----
    singles_ids: List[Union[int, str]] = []

    # (A) 環境變數直接指定
    if env_singles_ids:
        for tok in env_singles_ids.split(","):
            node = tok.strip()
            if node:
                singles_ids.append(int(node) if node.isdigit() else node)

    # (B) 自動從 cand_single 偵測「<base>:0..3」群組
    if not singles_ids:
        pat = re.compile(r"^([^:]+):(\d+)$")
        groups: Dict[str, Dict[int, str]] = {}
        for nid in cand_single:
            m = pat.match(nid)
            if not m:
                continue
            base, idx_s = m.group(1), m.group(2)
            try:
                idx = int(idx_s)
            except Exception:
                continue
            groups.setdefault(base, {})[idx] = nid

        # 找擁有 0..3 的群組，挑 base 較小者
        best: Optional[Tuple[str, Dict[int, str]]] = None
        for base, d in groups.items():
            if all(i in d for i in (0, 1, 2, 3)):
                if best is None:
                    best = (base, d)
                else:
                    cur_base, _ = best
                    def _as_int(s: str) -> int:
                        return int(s) if s.isdigit() else 1_000_000
                    if _as_int(base) < _as_int(cur_base):
                        best = (base, d)
        if best:
            _, d = best
            singles_ids = [d[i] for i in (0, 1, 2, 3)]

    # 檢查 collection 與 singles 是否就緒
    if not c_sel or not singles_ids:
        print("[stage1] ⚠️ 無法自動解析字符串節點；以下為候選：")
        if c_sel or cand_c:
            print("  [collection-candidates]")
            for nid, k, cls in cand_c:
                print(f"    - node={nid!r} class={cls!r} key=inputs.{k}")
        else:
            print("  [collection-candidates] <none>（找不到單一字串輸入節點）")

        if s_sel:
            print("  [strings-candidates] 發現列表節點，但上方條件阻擋")
        else:
            print("  [strings-candidates] <none>（找不到字串列表輸入節點）")

        if cand_single:
            print("  [singles-candidates]")
            for nid in sorted(cand_single):
                print(f"    - node={nid} key=inputs.value")
        else:
            print("  [singles-candidates] <none>")
        raise PipelineError("解析 Stage1 字符串節點失敗：請檢查 workflow JSON 或以環境變數 COMFY_STAGE1_* 覆寫。")

    # singles 模式回傳
    return ResolvedTargets(
        mode="singles",
        collection_id=c_sel[0],
        collection_key=f"inputs.{c_sel[1]}",
        single_ids=singles_ids,
        single_key=f"inputs.{env_singles_key}",
    )

def _ensure_list_len(prompt: Dict[str, Any], node_id: Union[int, str], list_key: str, n: int) -> None:
    """保證 prompt[node]['inputs'][<key>] 為 list 且長度 ≥ n。"""
    node = prompt[str(node_id)]
    inputs = node.setdefault("inputs", {})
    key = list_key.split(".", 1)[1] if list_key.startswith("inputs.") else list_key
    lst = inputs.get(key)
    if not isinstance(lst, list):
        lst = []
    while len(lst) < n:
        lst.append("")
    inputs[key] = lst

# =========================================================
# Job 定義
# =========================================================

@dataclass(frozen=True)
class Stage1Job:
    image_path: Path
    prompt_mapping: Dict[str, Any]
    base_url: str
    client_id: str
    source_root: Path
    collection_name: str

    def tag(self) -> str:
        try:
            return f"{self.image_path.parent.parent.name}/{self.image_path.parent.name}/{self.image_path.name}"
        except Exception:
            return str(self.image_path)

    def submit(self, *, timeout: float = 30.0) -> str:
        prompt = copy.deepcopy(self.prompt_mapping)

        # 解析目標（list 或 singles）
        resolved = _discover_string_targets(prompt)

        # ===== 1) 推導四段字串與 collection =====
        source_root_name = self.source_root.name                     # ex: '來源'
        batch_name       = (self.image_path.parent.parent.name
                            if self.image_path.parent and self.image_path.parent.parent else "")
        kind             = self.image_path.parent.name               # 'Target' | 'Face'
        sep              = " // "
        collection_name  = self.collection_name

        # ===== 2) （可選）預檢 Loader 綁定 =====
        bind_loader   = os.getenv("COMFY_STAGE1_BIND_LOADER", "1") == "1"
        loader_node   = os.getenv("COMFY_STAGE1_LOADER_NODE", "29")
        loader_path   = str(self.image_path.parent).replace("\\", "/")
        loader_pattern = self.image_path.name
        loader_index  = 0

        # 檔案存在快速檢查
        file_exists = (self.image_path.exists() and self.image_path.is_file())

        # ===== 3) Debug 預覽 =====
        if os.getenv("COMFY_STAGE1_DEBUG", "0") == "1":
            print("\n[stage1:preview]")
            print(f"  image_path        : {self.image_path}")
            print(f"  collection_name   : {collection_name}")
            print(f"  strings[0] root   : {source_root_name}")
            print(f"  strings[1] batch  : {batch_name}")
            print(f"  strings[2] sep    : {sep}")
            print(f"  strings[3] kind   : {kind}")
            if bind_loader:
                print("  loader binding    :")
                print(f"    node            : {loader_node}")
                print(f"    path            : {loader_path}")
                print(f"    pattern         : {loader_pattern}")
                print(f"    index           : {loader_index}")
                print(f"    file_exists     : {file_exists}")

        if os.getenv("COMFY_STAGE1_STRICT", "0") == "1" and not file_exists:
            raise PipelineError(f"預檢失敗：檔案不存在 -> {self.image_path}")

        # ===== 4) 準備 staged 寫入 =====
        staged: List[Tuple[Union[int, str], str, Any]] = []
        # collection 一律寫入
        staged.append((resolved.collection_id, resolved.collection_key, str(collection_name)))

        # 四段字串
        if resolved.mode == "list":
            _ensure_list_len(prompt, resolved.strings_id, resolved.strings_key, 4)  # type: ignore[arg-type]
            staged.extend([
                (resolved.strings_id, f"{resolved.strings_key}[0]", str(source_root_name)),  # type: ignore[arg-type]
                (resolved.strings_id, f"{resolved.strings_key}[1]", str(batch_name)),        # type: ignore[arg-type]
                (resolved.strings_id, f"{resolved.strings_key}[2]", str(sep)),               # type: ignore[arg-type]
                (resolved.strings_id, f"{resolved.strings_key}[3]", str(kind)),              # type: ignore[arg-type]
            ])
            print(f"[stage1] resolved strings(list) -> node={resolved.strings_id!r} path='{resolved.strings_key}'")
        else:
            ids = resolved.single_ids or []
            key = resolved.single_key or "inputs.value"
            vals = [str(source_root_name), str(batch_name), str(sep), str(kind)]
            for nid, val in zip(ids, vals):
                staged.append((nid, key, val))
            print(f"[stage1] resolved strings(singles) -> nodes={ids!r} path='{key}'")

        # 綁定載圖（避免上游拿不到圖）
        if bind_loader:
            staged.extend([
                (loader_node, "inputs.mode", "incremental_image"),
                (loader_node, "inputs.path", loader_path),
                (loader_node, "inputs.pattern", loader_pattern),
                (loader_node, "inputs.index", loader_index),
            ])
            # 嘗試關閉額外欄位（有就寫，沒有也不影響）
            staged.append((loader_node, "inputs.allow_RGBA_output", False))
            staged.append((loader_node, "inputs.filename_text_extension", True))

        # Debug：列印 staged 明細
        if os.getenv("COMFY_STAGE1_DEBUG", "0") == "1":
            print("  staged writes     :")
            for i, (nid, path_key, val) in enumerate(staged):
                vs = str(val)
                if len(vs) > 180:
                    vs = vs[:177] + "..."
                print(f"    [{i:02d}] nid={nid!r} path={path_key!r} value={vs!r}")

        # 只預覽不送單
        if os.getenv("COMFY_STAGE1_BREAK_BEFORE_POST", "0") == "1":
            raise PipelineError("（預檢模式）僅預覽 staged 寫入，未送出到 /prompt。關閉 COMFY_STAGE1_BREAK_BEFORE_POST 後再執行。")

        # ===== 5) 送出 =====
        resp = comfy_post_prompt(self.base_url, prompt, staged=staged, client_id=self.client_id, timeout=timeout)
        if isinstance(resp, dict):
            for k in ("prompt_id", "id", "promptId", "promptID"):
                if k in resp:
                    return str(resp[k])
        raise PipelineError(f"post_prompt 回傳未知格式：{resp!r}")

# =========================================================
# 對外：準備與提交
# =========================================================

def _iter_images_under(source_root: Path, batch: str, sub: str) -> Iterable[Path]:
    folder = source_root / batch / sub
    if folder.is_dir():
        for p in list_images(folder, recursive=False, sort=True):
            yield p

def prepare_stage1_jobs() -> List[Stage1Job]:
    """
    1) 讀設定與 workflow(JSON)
    2) 檢查來源資料夾規範（batch/Target/Face）
    3) 為每張圖片建立一個 Stage1Job
    """
    base_url   = _get_base_url()
    source_root = _get_source_root()
    collection  = _get_collection_name()
    prompt      = _load_stage1_prompt()

    # 嚴格檢查來源規範
    batches = check_source_schema(source_root)

    client_id = str(uuid.uuid4())
    jobs: List[Stage1Job] = []

    for b in batches:
        for sub in ("Target", "Face"):
            for img in _iter_images_under(source_root, b, sub):
                jobs.append(Stage1Job(
                    image_path=img,
                    prompt_mapping=prompt,
                    base_url=base_url,
                    client_id=client_id,
                    source_root=source_root,
                    collection_name=collection,
                ))
    return jobs

def submit_stage1_jobs(
    jobs: List[Stage1Job],
    *,
    max_inflight: int = 4,
    poll_interval_sec: float = 0.75,
    max_retries: int = 2,
) -> None:
    """
    以固定上限 inflight 送到 /prompt 佇列並輪詢 /history，直到所有完成或失敗。
    """
    pending: List[Tuple[Stage1Job, int]] = [(j, max_retries) for j in jobs]
    inflight: Dict[str, Dict[str, Any]] = {}  # job_id -> {job, tag, retries}

    def _extract_status(history_payload: Dict[str, Any]) -> str:
        try:
            s = history_payload.get("status", {})
            if isinstance(s, dict):
                v = s.get("status")
                if isinstance(v, str):
                    return v.lower()
        except Exception:
            pass
        v = history_payload.get("status")
        if isinstance(v, str):
            return v.lower()
        return "pending"

    def _poll_once() -> None:
        to_rm: List[str] = []
        for jid, entry in list(inflight.items()):
            tag = entry["tag"]
            try:
                hist = comfy_get_history(jobs[0].base_url, jid, timeout=30.0)
                st = _extract_status(hist)
                if st in ("success", "finished", "done", "completed"):
                    print(f"[stage1] ✅ done: {tag} (job_id={jid})")
                    to_rm.append(jid)
                elif st in ("error", "failed", "canceled", "exception"):
                    print(f"[stage1] ❌ fail: {tag} (job_id={jid})")
                    to_rm.append(jid)
                    if entry["retries"] > 0:
                        pending.append((entry["job"], entry["retries"] - 1))
                        print(f"[stage1] 🔁 will retry: {tag} (remain {entry['retries'] - 1})")
            except Exception as e:
                print(f"[stage1] ⚠️  history error for {tag}: {e}")
                to_rm.append(jid)
                if entry["retries"] > 0:
                    pending.append((entry["job"], entry["retries"] - 1))
                    print(f"[stage1] 🔁 will retry: {tag} (remain {entry['retries'] - 1})")

        for jid in to_rm:
            inflight.pop(jid, None)

    while pending or inflight:
        # 裝填
        while pending and len(inflight) < max_inflight:
            job, r = pending.pop(0)
            tag = job.tag()
            try:
                jid = job.submit()
                inflight[jid] = {"job": job, "tag": tag, "retries": r}
                print(f"[stage1] 📤 posted: {tag} (job_id={jid})")
            except Exception as e:
                if r > 0:
                    print(f"[stage1] ⚠️  首次送單失敗，將重試（remain {r - 1}）：{tag} -> {e}")
                    pending.append((job, r - 1))
                else:
                    raise
        # 輪詢
        _poll_once()
        if inflight or pending:
            time.sleep(poll_interval_sec)
