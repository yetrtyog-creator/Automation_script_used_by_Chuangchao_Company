#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations
import json, sys
from dataclasses import dataclass
from pathlib import Path

try:
    import yaml  # type: ignore
except Exception:
    yaml = None  # optional

@dataclass
class ComfyConfig:
    dir: str
    port: int = 8199
    host: str = "127.0.0.1"
    start_args: str = "--disable-auto-launch --enable-cors-header"

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

@dataclass
class PathsConfig:
    source_root: str
    staging_root: str
    output_root: str

@dataclass
class PipelineConfig:
    max_inflight: int = 4
    max_retries: int = 2
    poll_interval_sec: float = 1.0

@dataclass
class StageMapping:
    file: str
    mappings: dict  # free-form node-id -> input-key or semantic alias map
    extras: dict | None = None  # e.g. {"collection_name_prefix": "Face_Changing"}

@dataclass
class RootConfig:
    comfyui: ComfyConfig
    paths: PathsConfig
    pipeline: PipelineConfig
    workflows: dict  # {"stage1": StageMapping, "stage2": StageMapping, "stage3": StageMapping}

def _to_obj(d: dict) -> RootConfig:
    comfyui = ComfyConfig(**d["comfyui"])
    paths = PathsConfig(**d["paths"])
    pipeline = PipelineConfig(**d["pipeline"])
    wf = {}
    for k, v in d["workflows"].items():
        wf[k] = StageMapping(file=v["file"], mappings=v.get("mappings", {}), extras=v.get("extras"))
    return RootConfig(comfyui=comfyui, paths=paths, pipeline=pipeline, workflows=wf)

def load_config(path: str | Path) -> RootConfig:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"設定檔不存在：{p}")
    text = p.read_text(encoding="utf-8")
    # Try YAML first if available, fall back to JSON
    if yaml is not None:
        try:
            data = yaml.safe_load(text)
            return _to_obj(data)
        except Exception as e:
            raise RuntimeError(f"解析 YAML 失敗：{e}")
    else:
        try:
            data = json.loads(text)
            return _to_obj(data)
        except Exception as e:
            raise RuntimeError("未安裝 PyYAML，且 JSON 解析失敗；請改用 JSON 格式或安裝 pyyaml：pip install pyyaml") from e
