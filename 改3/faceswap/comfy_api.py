#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations
import json, time, uuid
from typing import Dict, Any, Optional
import requests

class ComfyAPI:
    def __init__(self, base_url: str, poll_interval: float = 1.0):
        self.base_url = base_url.rstrip("/")
        self.poll_interval = max(0.25, poll_interval)
        # 明確帶 client_id（雖然不是必填，但能改善行為一致性）
        self.client_id = f"faceswap-{uuid.uuid4().hex}"

    # --- 基礎 ---
    def object_info(self) -> dict:
        r = requests.get(self.base_url + "/object_info", timeout=10)
        r.raise_for_status()
        return r.json()

    def post_prompt(self, prompt: Dict[str, Any]) -> str:
        payload = {"prompt": prompt, "client_id": self.client_id}
        r = requests.post(self.base_url + "/prompt", json=payload, timeout=30)
        try:
            data = r.json()
        except Exception:
            r.raise_for_status()
            raise RuntimeError("ComfyUI /prompt 非 JSON 回應")
        if r.status_code != 200:
            raise RuntimeError(f"ComfyUI /prompt 錯誤：HTTP {r.status_code} → {data}")
        if "prompt_id" in data:
            return data["prompt_id"]
        if "error" in data:
            raise RuntimeError(f"ComfyUI /prompt 錯誤：{data['error']}")
        raise RuntimeError(f"ComfyUI /prompt 未回傳 prompt_id：{data}")

    def get_history(self, prompt_id: str) -> dict:
        r = requests.get(self.base_url + f"/history/{prompt_id}", timeout=15)
        r.raise_for_status()
        return r.json()

    # 單次「嘗試」讀一次 history
    def peek_history(self, prompt_id: str) -> Optional[dict]:
        try:
            hist = self.get_history(prompt_id)
            # 修正：正確解析 history 結構
            if isinstance(hist, dict):
                # ComfyUI 的 history API 可能返回 {prompt_id: {...}} 或 {"history": {prompt_id: {...}}}
                if prompt_id in hist:
                    return hist[prompt_id]
                elif "history" in hist and isinstance(hist["history"], dict):
                    return hist["history"].get(prompt_id)
                # 如果 history 是空的但請求成功，表示任務可能已完成但沒有輸出
                # 這種情況下返回一個標記對象
                if hist == {} or (isinstance(hist.get("history"), dict) and hist["history"] == {}):
                    return {"status": "completed", "outputs": None}
            return None
        except Exception as e:
            print(f"[debug] peek_history 錯誤: {e}")
            return None

    # --- 佇列觀測 ---
    def get_queue(self) -> dict:
        r = requests.get(self.base_url + "/queue", timeout=10)
        r.raise_for_status()
        return r.json()

    def _id_in_queue(self, q: dict, prompt_id: str) -> Optional[str]:
        # 嘗試在 queue_running / queue_pending 找 id
        try:
            # 檢查運行中的任務
            running = q.get("queue_running", [])
            if isinstance(running, list):
                for item in running:
                    if isinstance(item, dict):
                        # ComfyUI 通常用 [prompt_id, ...] 格式
                        if isinstance(item, list) and len(item) > 0 and item[0] == prompt_id:
                            return "running"
                        # 或者在 dict 中尋找
                        for v in item.values():
                            if v == prompt_id:
                                return "running"
                    elif isinstance(item, list) and len(item) > 0 and item[0] == prompt_id:
                        return "running"
            
            # 檢查等待中的任務
            pending = q.get("queue_pending", [])
            if isinstance(pending, list):
                for item in pending:
                    if isinstance(item, dict):
                        for v in item.values():
                            if v == prompt_id:
                                return "queued"
                    elif isinstance(item, list) and len(item) > 0 and item[0] == prompt_id:
                        return "queued"
        except Exception as e:
            print(f"[debug] _id_in_queue 錯誤: {e}")
        return None

    def check_status(self, prompt_id: str) -> str:
        """
        回傳：'done' | 'failed' | 'running' | 'queued' | 'unknown'
        改進版：更好地處理沒有輸出節點的情況
        """
        # 先檢查 queue（看是否還在運行）
        try:
            q = self.get_queue()
            inq = self._id_in_queue(q, prompt_id)
            if inq:
                return inq  # 'running' 或 'queued'
        except Exception as e:
            print(f"[debug] 檢查 queue 失敗: {e}")
        
        # 再檢查 history
        rec = self.peek_history(prompt_id)
        if rec is not None:
            # 檢查各種可能的狀態欄位
            status = rec.get("status", "").lower() if rec.get("status") else ""
            
            # 明確的失敗狀態
            if status in ("failed", "error", "interrupted"):
                return "failed"
            
            # 明確的成功狀態
            if status in ("completed", "success", "ok", "done", "finished"):
                return "done"
            
            # 如果有輸出，視為完成
            if rec.get("outputs"):
                return "done"
            
            # 如果 history 中有這個 ID 但沒有明確狀態，
            # 且不在 queue 中，很可能是完成了但沒有輸出節點
            # 這是 Load Image Batch 的典型情況
            if rec != {} and not inq:
                # 給它一點時間確認真的完成了
                time.sleep(0.5)
                # 再次檢查是否在 queue 中
                try:
                    q2 = self.get_queue()
                    if not self._id_in_queue(q2, prompt_id):
                        print(f"[debug] {prompt_id[:8]}... 可能已完成（無輸出）")
                        return "done"
                except Exception:
                    pass
            
            return "running"
        
        # 如果 history 和 queue 都沒有，返回 unknown
        return "unknown"

    # 阻塞等待（保留）
    def wait_done(self, prompt_id: str, heartbeat: bool = True, timeout: float = 900.0) -> dict:
        t0 = time.time()
        last_status = None
        while True:
            try:
                status = self.check_status(prompt_id)
                if status != last_status:
                    if heartbeat:
                        print(f"[wait] {prompt_id[:8]}... status={status}")
                    last_status = status
                
                if status == "done":
                    # 嘗試獲取完整記錄
                    rec = self.peek_history(prompt_id)
                    return rec if rec else {"status": "done", "outputs": None}
                elif status == "failed":
                    raise RuntimeError(f"任務執行失敗：{prompt_id}")
                
            except Exception as e:
                if heartbeat:
                    print(f"[wait] 錯誤：{e}")
            
            time.sleep(self.poll_interval)
            if time.time() - t0 > timeout:
                raise TimeoutError(f"等待超時（{timeout}s）：{prompt_id}")