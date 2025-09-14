# comfy_launcher.py
# =============================================================================
# 【API 說明（繁體中文）】
#
# 檔案用途
#   在 vast.ai（Linux 容器）中「啟動或附著」ComfyUI 服務，提供給 main.py 匯入呼叫。
#   會先檢查指定 port 是否已經有 ComfyUI 可回應；若尚未啟動，則自動啟動並等待就緒。
#
# 主要入口
#   ensure_comfyui(
#       *,
#       config_path: Optional[str | Path] = None,
#       port: Optional[int] = None,
#       listen_host: str = "0.0.0.0",
#       wait_seconds: float = 120.0,
#       extra_args: Optional[list[str]] = None,
#   ) -> dict
#
# 設定依賴
#   透過同層級 settings.py 的 get_settings() 讀取：
#     - comfyui.dir  : ComfyUI 根目錄（必需）
#     - comfyui.port : 預設啟動/檢查的連接埠（可被參數 port 覆蓋）
#
# 行為說明
#   1) 若本機 127.0.0.1:<port> 已能連線且 HTTP 可回應（/ 或 /api/nodes），則直接回報附著狀態，
#      不會重啟既有 ComfyUI。
#   2) 若未就緒，會依序嘗試尋找 Python 可執行檔（COMFY_PY > <comfy>/venv/bin/python >
#      /venv/main/bin/python > sys.executable），以 `python main.py --port <port> --listen <host>`
#      啟動 ComfyUI，並在 `wait_seconds` 內輪詢直到 HTTP 可回應。
#
# 回傳結構
#   {
#     "url": str,          # 供呼叫端顯示或存取的 base URL。
#     "port": int,         # 實際使用的連接埠。
#     "pid": int | None,   # 若是新啟動則為子程序 PID；若是附著既有服務則為 None。
#     "started_new": bool  # True 表示本次由本函式新啟動；False 表示附著既有服務。
#   }
#   （備註）若 listen_host 為 "0.0.0.0"，回傳的 url 會以 "127.0.0.1" 取代，避免回傳不可直連的位址。
#
# 可能拋出的例外
#   - FileNotFoundError : 找不到 ComfyUI 目錄或入口 main.py
#   - RuntimeError      : 子程序異常提早結束（尚未就緒）
#   - TimeoutError      : 逾時仍未就緒
#
# 典型用法（於 main.py）
#   from comfy_launcher import ensure_comfyui
#   info = ensure_comfyui()          # 讀取設定，必要時自動啟動
#   print("ComfyUI:", info["url"])
#
# 參數建議
#   - port        : 不指定時使用設定檔 comfyui.port
#   - listen_host : 容器對外建議保留 "0.0.0.0"；但回傳的 url 會自動使用 "127.0.0.1"
#   - extra_args  : 例如 ["--lowvram", "--force-fp16"] 等，會原樣傳給 ComfyUI
#
# 相依條件
#   - 檔案需與 settings.py 位於同一層，且 settings.py 需提供 get_settings()
# =============================================================================

from __future__ import annotations

import os
import sys
import time
import socket
import subprocess
from pathlib import Path
from typing import Optional

from settings import get_settings  # 依賴同層 settings.py


def _port_open_local(port: int, timeout: float = 0.5) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(timeout)
        try:
            s.connect(("127.0.0.1", port))
            return True
        except OSError:
            return False


def _http_ready(port: int, timeout: float = 1.5) -> bool:
    import urllib.request, urllib.error
    base = f"http://127.0.0.1:{port}"
    for ep in ("", "api/nodes"):
        try:
            with urllib.request.urlopen(f"{base}/{ep}", timeout=timeout) as resp:
                code = getattr(resp, "status", 200)
                if 200 <= code < 500:
                    return True
        except urllib.error.URLError:
            pass
        except Exception:
            pass
    return False


def _pick_python(comfy_dir: Path) -> str:
    # vast.ai 常見：/venv/main/bin/python；否則退回 sys.executable
    env_py = os.environ.get("COMFY_PY")
    if env_py and Path(env_py).exists():
        return env_py
    venv_py = comfy_dir / "venv" / "bin" / "python"
    if venv_py.exists():
        return str(venv_py)
    common = Path("/venv/main/bin/python")
    if common.exists():
        return str(common)
    return sys.executable


def ensure_comfyui(
    *,
    config_path: Optional[str | Path] = None,
    port: Optional[int] = None,
    listen_host: str = "0.0.0.0",
    wait_seconds: float = 120.0,
    extra_args: Optional[list[str]] = None,
) -> dict[str, object]:
    """
    確保 ComfyUI 在容器內就緒；若未啟動則啟動之。
    回傳: {'url': str, 'port': int, 'pid': int|None, 'started_new': bool}
    """
    # 讀設定（必要: comfyui.dir, comfyui.port）
    ss = get_settings(["comfyui.dir", "comfyui.port"], config_path=config_path, as_dict=True)
    comfy_dir = Path(ss["comfyui.dir"]).expanduser().resolve()
    cfg_port = int(ss["comfyui.port"])
    port = int(port or cfg_port)

    if not comfy_dir.exists():
        raise FileNotFoundError(f"ComfyUI dir not found: {comfy_dir}")

    # 若已在執行且 HTTP 可回應，直接回傳（附著）
    if _port_open_local(port) and _http_ready(port):
        url_host = "127.0.0.1" if listen_host == "0.0.0.0" else listen_host
        return {
            "url": f"http://{url_host}:{port}",
            "port": port,
            "pid": None,
            "started_new": False,
        }

    # 準備啟動
    python_exe = _pick_python(comfy_dir)
    entry = comfy_dir / "main.py"
    if not entry.exists():
        raise FileNotFoundError(f"ComfyUI entry not found: {entry}")

    cmd = [
        python_exe, "-u", str(entry),
        "--port", str(port),
        "--listen", listen_host,
    ]
    if extra_args:
        cmd.extend(extra_args)

    # 在 ComfyUI 目錄下啟動；輸出導向父程序（方便 docker logs）
    proc = subprocess.Popen(
        cmd,
        cwd=str(comfy_dir),
        env=os.environ.copy(),
        stdout=None,
        stderr=None,
        text=False,
    )

    # 等候就緒
    start = time.time()
    delay = 0.25
    while True:
        # 若子程序提前退出
        if proc.poll() is not None:
            raise RuntimeError(f"ComfyUI exited early with code {proc.returncode}")

        if _http_ready(port):
            break

        if time.time() - start > wait_seconds:
            raise TimeoutError(f"ComfyUI not ready within {wait_seconds:.0f}s on port {port}")

        time.sleep(delay)
        delay = min(delay * 1.5, 2.0)

    url_host = "127.0.0.1" if listen_host == "0.0.0.0" else listen_host
    return {
        "url": f"http://{url_host}:{port}",
        "port": port,
        "pid": proc.pid,
        "started_new": True,
    }


# 可選：簡單 CLI 測試
if __name__ == "__main__":
    info = ensure_comfyui()
    print(f"[OK] ComfyUI {'started' if info['started_new'] else 'attached'} @ {info['url']} (port={info['port']}) pid={info['pid']}")
