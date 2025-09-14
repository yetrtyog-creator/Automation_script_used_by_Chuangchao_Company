# faceswap_main.py
from __future__ import annotations

import os
import sys
import time
import signal
from contextlib import contextmanager
from typing import Iterable, Any

# 依你的專案結構（model/ 模組）
from model.comfy_launcher import ensure_comfyui
from model.stage1_api import prepare_stage1_jobs, submit_stage1_jobs
from model.settings import get_settings


# ========== 使用者可調的環境旗標 ==========
# 預設寬鬆模式（忽略常見非致命錯誤，成功送單與輪詢即視為完成）
FS_LENIENT = os.getenv("FS_LENIENT", "1") not in ("", "0", "false", "False")
# 額外要忽略的錯誤關鍵字（逗號分隔）；碰到就當作警告而非致命
FS_IGNORE_ERRORS = os.getenv(
    "FS_IGNORE_ERRORS",
    # 預設忽略這些關鍵字
    "extra_pnginfo,missing 'workflow' key,Failed to validate prompt for output"
).split(",")


def _get_setting_safe(key: str, default: Any):
    """讀取設定失敗時回退 default，不讓主流程因設定缺失而中斷。"""
    try:
        return get_settings(key, as_dict=False)
    except Exception:
        return default


def _ensure_default_env():
    """沒有手動設時，補上避免 metadata 噴錯的建議開關。"""
    os.environ.setdefault("COMFY_STRIP_EXTRA_PNGINFO", "1")
    os.environ.setdefault("COMFY_DISABLE_SAVE_META", "1")
    # 你也可視情況加上剝除欄位（若 workflow 自己會寫入 metadata 類輸入）
    # os.environ.setdefault("COMFY_STRIP_FIELDS", "extra_pnginfo,read_metadata,read_workflow,use_image_metadata")


def _should_ignore_error_text(msg: str) -> bool:
    m = msg.lower()
    for kw in FS_IGNORE_ERRORS:
        kw = kw.strip().lower()
        if kw and kw in m:
            return True
    return False


@contextmanager
def _graceful_sigint():
    """讓 Ctrl+C 能優雅中斷。"""
    orig = signal.getsignal(signal.SIGINT)
    try:
        signal.signal(signal.SIGINT, signal.default_int_handler)
        yield
    finally:
        signal.signal(signal.SIGINT, orig)


def _print_summary(selectors: Iterable[str]):
    print("[OK] 設定載入成功")
    for k in selectors:
        v = _get_setting_safe(k, None)
        print(f"  {k:22s} = {v}")


def main() -> int:
    # 顯示關鍵設定（可按需增減）
    selectors = [
        "schema_version",
        "comfyui.dir",
        "comfyui.port",
        "comfyui.workflows_dir",
        "paths_source_root",
        "paths_staging_root",
        "paths_output_root",
        "workflows.stage1",
        "workflow_paths.stage1",
        "pipeline.run_stage1",
        "pipeline.collection_name",
        "pipeline.max_inflight",
        "pipeline.max_retries",
        "pipeline.poll_interval_sec",
    ]
    _print_summary(selectors)

    # 自動補建議環境變數（若未手設）
    _ensure_default_env()

    if not _get_setting_safe("pipeline.run_stage1", True):
        print("[stage1] skipped by config (pipeline.run_stage1 = false).")
        return 0

    # 啟動或確認 ComfyUI
    port = int(_get_setting_safe("comfyui.port", os.getenv("COMFYUI_PORT", 8199)))
    with _graceful_sigint():
        ensure_comfyui(port=port)

    try:
        # 準備 Stage1 任務
        jobs = prepare_stage1_jobs()
        print(f"[stage1] prepared {len(jobs)} jobs.")

        # 送單與輪詢（遇到非致命錯誤，視 FS_LENIENT 決定是否中止）
        try:
            submit_stage1_jobs(
                jobs,
                max_inflight=int(_get_setting_safe("pipeline.max_inflight", 4)),
                poll_interval_sec=float(_get_setting_safe("pipeline.poll_interval_sec", 0.75)),
                max_retries=int(_get_setting_safe("pipeline.max_retries", 2)),
            )
        except Exception as e:
            msg = f"{e}"
            if FS_LENIENT and _should_ignore_error_text(msg):
                print(f"[stage1] ⚠️  忽略可容忍錯誤（lenient）: {e}")
            elif FS_LENIENT and not msg:
                # 某些例外沒有訊息，但實際上已執行完成；在寬鬆模式下一樣略過
                print(f"[stage1] ⚠️  忽略未分類例外（lenient，無訊息）：{e.__class__.__name__}")
            else:
                # 非寬鬆或不可忽略的錯誤，當作致命
                raise

    except KeyboardInterrupt:
        print("\n[stage1] interrupted by user (Ctrl+C).")
        return 130
    except Exception as e:
        # 其他未預期錯誤：在寬鬆模式下也盡量不擋住流程（若你想更保守，可改成直接 return 1）
        if FS_LENIENT and _should_ignore_error_text(str(e)):
            print(f"[stage1] ⚠️  忽略可容忍錯誤（lenient，outmost）: {e}")
        else:
            print(f"[stage1] ERROR: {e.__class__.__name__}: {e}")
            return 1

    print("[stage1] all jobs finished.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
