"""
模組：model/settings.py
用途：集中定義「設定檔讀取／驗證」與「點號選擇器查詢」API，供整個換臉三階段管線與 ComfyUI 啟動器使用。
本模組將 config.yaml（schema_version=3）解析為不可變 dataclass，並提供安全的工作流路徑解析與便捷取值。

【提供的 API】
1) load_config(config_path: str | Path | None = None) -> Config
   - 讀取並驗證 YAML 設定，只在第一次呼叫時進行 I/O；結果以 lru_cache(1) 快取。
   - 驗證內容：
     - comfyui.dir 必須存在且含 main.py；comfyui.port 介於 1~65535。
     - 自動推導 comfyui.workflows_dir = <comfyui.dir>/user/default/workflows，且需存在。
     - paths.source_root / staging_root / output_root 必須存在且為資料夾。
     - pipeline.* 型別與範圍檢查（例如 max_inflight>0、poll_interval_sec>0、collection_name 非空）。
     - workflows.stage{1,2,3} 只允許位於 comfyui.workflows_dir 內，並檔案必須存在（以安全拼接防穿越）。
   - 可能拋出的例外：FileNotFoundError、NotADirectoryError、ValueError、TypeError、KeyError。

2) get_settings(selectors: Iterable[str] | str, *, config_path: str | Path | None = None, as_dict: bool = True)
   - 以「點號選擇器」讀取設定值（會自動呼叫並使用 load_config 的快取）。
   - selectors 可為單一字串（回傳單值或 {selector: value}）或多個字串（回傳 dict）。
   - 範例選擇器：
       'comfyui.port'
       'comfyui.workflows_dir'
       'workflows.stage3'        # YAML 中的名稱
       'workflow_paths.stage3'   # 解析後的絕對路徑
       'pipeline.max_inflight'
       'paths_source_root'
   - 找不到路徑時拋出 KeyError。

【資料結構（皆為 frozen dataclass，不可變，執行緒安全）】
- ComfyUICfg：{ dir: Path, port: int, workflows_dir: Path }
- PipelineCfg：{ run_stage1, run_stage2, run_stage3, collection_name, max_inflight, max_retries,
                 poll_interval_sec, keep_going_on_stage_fail }
- WorkflowsNames：YAML 中提供的檔名（相對於 comfyui.workflows_dir）
- WorkflowsPaths：經安全解析後的絕對路徑
- Config：彙整以上所有欄位，schema_version 固定為 3

【安全性設計】
- _safe_join_within(root, rel)：禁止絕對路徑與 '..'，並確保解析結果仍位於 root 底下，避免目錄穿越。
- 所有型別轉換具明確錯誤訊息（型別/範圍檢查）。

【簡易使用範例】
    from model.settings import load_config, get_settings

    cfg = load_config()  # 第一次會讀檔與驗證，之後使用快取
    port = get_settings('comfyui.port', as_dict=False)
    p1   = get_settings('workflow_paths.stage1', as_dict=False)  # 取得 stage1 絕對路徑（Path 轉為 str 於輸出前）

此模組僅負責設定與路徑驗證，不處理排程、佇列提交或 ComfyUI 呼叫；請在其他模組（例如 comfy_launcher.py、
comfy_api.py、faceswap_main.py）中組合使用本模組輸出的設定物件與便利查詢。
"""
from __future__ import annotations

from dataclasses import dataclass, asdict, is_dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable, Optional

import yaml


# ===== 型別 =====
@dataclass(frozen=True)
class ComfyUICfg:
    dir: Path
    port: int
    workflows_dir: Path  # <comfyui>/user/default/workflows

@dataclass(frozen=True)
class PipelineCfg:
    run_stage1: bool
    run_stage2: bool
    run_stage3: bool
    collection_name: str
    max_inflight: int
    max_retries: int
    poll_interval_sec: float
    keep_going_on_stage_fail: bool

@dataclass(frozen=True)
class WorkflowsNames:
    stage1: str
    stage2: str
    stage3: str

@dataclass(frozen=True)
class WorkflowsPaths:
    stage1: Path
    stage2: Path
    stage3: Path

@dataclass(frozen=True)
class Config:
    schema_version: int
    comfyui: ComfyUICfg
    paths_source_root: Path
    paths_staging_root: Path
    paths_output_root: Path
    pipeline: PipelineCfg
    workflows: WorkflowsNames      # 你在 YAML 中提供的名稱
    workflow_paths: WorkflowsPaths # 解析後的絕對路徑（位於 comfyui.workflows_dir 內）


