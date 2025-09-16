# -*- coding: utf-8 -*-
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from .comfy_api import ComfyAPI


@dataclass
class Task:
    name: str
    workflow: Dict[str, Any]
    max_retries: int = 0


def _submit(api: ComfyAPI, task: Task) -> Tuple[Optional[str], Optional[str]]:
    """送出單一任務"""
    print(f"[scheduler] 提交任務: {task.name}")
    try:
        resp = api.post_prompt(task.workflow)
        if isinstance(resp, str):
            print(f"[scheduler] 成功提交，prompt_id: {resp[:8]}...")
            return resp, None
        if isinstance(resp, dict):
            pid = resp.get("prompt_id") or resp.get("id") or resp.get("promptId")
            if pid:
                print(f"[scheduler] 成功提交，prompt_id: {str(pid)[:8]}...")
                return str(pid), None
        return None, "post_prompt: 無法取得 prompt_id"
    except Exception as e:
        print(f"[scheduler] 提交失敗: {e}")
        return None, f"post_prompt: {e!r}"


def _check_done(api: ComfyAPI, prompt_id: str, task_name: str) -> Tuple[bool, Optional[str]]:
    """輪詢單一任務 - 修正版"""
    try:
        # 使用完整的 history 端點
        hist_resp = api._get(f"/history/{prompt_id}")
        hist_data = hist_resp.json()
        
        # ComfyUI 的 history 格式通常是 {prompt_id: {record}}
        if prompt_id in hist_data:
            record = hist_data[prompt_id]
            
            # 檢查 status 字段（這是關鍵）
            if 'status' in record:
                status_obj = record['status']
                
                # status 可能是 dict 或 string
                if isinstance(status_obj, dict):
                    # 檢查 status dict 中的狀態
                    status_str = status_obj.get('status_str', '')
                    completed = status_obj.get('completed', False)
                    execution_time = status_obj.get('execution_time')
                    
                    # 如果明確標記為完成
                    if completed:
                        print(f"[scheduler] ✓ 任務完成: {task_name} (completed=True)")
                        return True, None
                    
                    # 如果有執行時間，通常表示已完成
                    if execution_time is not None:
                        print(f"[scheduler] ✓ 任務完成: {task_name} (execution_time={execution_time})")
                        return True, None
                    
                    # 檢查狀態字符串
                    if 'error' in str(status_str).lower() or 'fail' in str(status_str).lower():
                        print(f"[scheduler] ✗ 任務失敗: {task_name}")
                        return True, status_str
                    elif 'success' in str(status_str).lower() or 'completed' in str(status_str).lower():
                        print(f"[scheduler] ✓ 任務完成: {task_name}")
                        return True, None
                else:
                    # status 是字符串
                    status_str = str(status_obj).lower()
                    if 'error' in status_str or 'fail' in status_str:
                        print(f"[scheduler] ✗ 任務失敗: {task_name}")
                        return True, status_obj
                    elif 'success' in status_str or 'completed' in status_str:
                        print(f"[scheduler] ✓ 任務完成: {task_name}")
                        return True, None
            
            # 檢查是否有 outputs 鍵（即使是空的）
            if 'outputs' in record:
                # outputs 存在就表示執行完成（即使是空字典）
                print(f"[scheduler] ✓ 任務完成: {task_name} (有 outputs 鍵)")
                return True, None
            
            # 檢查是否還在隊列中
            queue_data = api.get_queue()
            pending = queue_data.get('queue_pending', []) or queue_data.get('pending', [])
            running = queue_data.get('queue_running', []) or queue_data.get('running', [])
            
            # 檢查是否還在隊列中
            in_queue = False
            for item in pending + running:
                if isinstance(item, list) and len(item) > 0:
                    if str(item[0]) == prompt_id:
                        in_queue = True
                        break
                elif isinstance(item, dict):
                    if item.get('prompt_id') == prompt_id or item.get('id') == prompt_id:
                        in_queue = True
                        break
            
            if not in_queue and record:
                # 不在隊列中且有歷史記錄 = 完成
                print(f"[scheduler] ✓ 任務完成: {task_name} (不在隊列中)")
                return True, None
        
        # 如果 history 中沒有記錄，可能還在執行
        return False, None
        
    except Exception as e:
        print(f"[scheduler] 檢查狀態時發生錯誤 {prompt_id[:8]}...: {e}")
        # 發生錯誤時不認為完成，繼續輪詢
        return False, None


