"""
comfy_api.py — ComfyUI 薄封裝 REST 客戶端（post_prompt / get_history / is_ready）

用途與範圍
----------
本模組只做三件事（不負責排程、輪詢、重試等流程控制）：
1) `is_ready()`：查 ComfyUI 是否已就緒可收工作。
2) `post_prompt()`：送出一個 API 版的「prompt 映射（mapping）」。
3) `get_history(prompt_id)`：取回指定 `prompt_id` 的推理結果/歷史。

狀態式變更暫存（staging）
------------------------
• 你先 `set_prompt(...)` 載入一份「API 風格」的 prompt 映射（不是 GUI 專案圖）。
• 接著用 `stage_set(node_id, path, value)` 或 `stage_many(...)` 累積節點欄位的修改。
• 呼叫 `post_prompt()` 會把「原始 prompt + 暫存變更」合併後送出；**成功送出後才會清空暫存變更**。

路徑語法（通用鍵路徑）
----------------------
`path` 參數支援「.」與「[索引]」的混合路徑，適用於 dict 與 list 巢狀結構，例如：
- "inputs.text"
- "inputs.images[0]"
- "inputs.transforms[2].scale"
缺失的中繼層級會自動建立（dict/list），list 會以 `None` 補齊長度。

錯誤處理
--------
• 全部拋出單一例外基類 `PipelineError`。
• 網路/HTTP 錯誤、JSON 解析錯誤、路徑設定錯誤、缺少節點 ID 等，皆會轉成 `PipelineError`。

重要前提
--------
- 這裡的 `prompt` 指的是 **ComfyUI API 版** 的「節點映射」結構，而非 GUI 匯出的工作流（含節點座標/連線）。
  典型結構：
    {
      "3": {"class_type": "KSampler", "inputs": {"cfg": 7.0, ...}},
      "7": {"class_type": "SaveImage", "inputs": {"images": ["3", 0]}}
    }
- 送出時配合 ComfyUI 的 `POST /prompt`，payload 形如：{"prompt": <mapping>, "client_id": <uuid>}.
- `GET /system/ready` 以回傳的 JSON 或純文字解讀 ready 狀態。
- `GET /history/<prompt_id>` 取回該次推理記錄。

常見用法
--------
    api = ComfyAPI(base_url="http://127.0.0.1:8199")
    api.set_prompt(prompt_dict_or_json_path)                  # 設定 API 版 prompt
    api.stage_set(7, "inputs.text", "hello world")            # 暫存對某節點欄位的變更
    api.stage_many([(3, "inputs.cfg", 6.5), (3, "inputs.seed", 1234)])
    pid = api.post_prompt()                                   # 送出（成功後清空暫存）
    hist = api.get_history(pid)                               # 查歷史/輸出

備註
----
- 若你手上是 GUI 匯出的工作流（含 nodes/links/pos 等），請先轉成 API 的 prompt 映射，本檔不負責轉換。
- 本模組預設使用 `requests`，請先安裝：`pip install requests`。
- 預設逾時 `timeout=60` 秒；可在初始化時調整。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple, Union
import json
import uuid

try:
    # Prefer requests if available for simplicity
    import requests  # type: ignore
except Exception as e:  # pragma: no cover
    raise RuntimeError(
        "The 'requests' package is required for comfy_api.py. Install with: pip install requests"
    ) from e


# ========================
# Exceptions
# ========================
class PipelineError(Exception):
    """Base exception for Comfy pipeline operations."""


# ========================
# Types
# ========================
JSON = Dict[str, Any]
PromptMapping = Dict[str, JSON]


@dataclass
class PendingChange:
    node_id: str
    path: str  # dotted/bracket path relative to the node dict, e.g. "inputs.text" or "inputs.images[0]"
    value: Any


# ========================
# Client
# ========================
class ComfyAPI:
    """
    Thin REST wrapper around a running ComfyUI server.

    Usage:
        api = ComfyAPI(base_url="http://127.0.0.1:8199")
        api.set_prompt(prompt_dict)                       # <-- API-style prompt mapping
        api.stage_set(7, "inputs.text", "hello world")    # stage changes
        prompt_id = api.post_prompt()                     # apply staged -> POST /prompt
        hist = api.get_history(prompt_id)
    """

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8199",
        *,
        client_id: Optional[str] = None,
        timeout: float = 60.0,
        session: Optional[requests.Session] = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.client_id = client_id or str(uuid.uuid4())
        self.timeout = timeout
        self.http = session or requests.Session()

        # Working state
        self._prompt: PromptMapping = {}
        self._staged: List[PendingChange] = []

    # ---------- Core REST ----------
    def is_ready(self) -> bool:
        """True if the backend has finished loading and can accept prompts."""
        url = f"{self.base_url}/system/ready"
        try:
            r = self.http.get(url, timeout=self.timeout)
            r.raise_for_status()
        except requests.RequestException as e:  # network / HTTP error
            raise PipelineError(f"is_ready failed: {e}") from e

        # Some variants return JSON {"status": "ready"} or {"ready": true}; others return plain text
        try:
            data = r.json()
            if isinstance(data, dict):
                # Common possibilities
                if data.get("ready") is True:
                    return True
                if str(data.get("status", "")).lower() in {"ready", "ok", "true"}:
                    return True
            # Fallback: interpret truthy text
        except ValueError:
            pass
        text = r.text.strip().lower()
        return text in {"true", "ready", "ok", "1"}

    def post_prompt(self) -> str:
        """
        Apply staged changes to the current prompt, POST to /prompt, clear staged on success,
        and return the prompt_id from the server.
        """
        if not self._prompt:
            raise PipelineError("post_prompt: no prompt set. Call set_prompt(...) first.")

        payload = self._materialize_payload()
        url = f"{self.base_url}/prompt"
        try:
            r = self.http.post(url, json=payload, timeout=self.timeout)
            r.raise_for_status()
            data = r.json()
        except requests.RequestException as e:
            raise PipelineError(f"post_prompt failed: {e}") from e
        except ValueError as e:
            raise PipelineError("post_prompt: server returned non-JSON response") from e

        prompt_id = data.get("prompt_id") or data.get("id")
        if not prompt_id:
            raise PipelineError(f"post_prompt: unexpected response, missing prompt_id: {data}")

        # 清空暫存變更：僅在成功取得 prompt_id 之後
        self._staged.clear()
        return str(prompt_id)

    def get_history(self, prompt_id: Union[str, uuid.UUID]) -> JSON:
        """Fetch history for a previously submitted prompt_id."""
        url = f"{self.base_url}/history/{prompt_id}"
        try:
            r = self.http.get(url, timeout=self.timeout)
            r.raise_for_status()
            return r.json()
        except requests.RequestException as e:
            raise PipelineError(f"get_history failed: {e}") from e
        except ValueError as e:
            raise PipelineError("get_history: server returned non-JSON response") from e

    # ---------- Stateful staging ----------
    def set_prompt(self, prompt: Union[PromptMapping, JSON, str, Path], *, clear_staged: bool = True) -> None:
        """
        Set/replace the current API prompt mapping.
        - If a str/Path is provided, it will be read as JSON from disk.
        - If a dict with a top-level key 'prompt' is provided, its value is used.
        - Otherwise the dict itself is assumed to be the mapping {node_id: {...}}.
        """
        if isinstance(prompt, (str, Path)):
            with open(prompt, "r", encoding="utf-8") as f:
                loaded = json.load(f)
        else:
            loaded = prompt

        if not isinstance(loaded, dict):
            raise PipelineError("set_prompt: expected dict or path to JSON file")

        mapping = loaded.get("prompt") if "prompt" in loaded and isinstance(loaded["prompt"], dict) else loaded
        # Shallow-validate the mapping looks like {"<id>": {"class_type": str, "inputs": {...}}}
        if not isinstance(mapping, dict) or not mapping:
            raise PipelineError("set_prompt: invalid or empty prompt mapping")

        self._prompt = {str(k): v for k, v in mapping.items()}
        if clear_staged:
            self._staged.clear()

    def stage_set(self, node_id: Union[int, str], path: str, value: Any) -> None:
        """Stage a change for a node: (node_id, dotted/bracket path, value)."""
        nid = str(node_id)
        self._staged.append(PendingChange(node_id=nid, path=path, value=value))

    def stage_many(self, changes: Iterable[Tuple[Union[int, str], str, Any]]) -> None:
        """Stage multiple (node_id, path, value) items."""
        for node_id, path, value in changes:
            self.stage_set(node_id, path, value)

    def clear_staged(self) -> None:
        """Discard any staged changes without submitting."""
        self._staged.clear()

    # ---------- Helpers ----------
    def _materialize_payload(self) -> JSON:
        """Apply staged changes to a *copy* of the current prompt and return POST body."""
        if not self._prompt:
            raise PipelineError("No prompt set")
        # Deep-ish copy (structure is plain JSON)
        prompt_copy = json.loads(json.dumps(self._prompt))

        for ch in self._staged:
            node = prompt_copy.get(ch.node_id)
            if node is None:
                raise PipelineError(f"stage points to missing node id {ch.node_id}")
            try:
                self._set_by_path(node, ch.path, ch.value)
            except Exception as e:  # path errors
                raise PipelineError(f"Failed to apply {ch.node_id}.{ch.path}: {e}") from e

        return {"prompt": prompt_copy, "client_id": self.client_id}

    @staticmethod
    def _set_by_path(root: JSON, path: str, value: Any) -> None:
        """
        Set a value in a nested dict/list structure using dotted + bracket paths.
        Examples:
            _set_by_path(node, "inputs.text", "hi")
            _set_by_path(node, "inputs.images[0]", ["3", 0])
            _set_by_path(node, "inputs.transforms[2].scale", 1.25)
        """
        tokens = ComfyAPI._tokenize_path(path)
        if not tokens:
            raise ValueError("empty path")
        cur: Any = root
        # Traverse to the parent of the final token
        for i, tok in enumerate(tokens[:-1]):
            if tok[0] == "key":
                k = tok[1]
                if not isinstance(cur, dict):
                    raise TypeError(f"Expected dict at segment '{k}', got {type(cur).__name__}")
                if k not in cur or cur[k] is None:
                    # Create dict by default when stepping into a missing key
                    cur[k] = {}
                cur = cur[k]
            else:  # index
                idx = tok[1]
                if not isinstance(cur, list):
                    raise TypeError(f"Expected list at index [{idx}], got {type(cur).__name__}")
                # Extend list if needed with None placeholders
                if idx >= len(cur):
                    cur.extend([None] * (idx - len(cur) + 1))
                if cur[idx] is None:
                    cur[idx] = {}
                cur = cur[idx]

        # Apply final segment
        last = tokens[-1]
        if last[0] == "key":
            k = last[1]
            if not isinstance(cur, dict):
                raise TypeError(f"Expected dict for final segment '{k}', got {type(cur).__name__}")
            cur[k] = value
        else:
            idx = last[1]
            if not isinstance(cur, list):
                raise TypeError(f"Expected list for final index [{idx}], got {type(cur).__name__}")
            if idx >= len(cur):
                cur.extend([None] * (idx - len(cur) + 1))
            cur[idx] = value

    @staticmethod
    def _tokenize_path(path: str) -> List[Tuple[str, Union[str, int]]]:
        """
        Convert 'inputs.images[0].name' into [('key','inputs'), ('key','images'), ('index',0), ('key','name')].
        """
        tokens: List[Tuple[str, Union[str, int]]] = []
        buf: List[str] = []
        i = 0
        n = len(path)

        def flush_key() -> None:
            if buf:
                tokens.append(("key", "".join(buf)))
                buf.clear()

        while i < n:
            c = path[i]
            if c == ".":
                flush_key()
                i += 1
                continue
            if c == "[":
                flush_key()
                # parse integer index until ']'
                j = i + 1
                if j >= n:
                    raise ValueError("Unclosed '[' in path")
                sign = 1
                if path[j] == "+":
                    j += 1
                elif path[j] == "-":
                    sign = -1
                    j += 1
                start = j
                while j < n and path[j].isdigit():
                    j += 1
                if j == start:
                    raise ValueError("Empty index '[]' is not allowed")
                if j >= n or path[j] != "]":
                    raise ValueError("Missing closing ']' in path")
                idx = int(path[start:j]) * sign
                tokens.append(("index", idx))
                i = j + 1
                continue
            # normal key char
            buf.append(c)
            i += 1
        # Flush remaining key
        flush_key()
        return tokens


# ===========
# __all__
# ===========
__all__ = [
    "ComfyAPI",
    "PipelineError",
    "PendingChange",
]


if __name__ == "__main__":  # Minimal smoke test (no real server call)
    # This block only demonstrates staging mechanics without HTTP calls.
    demo = ComfyAPI(base_url="http://127.0.0.1:8199")
    demo.set_prompt({
        "3": {"class_type": "KSampler", "inputs": {"cfg": 7.0, "seed": 1}},
        "7": {"class_type": "SaveImage", "inputs": {"images": ["3", 0]}}
    })
    demo.stage_set(3, "inputs.cfg", 6.5)
    demo.stage_set(3, "inputs.extra.options[0]", "demo")
    materialized = demo._materialize_payload()
    print(json.dumps(materialized, ensure_ascii=False, indent=2))
