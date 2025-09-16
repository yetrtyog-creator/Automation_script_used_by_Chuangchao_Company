#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
comfy_api.py — 穩健版 ComfyUI REST API 封裝
- 修正 history.status 可能為 dict 導致的 '.lower()' 例外
- 統一處理 queue/history 可能的多種結構
- 提供簡單的 /prompt 提交與狀態查詢介面
"""
from __future__ import annotations

from typing import Any, Dict, Optional, Tuple, List
import time, uuid, json

import requests


class ComfyAPI:
    def __init__(self, base_url: str, poll_interval: float = 1.0, timeout: float = 30.0):
        self.base_url = base_url.rstrip("/")
        self.poll_interval = max(0.25, float(poll_interval))
        self.timeout = float(timeout)
        # 明確帶 client_id（雖然不是必填，但能改善行為一致性）
        self.client_id = f"faceswap-{uuid.uuid4().hex}"

    # ======================
    # 基礎 HTTP
    # ======================
    def _get(self, path: str, timeout: Optional[float] = None) -> requests.Response:
        t = timeout or self.timeout
        r = requests.get(self.base_url + path, timeout=t)
        r.raise_for_status()
        return r

    def _post(self, path: str, payload: Dict[str, Any], timeout: Optional[float] = None) -> requests.Response:
        t = timeout or self.timeout
        r = requests.post(self.base_url + path, json=payload, timeout=t)
        r.raise_for_status()
        return r

    # ======================
    # ComfyUI 端點
    # ======================
    def object_info(self) -> Dict[str, Any]:
        """回傳 /object_info"""
        return self._get("/object_info").json()

    def get_queue(self) -> Dict[str, Any]:
        """回傳 /queue，容錯不同返回結構"""
        data = self._get("/queue").json()
        # 合併兼容：有時 key 叫 queue_pending/queue_running、有時 pending/running
        pending = data.get("queue_pending") or data.get("pending") or []
        running = data.get("queue_running") or data.get("running") or []
        return {"pending": pending, "running": running, **data}

    def get_history(self, prompt_id: str) -> Dict[str, Any]:
        """回傳 /history/{prompt_id} 原始 JSON"""
        return self._get(f"/history/{prompt_id}").json()

    def peek_history(self, prompt_id: str) -> Optional[Dict[str, Any]]:
        """
        讀一次 history，並抽出對應 prompt_id 的紀錄。
        ComfyUI 的 history 可能有兩種形態：
          1) { "<pid>": { ...record... } }
          2) { "history": { "<pid>": { ...record... } } }
        """
        try:
            hist = self.get_history(prompt_id)
        except Exception:
            return None

        if isinstance(hist, dict):
            if prompt_id in hist and isinstance(hist[prompt_id], dict):
                return hist[prompt_id]
            h = hist.get("history")
            if isinstance(h, dict) and prompt_id in h and isinstance(h[prompt_id], dict):
                return h[prompt_id]
        return None

    def post_prompt(self, workflow: Dict[str, Any]) -> str:
        """
        呼叫 /prompt 提交工作流。成功回傳 prompt_id。
        典型回應：
          {"prompt_id":"<uuid>","number":26,"node_errors":{}}
        """
        payload = {"prompt": workflow, "client_id": self.client_id}
        j = self._post("/prompt", payload).json()
        pid = j.get("prompt_id")
        if not pid:
            raise RuntimeError(f"/prompt 回傳異常：{j}")
        return str(pid)

    # ======================
    # 佇列/狀態解析
    # ======================
    @staticmethod
    def _normalize_id(x: Any) -> Optional[str]:
        if x is None:
            return None
        try:
            s = str(x).strip()
            return s if s else None
        except Exception:
            return None

    def _id_in_queue(self, queue: Dict[str, Any], prompt_id: str) -> Optional[str]:
        """
        檢查指定 prompt_id 是否在 queue 中。回傳：
          - 'running' | 'queued' | None
        容忍元素可能是 ["<pid>", <num>]、{"id":"<pid>", ...}、或直接是 "<pid>"。
        """
        pid = self._normalize_id(prompt_id)

        def _match_bucket(bucket: List[Any]) -> bool:
            for item in bucket:
                try:
                    if isinstance(item, (list, tuple)) and item:
                        # 形如 ["<pid>", <n>]
                        if self._normalize_id(item[0]) == pid:
                            return True
                    elif isinstance(item, dict):
                        # 形如 {"id":"<pid>", ...} 或 {"prompt_id":"<pid>"}
                        if self._normalize_id(item.get("id") or item.get("prompt_id")) == pid:
                            return True
                    else:
                        # 形如 "<pid>"
                        if self._normalize_id(item) == pid:
                            return True
                except Exception:
                    continue
            return False

        running = queue.get("queue_running") or queue.get("running") or []
        pending = queue.get("queue_pending") or queue.get("pending") or []

        if _match_bucket(running):
            return "running"
        if _match_bucket(pending):
            return "queued"
        return None

    def _extract_status_fields(self, rec: Dict[str, Any]) -> Tuple[str, Optional[bool]]:
        """
        從 history 記錄中抽取（標準化）狀態字串與 completed 布林值。
        可能輸入：
          - "status": "success"
          - "status": {"status_str": "success", "completed": true}
          - "status": {"status": "error"}
          - 其它怪型態 → 回傳 ("", None)
        回傳：(status_str_lower, completed_bool_or_None)
        """
        status_val = rec.get("status")
        completed: Optional[bool] = None

        # dict：嘗試常見欄位
        if isinstance(status_val, dict):
            if isinstance(status_val.get("completed"), bool):
                completed = status_val.get("completed")
            for k in ("status_str", "status", "state", "phase", "label", "text"):
                if k in status_val and status_val[k] is not None:
                    try:
                        return str(status_val[k]).strip().lower(), completed
                    except Exception:
                        break
            return "", completed

        # 字串/數字：直接字串化
        if isinstance(status_val, (str, int, float)):
            try:
                return str(status_val).strip().lower(), completed
            except Exception:
                return "", completed

        # 其它型別：未知
        return "", completed

    def check_status(self, prompt_id: str) -> str:
        """
        回傳：'done' | 'failed' | 'running' | 'queued' | 'unknown'
        容錯處理 history.status 為 dict 的情況，並考慮 completed/error/node_errors。
        """
        # 先看 queue（是否在跑或排隊）
        try:
            q = self.get_queue()
            inq = self._id_in_queue(q, prompt_id)
            if inq:
                return inq  # 'running' 或 'queued'
        except Exception as e:
            print(f"[debug] 檢查 queue 失敗: {e}")

        # 看 history（是否已有結果）
        rec = self.peek_history(prompt_id)
        if rec is not None and isinstance(rec, dict):
            # 1) 正規化 status
            status_str, completed = self._extract_status_fields(rec)

            # 2) 明確失敗
            if status_str in ("failed", "error", "interrupted", "exception"):
                return "failed"

            # 3) 明確成功 or completed=True
            if completed is True or status_str in ("completed", "success", "ok", "done", "finished"):
                return "done"

            # 4) 有錯誤欄位也視為失敗
            if rec.get("error") or rec.get("node_errors"):
                # node_errors 可能是非空字典；有些版本運算途中也可能暫時填入，需要謹慎。
                # 這裡採「非空且 queue 不在跑/排隊」才視為失敗。
                try:
                    q2 = self.get_queue()
                    if not self._id_in_queue(q2, prompt_id):
                        return "failed"
                except Exception:
                    return "failed"

            # 5) 有輸出即完成
            if rec.get("outputs"):
                return "done"

            # 6) 沒有明確狀態/輸出，但 history 有記錄且不在 queue：可能是「無輸出節點」→ 回 'unknown'
            try:
                q2 = self.get_queue()
                inq2 = self._id_in_queue(q2, prompt_id)
                if not inq2:
                    return "unknown"
                return inq2
            except Exception:
                return "unknown"

        # history 也沒有：unknown
        return "unknown"

    # ======================
    # 等待直到完成（可選）
    # ======================
    def tail_history_until_done(self, prompt_id: str, timeout: float = 300.0, heartbeat: bool = True) -> Dict[str, Any]:
        """
        一直輪詢 queue/history，直到任務完成或失敗或超時。
        回傳最後的 history 記錄（可能是空 dict），失敗則丟例外。
        """
        t0 = time.time()
        last_status = None
        while True:
            try:
                status = self.check_status(prompt_id)
                if status != last_status and heartbeat:
                    print(f"[poll] {prompt_id[:8]}… status={status}")
                last_status = status

                if status == "running" or status == "queued":
                    pass  # 繼續等
                elif status == "done":
                    # 嘗試獲取完整記錄
                    rec = self.peek_history(prompt_id)
                    return rec if rec else {"status": "done", "outputs": None}
                elif status == "failed":
                    raise RuntimeError(f"任務執行失敗：{prompt_id}")
                else:
                    # unknown → 再看一次 history
                    rec = self.peek_history(prompt_id)
                    if rec and rec.get("outputs"):
                        return rec
                    # 若真的無法判定，暫停一下
            except Exception as e:
                if heartbeat:
                    print(f"[wait] 錯誤：{e}")

            time.sleep(self.poll_interval)
            if time.time() - t0 > timeout:
                raise TimeoutError(f"等待超時（{timeout}s）：{prompt_id}")
