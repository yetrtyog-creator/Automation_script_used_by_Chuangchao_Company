# -*- coding: utf-8 -*-
"""
comfy_api.py — ComfyUI REST 薄封裝（post_prompt / get_history / is_ready）

功能重點
--------
1) 就緒檢查 is_ready():
   - 兼容多種端點差異，依序探測：
       GET  /system/ready      （部分分支/外掛才有）
       GET  /system/versions   （核心，啟動後常見 200）
       GET  /api/nodes         （核心節點列表）
       OPTIONS /prompt         （路由存在常回 200/204/405）
   - 可用環境變數 COMFY_READY_PATHS 覆寫，格式如：
       "GET /healthz,GET /api/nodes,OPTIONS /prompt"

2) post_prompt() 與 submit():
   - 支援兩種用法：
       a) 先 load_prompt()/stage()，最後呼叫 submit()
       b) 直接呼叫實例方法 post_prompt(prompt_mapping, staged=...) 一次完成
   - 送出前可依環境變數進行清洗：
       COMFY_STRIP_EXTRA_PNGINFO=0  -> 關閉移除 extra_pnginfo（預設 開啟）
       COMFY_DISABLE_SAVE_META=0    -> 關閉關閉 Save/Preview metadata（預設 開啟）
       COMFY_STRIP_FIELDS="a,b,c"   -> 另外從 inputs.* 刪除的欄位（逗號分隔）

3) 泛用路徑設值器 _set_by_path():
   - 支援 "inputs.images[0].filename" 這種點號+索引

4) 例外：PipelineError
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Tuple, Union
from dataclasses import dataclass
from copy import deepcopy
import json
import uuid
import os

# 第三方
try:
    import requests  # type: ignore
except Exception as e:  # 避免在未安裝 requests 時直接崩潰
    raise RuntimeError("comfy_api.py 需要 'requests' 模組，請先安裝：pip install requests") from e


# ===== 例外 =====
class PipelineError(Exception):
    """ComfyUI API 互動與工作流封裝的通用錯誤。"""


# ===== 主要類別 =====
class ComfyAPI:
    def __init__(self, base_url: str, *, default_timeout: float = 30.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.default_timeout = default_timeout
        self.client_id = str(uuid.uuid4())
        self._prompt: Dict[str, Any] = {}
        self._staged: List[Tuple[Union[int, str], str, Any]] = []

        # 環境變數開關
        self._enable_strip_extrapng = os.environ.get("COMFY_STRIP_EXTRA_PNGINFO", "1") != "0"
        self._enable_disable_save_meta = os.environ.get("COMFY_DISABLE_SAVE_META", "1") != "0"
        self._extra_strip_fields = self._parse_strip_fields(os.environ.get("COMFY_STRIP_FIELDS", ""))

        # session
        self._sess = requests.Session()

    # ---------- 公開 API ----------

    def is_ready(self, timeout: float = 1.0) -> bool:
        """
        檢查 ComfyUI 是否就緒（不同版本/分支端點不一致）。
        順序探測以下端點，任一成功即視為就緒：
          - GET  /system/ready
          - GET  /system/versions
          - GET  /api/nodes
          - OPTIONS /prompt （存在路由時常回 200/204/405）
        可用環境變數 COMFY_READY_PATHS 覆寫，例：
          "GET /healthz,GET /api/nodes,OPTIONS /prompt"
        """
        custom = os.environ.get("COMFY_READY_PATHS")
        if custom:
            probes: List[Tuple[str, str]] = []
            for item in custom.split(","):
                item = item.strip()
                if not item:
                    continue
                if " " in item:
                    m, p = item.split(" ", 1)
                    probes.append((m.strip().upper() or "GET", p.strip()))
                else:
                    probes.append(("GET", item))
        else:
            probes = [
                ("GET", "/system/ready"),
                ("GET", "/system/versions"),
                ("GET", "/api/nodes"),
                ("OPTIONS", "/prompt"),
            ]

        for method, path in probes:
            url = f"{self.base_url}{path}"
            try:
                r = self._sess.request(method, url, timeout=timeout)
                if r.status_code in (200, 204):
                    return True
                if path == "/prompt" and r.status_code in (200, 204, 405):
                    return True
            except requests.RequestException:
                pass
        return False

    def load_prompt(self, prompt_mapping: Dict[str, Any]) -> None:
        """載入 ComfyUI /prompt 所需的 'prompt' 對映（非 GUI graph）。"""
        if not isinstance(prompt_mapping, dict):
            raise PipelineError("load_prompt() 需要 dict 形態的 prompt 對映。")
        self._prompt = deepcopy(prompt_mapping)
        self._staged.clear()

    def stage(self, node_id: Union[int, str], path: str, value: Any) -> None:
        """
        記錄尚未套用的節點變更。
        例：stage(7, "inputs.images[0].filename", "/tmp/a.png")
        """
        if not path:
            raise PipelineError("stage() 需要非空的 path。")
        self._staged.append((node_id, path, value))

    def apply_staged(self) -> Dict[str, Any]:
        """回傳已套用 staged 變更後的 prompt 副本（不清空 staged）。"""
        if not isinstance(self._prompt, dict) or not self._prompt:
            raise PipelineError("尚未 load_prompt()。")
        prompt = deepcopy(self._prompt)
        for node_id, path, value in self._staged:
            self._set_by_path(prompt, node_id, path, value)
        return prompt

    def submit(self, *, timeout: Optional[float] = None) -> Dict[str, Any]:
        """
        套用 staged -> 清洗（依環境變數）-> POST /prompt
        成功後清空 staged，回傳服務端 JSON。
        """
        if not isinstance(self._prompt, dict) or not self._prompt:
            raise PipelineError("尚未 load_prompt()。")

        payload_prompt = self.apply_staged()

        # 清洗：遞迴移除 extra_pnginfo
        if self._enable_strip_extrapng:
            self._strip_extra_pnginfo(payload_prompt)

        # 清洗：移除 inputs.* 內自訂欄位
        if self._extra_strip_fields:
            self._strip_inputs_fields(payload_prompt, self._extra_strip_fields)

        # 清洗：關閉 Save/Preview 節點的 metadata 類開關
        if self._enable_disable_save_meta:
            self._disable_save_preview_metadata(payload_prompt)

        url = f"{self.base_url}/prompt"
        body = {"prompt": payload_prompt, "client_id": self.client_id}

        try:
            r = self._sess.post(url, json=body, timeout=timeout or self.default_timeout)
            r.raise_for_status()
            self._staged.clear()
            return r.json()
        except requests.HTTPError as he:
            try:
                detail = r.text  # type: ignore[name-defined]
            except Exception:
                detail = str(he)
            raise PipelineError(f"post_prompt 失敗：{detail}") from he
        except requests.RequestException as re:
            raise PipelineError(f"post_prompt 連線錯誤：{re}") from re

    # === 向下相容：提供實例方法 post_prompt(...) ===
    def post_prompt(
        self,
        prompt_mapping: Optional[Dict[str, Any]] = None,
        *,
        staged: Iterable[Tuple[Union[int, str], str, Any]] = (),
        client_id: Optional[str] = None,
        timeout: Optional[float] = None,
    ) -> Dict[str, Any]:
        """
        與過去介面相容的便捷方法。
        - 若提供 prompt_mapping：會先 load，再套 staged，最後送出
        - 若未提供：視為已在外部 load_prompt()/stage() 完畢，直接 submit()
        - 可用 client_id 覆寫（若需要追蹤同一工作流程）
        """
        if client_id:
            self.client_id = client_id
        if prompt_mapping is not None:
            self.load_prompt(prompt_mapping)
        for (nid, path, val) in staged:
            self.stage(nid, path, val)
        return self.submit(timeout=timeout)

    def get_history(self, prompt_id: str, *, timeout: Optional[float] = None) -> Dict[str, Any]:
        """查詢 /history/<prompt_id>。"""
        if not prompt_id:
            raise PipelineError("get_history() 需要有效的 prompt_id。")
        url = f"{self.base_url}/history/{prompt_id}"
        try:
            r = self._sess.get(url, timeout=timeout or self.default_timeout)
            r.raise_for_status()
            return r.json()
        except requests.HTTPError as he:
            try:
                detail = r.text  # type: ignore[name-defined]
            except Exception:
                detail = str(he)
            raise PipelineError(f"get_history 失敗：{detail}") from he
        except requests.RequestException as re:
            raise PipelineError(f"get_history 連線錯誤：{re}") from re

    # ---------- 內部工具 ----------

    @staticmethod
    def _parse_strip_fields(blob: str) -> List[str]:
        if not blob:
            return []
        items = [s.strip() for s in blob.split(",")]
        return [s for s in items if s]

    def _set_by_path(self, prompt: Dict[str, Any], node_id: Union[int, str], path: str, value: Any) -> None:
        """
        在 prompt[node_id] 下，依據 path 設定值。
        path 支援點號與索引，如 "inputs.images[0].filename"
        """
        node_key = str(node_id)
        if node_key not in prompt or not isinstance(prompt[node_key], dict):
            raise PipelineError(f"節點 {node_key} 不存在於 prompt。")

        target = prompt[node_key]
        tokens: List[Union[str, int]] = self._parse_path_tokens(path)
        if not tokens:
            raise PipelineError(f"無效 path：{path!r}")

        curr: Any = target
        for tk in tokens[:-1]:
            if isinstance(tk, str):
                if tk not in curr or not isinstance(curr[tk], (dict, list)):
                    curr[tk] = {}  # type: ignore[index]
                curr = curr[tk]  # type: ignore[index]
            else:
                if not isinstance(curr, list):
                    raise PipelineError(f"path 存取 {tk} 失敗：預期 list，但得到 {type(curr)}")
                idx = tk
                while len(curr) <= idx:  # type: ignore[arg-type]
                    curr.append({})
                curr = curr[idx]  # type: ignore[index]

        last = tokens[-1]
        if isinstance(last, str):
            if not isinstance(curr, dict):
                raise PipelineError(f"最後節點預期 dict，但得到 {type(curr)}")
            curr[last] = value
        else:
            if not isinstance(curr, list):
                raise PipelineError(f"最後節點預期 list，但得到 {type(curr)}")
            idx = last
            while len(curr) <= idx:
                curr.append(None)
            curr[idx] = value

    @staticmethod
    def _parse_path_tokens(path: str) -> List[Union[str, int]]:
        """將 'inputs.images[0].filename' 解析成 ['inputs','images',0,'filename']"""
        out: List[Union[str, int]] = []
        buf = ""
        i = 0
        while i < len(path):
            c = path[i]
            if c == ".":
                if buf:
                    out.append(buf)
                    buf = ""
                i += 1
                continue
            if c == "[":
                if buf:
                    out.append(buf)
                    buf = ""
                j = path.find("]", i + 1)
                if j == -1:
                    raise PipelineError(f"path 括號未閉合：{path!r}")
                idx_str = path[i + 1 : j].strip()
                if not idx_str.isdigit():
                    raise PipelineError(f"path 索引非數字：{idx_str!r}")
                out.append(int(idx_str))
                i = j + 1
                continue
            buf += c
            i += 1
        if buf:
            out.append(buf)
        return out

    # ----- 清洗：遞迴移除 extra_pnginfo -----

    def _strip_extra_pnginfo(self, obj: Any) -> Any:
        """遞迴刪除所有鍵名為 'extra_pnginfo' 的欄位（dict/list 皆處理）。"""
        if isinstance(obj, dict):
            if "extra_pnginfo" in obj:
                del obj["extra_pnginfo"]
            for k in list(obj.keys()):
                self._strip_extra_pnginfo(obj[k])
        elif isinstance(obj, list):
            for i in range(len(obj)):
                self._strip_extra_pnginfo(obj[i])
        return obj

    # ----- 清洗：移除節點 inputs.* 內自訂欄位 -----

    def _strip_inputs_fields(self, prompt: Dict[str, Any], fields: List[str]) -> None:
        if not fields:
            return
        for node_key, node in list(prompt.items()):
            if not isinstance(node, dict):
                continue
            inputs = node.get("inputs")
            if isinstance(inputs, dict):
                for f in fields:
                    if f in inputs:
                        del inputs[f]

    # ----- 清洗：關閉 Save/Preview 類節點的 metadata 類開關 -----

    def _disable_save_preview_metadata(self, prompt: Dict[str, Any]) -> None:
        """
        對於 class_type 看起來像 Save/Preview 的節點，把 metadata/工作流嵌入等關閉。
        嘗試關閉以下可能存在的鍵（若存在即設 False）：
          include_metadata, metadata, save_metadata, add_metadata, embed_workflow,
          include_workflow, save_workflow, write_meta, enable_metadata
        """
        meta_keys = {
            "include_metadata",
            "metadata",
            "save_metadata",
            "add_metadata",
            "embed_workflow",
            "include_workflow",
            "save_workflow",
            "write_meta",
            "enable_metadata",
        }
        for node_key, node in list(prompt.items()):
            if not isinstance(node, dict):
                continue
            cls = f"{node.get('class_type', '')}".lower()
            is_save_like = ("save" in cls) or ("preview" in cls)
            if not is_save_like:
                continue
            inputs = node.get("inputs")
            if isinstance(inputs, dict):
                for k in list(inputs.keys()):
                    lk = k.lower()
                    if lk in meta_keys:
                        inputs[k] = False


# ===== 模組級便捷函數（保留給舊呼叫） =====

def post_prompt(base_url: str, prompt_mapping: Dict[str, Any], *,
                staged: Iterable[Tuple[Union[int, str], str, Any]] = (),
                client_id: Optional[str] = None,
                timeout: float = 30.0) -> Dict[str, Any]:
    """
    便捷單呼叫：建立 ComfyAPI、載入 prompt、套用 staged、送出。
    """
    api = ComfyAPI(base_url, default_timeout=timeout)
    if client_id:
        api.client_id = client_id
    api.load_prompt(prompt_mapping)
    for (nid, path, val) in staged:
        api.stage(nid, path, val)
    return api.submit(timeout=timeout)


def get_history(base_url: str, prompt_id: str, *, timeout: float = 30.0) -> Dict[str, Any]:
    api = ComfyAPI(base_url, default_timeout=timeout)
    return api.get_history(prompt_id, timeout=timeout)