# ===== 工具 =====
def _default_config_path() -> Path:
    return Path(__file__).resolve().parents[1] / "config.yaml"

def _need(d: dict, key: str, ctx: str) -> Any:
    if key not in d:
        raise KeyError(f"config.yaml 缺少必填欄位：{ctx}.{key}")
    return d[key]

def _as_int(v: Any, ctx: str) -> int:
    try:
        return int(v)
    except Exception:
        raise TypeError(f"{ctx} 應為整數，取得 {v!r}")

def _as_float(v: Any, ctx: str) -> float:
    try:
        return float(v)
    except Exception:
        raise TypeError(f"{ctx} 應為數值，取得 {v!r}")

def _as_bool(v: Any, ctx: str) -> bool:
    if isinstance(v, bool):
        return v
    if isinstance(v, str) and v.lower() in {"true", "false"}:
        return v.lower() == "true"
    raise TypeError(f"{ctx} 應為布林值 true/false，取得 {v!r}")

def _as_path(v: Any, ctx: str) -> Path:
    try:
        return Path(str(v)).expanduser().resolve()
    except Exception:
        raise TypeError(f"{ctx} 不是有效路徑：{v!r}")

def _safe_join_within(root: Path, rel: str, *, ctx: str) -> Path:
    """
    把 rel（檔名或在 root 內的相對路徑）安全拼接到 root：
      - 禁止絕對路徑
      - 禁止包含 '..'
      - 解析後必須仍在 root 範圍內
    """
    p = Path(rel)
    if p.is_absolute():
        raise ValueError(f"{ctx} 不可為絕對路徑：{rel!r}")
    # 禁止任何層級的 '..'
    if any(part == ".." for part in p.parts):
        raise ValueError(f"{ctx} 不可包含 '..'：{rel!r}")

    resolved = (root / p).resolve()
    try:
        resolved.relative_to(root)
    except Exception:
        raise ValueError(f"{ctx} 超出允許目錄：{resolved} 不在 {root}")
    return resolved

def _dot_get(d: dict, sel: str) -> Any:
    cur: Any = d
    for p in sel.split("."):
        if isinstance(cur, dict) and p in cur:
            cur = cur[p]
        else:
            raise KeyError(f"設定中找不到路徑：{sel}")
    return cur

