#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Dict, List
import time, json, copy

from .comfy_api import ComfyAPI

@dataclass
class Task:
    name: str
    workflow: Dict[str, Any]
    retries: int = 0
    max_retries: int = 2
    meta: Dict[str, Any] = field(default_factory=dict)

def run_queue(api: ComfyAPI, tasks: List[Task], max_inflight: int = 4, poll_interval: float = 1.0) -> List[Dict[str, Any]]:
    inflight: List[Dict[str, Any]] = []  # {"task": Task, "id": prompt_id, "ts": enqueue_ts, "last_status": str, "last_change": ts}
    done: List[Dict[str, Any]] = []
    pending: List[Task] = tasks[:]

    def launch_one(t: Task) -> None:
        wf = copy.deepcopy(t.workflow)
        pid = api.post_prompt(wf)
        now = time.time()
        inflight.append({
            "task": t, 
            "id": pid, 
            "ts": now, 
            "last_status": "queued", 
            "last_change": now,
            "no_change_count": 0  # 計數器：狀態沒變化的次數
        })
        print(f"✓ /prompt 已入隊，prompt_id = {pid} | name={t.name} | tries={t.retries+1}")

    # 啟發式完成檢測參數
    STALE_THRESHOLD = 10  # 如果狀態 10 秒沒變化
    STALE_CHECKS = 3      # 且連續檢查 3 次都沒變化
    MAX_UNKNOWN_TIME = 30 # unknown 狀態超過 30 秒視為完成（對於沒有輸出的工作流）

    while pending or inflight:
        # 盡量發車
        while pending and len(inflight) < max_inflight:
            t = pending.pop(0)
            try:
                launch_one(t)
            except Exception as e:
                print(f"[enqueue] 失敗：{t.name} → {e}")
                if t.retries < t.max_retries:
                    t.retries += 1
                    pending.append(t)
                else:
                    print(f"[drop] 超過重試次數：{t.name}")
                    done.append({"task": t, "error": str(e)})

        # 探測 inflight（非阻塞）
        i = 0
        now = time.time()
        while i < len(inflight):
            item = inflight[i]
            tid = item["id"]
            t = item["task"]

            try:
                status = api.check_status(tid)  # 'done' | 'failed' | 'running' | 'queued' | 'unknown'
            except Exception as e:
                status = f"probe-error: {e}"

            age = int(now - item["ts"])
            time_since_change = int(now - item.get("last_change", item["ts"]))
            
            # 狀態變化檢測
            if status != item.get("last_status"):
                print(f"[peek] {t.name} | {tid[:8]}… | status={status} | age={age}s")
                item["last_status"] = status
                item["last_change"] = now
                item["no_change_count"] = 0
            else:
                item["no_change_count"] = item.get("no_change_count", 0) + 1

            # === 改進的完成檢測邏輯 ===
            
            # 1. 明確的完成狀態
            if status == "done":
                print(f"[task] 完成：{t.name} | {tid[:8]}…")
                # 取一次完整歷史回寫
                rec = None
                try:
                    rec = api.peek_history(tid)
                    if not rec:
                        rec = {"status": "done", "outputs": None}
                except Exception:
                    rec = {"status": "done"}
                done.append({"task": t, "result": rec})
                inflight.pop(i)
                continue
            
            # 2. 明確的失敗狀態
            elif status == "failed":
                print(f"[task] 失敗：{t.name} | {tid[:8]}…")
                inflight.pop(i)
                if t.retries < t.max_retries:
                    t.retries += 1
                    pending.append(t)
                else:
                    print(f"[drop] 超過重試次數：{t.name}")
                    done.append({"task": t, "error": "failed"})
                continue
            
            # 3. 長時間 unknown 狀態（可能是沒有輸出節點的工作流）
            elif status == "unknown":
                if time_since_change > MAX_UNKNOWN_TIME:
                    print(f"[heuristic] {t.name} | {tid[:8]}… 長時間 unknown，視為完成（無輸出）")
                    done.append({"task": t, "result": {"status": "done", "no_output": True}})
                    inflight.pop(i)
                    continue
                elif time_since_change > 15:
                    print(f"[warn] {t.name} | {tid[:8]}… unknown 狀態 {time_since_change}s")
                    print("       可能原因：工作流沒有輸出節點（SaveImage/PreviewImage）")
                    print("       建議：在 config.yaml 設定 ensure_sink_from 自動添加輸出")
            
            # 4. 長時間沒有狀態變化（可能卡住或已完成但檢測不到）
            elif status in ("running", "queued"):
                if time_since_change > STALE_THRESHOLD and item["no_change_count"] > STALE_CHECKS:
                    # 再次深入檢查
                    try:
                        q = api.get_queue()
                        running = q.get("queue_running", [])
                        pending_q = q.get("queue_pending", [])
                        
                        # 檢查是否真的還在 queue 中
                        in_queue = False
                        for queue_list in [running, pending_q]:
                            for queue_item in queue_list:
                                if isinstance(queue_item, list) and len(queue_item) > 0:
                                    if queue_item[0] == tid:
                                        in_queue = True
                                        break
                        
                        if not in_queue:
                            print(f"[heuristic] {t.name} | {tid[:8]}… 不在 queue 中，視為完成")
                            done.append({"task": t, "result": {"status": "done", "heuristic": True}})
                            inflight.pop(i)
                            continue
                    except Exception as e:
                        print(f"[debug] 深入檢查失敗: {e}")
            
            # 5. 超時保護（防止永遠卡住）
            if age > 300:  # 5 分鐘超時
                print(f"[timeout] {t.name} | {tid[:8]}… 執行超時")
                inflight.pop(i)
                if t.retries < t.max_retries:
                    t.retries += 1
                    pending.append(t)
                else:
                    done.append({"task": t, "error": "timeout"})
                continue
            
            # 保留在 inflight
            i += 1

        # 心跳輸出
        if inflight or pending:
            status_summary = []
            for item in inflight[:3]:  # 只顯示前 3 個
                status_summary.append(f"{item['task'].name}:{item['last_status']}")
            if len(inflight) > 3:
                status_summary.append(f"...+{len(inflight)-3}")
            
            print(f"[hb] 佇列 {len(inflight)} [{', '.join(status_summary)}] | 待送 {len(pending)} | 已完成 {len(done)}")
        
        time.sleep(poll_interval)

    print(f"[scheduler] 全部任務完成：成功 {len([d for d in done if 'error' not in d])}，失敗 {len([d for d in done if 'error' in d])}")
    return done