def run_queue(api: ComfyAPI,
              tasks: List[Task],
              *,
              max_inflight: Optional[int] = None,
              poll_interval: float = 0.75) -> List[Dict[str, Any]]:
    """
    送出任務佇列並輪詢結果
    加入詳細的進度日誌
    """
    print(f"[scheduler] 開始處理 {len(tasks)} 個任務")
    results: List[Dict[str, Any]] = []
    if not tasks:
        return results

    pending: List[Task] = list(tasks)
    active: List[Dict[str, Any]] = []

    def _res(task: Task, prompt_id: Optional[str], error: Optional[str]) -> Dict[str, Any]:
        r: Dict[str, Any] = {"task": task}
        if prompt_id:
            r["prompt_id"] = prompt_id
        if error:
            r["error"] = error
        return r

    unbounded = (max_inflight is None) or (max_inflight == 0)
    print(f"[scheduler] 模式: {'無上限' if unbounded else f'上限 {max_inflight}'}")

    if unbounded:
        # 一次全部提交
        print(f"[scheduler] 批次提交所有 {len(pending)} 個任務...")
        while pending:
            task = pending.pop(0)
            pid, err = _submit(api, task)
            if err:
                if task.max_retries > 0:
                    task.max_retries -= 1
                    print(f"[scheduler] 重試任務: {task.name} (剩餘重試: {task.max_retries})")
                    pending.append(task)
                else:
                    results.append(_res(task, None, err))
                continue
            active.append({"task": task, "prompt_id": pid, "attempts": 0})

        # 輪詢直到全部完成
        print(f"[scheduler] 開始輪詢 {len(active)} 個活躍任務...")
        poll_count = 0
        while active:
            poll_count += 1
            if poll_count % 10 == 0:  # 每10次輪詢顯示一次狀態
                print(f"[scheduler] 輪詢中... 剩餘活躍任務: {len(active)}")
            
            time.sleep(poll_interval)
            
            for i in range(len(active) - 1, -1, -1):
                st = active[i]
                done, err = _check_done(api, st["prompt_id"], st["task"].name)
                if not done:
                    continue
                results.append(_res(st["task"], st["prompt_id"], err))
                active.pop(i)
    else:
        # 有上限（節流）
        max_inflight = max(1, int(max_inflight))
        total_submitted = 0
        poll_count = 0
        
        while pending or active:
            # 補滿 active
            while pending and len(active) < max_inflight:
                task = pending.pop(0)
                total_submitted += 1
                print(f"[scheduler] 提交 {total_submitted}/{len(tasks) + total_submitted - len(pending)}: {task.name}")
                
                pid, err = _submit(api, task)
                if err:
                    if task.max_retries > 0:
                        task.max_retries -= 1
                        pending.append(task)
                    else:
                        results.append(_res(task, None, err))
                    continue
                active.append({"task": task, "prompt_id": pid, "attempts": 0})

            if not active and pending:
                continue

            time.sleep(poll_interval)
            poll_count += 1
            
            if poll_count % 10 == 0:  # 每10次輪詢顯示狀態
                print(f"[scheduler] 活躍: {len(active)}, 待處理: {len(pending)}, 完成: {len(results)}")

            for i in range(len(active) - 1, -1, -1):
                st = active[i]
                done, err = _check_done(api, st["prompt_id"], st["task"].name)
                if not done:
                    continue
                results.append(_res(st["task"], st["prompt_id"], err))
                active.pop(i)

    print(f"[scheduler] 全部任務處理完成，共 {len(results)} 個結果")
    return results