def _to_plain(o: Any) -> Any:
    if is_dataclass(o):
        return {k: _to_plain(v) for k, v in asdict(o).items()}
    if isinstance(o, Path):
        return str(o)
    if isinstance(o, dict):
        return {k: _to_plain(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return type(o)(_to_plain(v) for v in o)
    return o


# ===== 讀取與驗證（schema v3） =====
@lru_cache(maxsize=1)
def load_config(config_path: Optional[str | Path] = None) -> Config:
    cfg_path = Path(config_path) if config_path else _default_config_path()
    if not cfg_path.exists():
        raise FileNotFoundError(f"找不到設定檔：{cfg_path}")

    with open(cfg_path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    schema_version = _as_int(raw.get("schema_version", 3), "schema_version")
    if schema_version != 3:
        raise ValueError(f"不支援的 schema_version：{schema_version}（目前僅支援 3）")

    # comfyui
    comfy_raw = _need(raw, "comfyui", "root")
    comfy_dir = _as_path(_need(comfy_raw, "dir", "comfyui"), "comfyui.dir")
    if not comfy_dir.exists() or not comfy_dir.is_dir():
        raise FileNotFoundError(f"comfyui.dir 不存在或不是資料夾：{comfy_dir}")
    if not (comfy_dir / "main.py").exists():
        raise FileNotFoundError(f"comfyui.dir 缺少 main.py：{comfy_dir}")
    workflows_dir = comfy_dir / "user" / "default" / "workflows"
    if not workflows_dir.exists() or not workflows_dir.is_dir():
        raise FileNotFoundError(f"找不到 ComfyUI 工作流目錄：{workflows_dir}（請先啟動一次 ComfyUI 或建立資料夾）")
    comfy_port = _as_int(_need(comfy_raw, "port", "comfyui"), "comfyui.port")
    if not (1 <= comfy_port <= 65535):
        raise ValueError("comfyui.port 必須介於 1~65535")
    comfyui = ComfyUICfg(dir=comfy_dir, port=comfy_port, workflows_dir=workflows_dir.resolve())

    # paths
    paths_raw = _need(raw, "paths", "root")
    src = _as_path(_need(paths_raw, "source_root", "paths"), "paths.source_root")
    stg = _as_path(_need(paths_raw, "staging_root", "paths"), "paths.staging_root")
    out = _as_path(_need(paths_raw, "output_root",  "paths"), "paths.output_root")
    for p, name in [(src, "paths.source_root"), (stg, "paths.staging_root"), (out, "paths.output_root")]:
        if not p.exists(): raise FileNotFoundError(f"{name} 不存在：{p}")
        if not p.is_dir(): raise NotADirectoryError(f"{name} 不是資料夾：{p}")

    # pipeline
    p_raw = _need(raw, "pipeline", "root")
    pipe = PipelineCfg(
        run_stage1=_as_bool(_need(p_raw, "run_stage1", "pipeline"), "pipeline.run_stage1"),
        run_stage2=_as_bool(_need(p_raw, "run_stage2", "pipeline"), "pipeline.run_stage2"),
        run_stage3=_as_bool(_need(p_raw, "run_stage3", "pipeline"), "pipeline.run_stage3"),
        collection_name=str(_need(p_raw, "collection_name", "pipeline")).strip(),
        max_inflight=_as_int(_need(p_raw, "max_inflight", "pipeline"), "pipeline.max_inflight"),
        max_retries=_as_int(_need(p_raw, "max_retries", "pipeline"), "pipeline.max_retries"),
        poll_interval_sec=_as_float(_need(p_raw, "poll_interval_sec", "pipeline"), "pipeline.poll_interval_sec"),
        keep_going_on_stage_fail=_as_bool(_need(p_raw, "keep_going_on_stage_fail", "pipeline"),
                                          "pipeline.keep_going_on_stage_fail"),
    )
    if not pipe.collection_name:
        raise ValueError("pipeline.collection_name 不可為空")
    if pipe.max_inflight <= 0:
        raise ValueError("pipeline.max_inflight 必須 > 0")
    if pipe.max_retries < 0:
        raise ValueError("pipeline.max_retries 必須 >= 0")
    if pipe.poll_interval_sec <= 0:
        raise ValueError("pipeline.poll_interval_sec 必須 > 0")

    # workflows（只允許 comfyui.workflows_dir 下的路徑名）
    w_raw = _need(raw, "workflows", "root")
    names = WorkflowsNames(
        stage1=str(_need(w_raw, "stage1", "workflows")),
        stage2=str(_need(w_raw, "stage2", "workflows")),
        stage3=str(_need(w_raw, "stage3", "workflows")),
    )
    # 安全解析＆存在性檢查
    p1 = _safe_join_within(comfyui.workflows_dir, names.stage1, ctx="workflows.stage1")
    p2 = _safe_join_within(comfyui.workflows_dir, names.stage2, ctx="workflows.stage2")
    p3 = _safe_join_within(comfyui.workflows_dir, names.stage3, ctx="workflows.stage3")
    for p, n in [(p1, "workflows.stage1"), (p2, "workflows.stage2"), (p3, "workflows.stage3")]:
        if not p.exists(): raise FileNotFoundError(f"{n} 檔案不存在：{p}")
        if not p.is_file(): raise FileNotFoundError(f"{n} 不是檔案：{p}")
    wf_paths = WorkflowsPaths(stage1=p1, stage2=p2, stage3=p3)

    return Config(
        schema_version=schema_version,
        comfyui=comfyui,
        paths_source_root=src,
        paths_staging_root=stg,
        paths_output_root=out,
        pipeline=pipe,
        workflows=names,
        workflow_paths=wf_paths,
    )


# ===== 便捷取值 =====
def get_settings(selectors: Iterable[str] | str,
                 *,
                 config_path: Optional[str | Path] = None,
                 as_dict: bool = True) -> dict[str, Any] | Any:
    """
    支援點號選擇器，舉例：
      - 'comfyui.port'
      - 'comfyui.workflows_dir'
      - 'workflows.stage3'（名稱）
      - 'workflow_paths.stage3'（解析過的絕對路徑）
      - 'pipeline.max_inflight'
      - 'paths_source_root'
    """
    cfg = load_config(config_path)
    blob = _to_plain(cfg)  # dataclass/Path -> dict/str

    if isinstance(selectors, str):
        val = _dot_get(blob, selectors)
        return {selectors: val} if as_dict else val

    out: dict[str, Any] = {}
    for s in selectors:
        out[s] = _dot_get(blob, s)
    return out
