#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations
import re
from pathlib import Path
from typing import List, Dict, Tuple

# 支援的影像副檔名
IMG_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tiff"}

# 批次資料夾名稱：純數字（例如 "01", "002", "12"）
NUM_DIR_REGEX = re.compile(r"^\d+$")


class RuleError(Exception):
    """來源/目錄規則檢查錯誤"""
    pass


def is_numeric_name(name: str) -> bool:
    return bool(NUM_DIR_REGEX.match(name))


def list_numeric_batches(root: Path) -> List[Path]:
    """列出 root 下所有數字命名的子資料夾（已排序）"""
    root = Path(root)
    if not root.exists():
        raise RuleError(f"來源資料夾不存在：{root}")
    if not root.is_dir():
        raise RuleError(f"來源路徑不是資料夾：{root}")
    return sorted([p for p in root.iterdir() if p.is_dir() and is_numeric_name(p.name)])


def list_images(folder: Path, recursive: bool = True) -> List[Path]:
    """列出資料夾內所有圖片檔；recursive=True 時包含子資料夾"""
    folder = Path(folder)
    if not folder.exists() or not folder.is_dir():
        return []
    it = folder.rglob("*") if recursive else folder.glob("*")
    out: List[Path] = []
    for p in it:
        if p.is_file() and p.suffix.lower() in IMG_EXTS:
            out.append(p)
    return sorted(out)


def count_images(folder: Path, recursive: bool = True) -> int:
    return len(list_images(folder, recursive=recursive))


def _has_image_anywhere(p: Path) -> bool:
    try:
        return count_images(p, recursive=True) > 0
    except Exception:
        return False


def ensure_source_layout(root: Path) -> List[Path]:
    """
    檢查來源層級：root/NN/{Target,Face} 且兩者皆至少有一張圖片（允許遞迴檢查）
    回傳批次資料夾清單（Path）
    """
    batches = list_numeric_batches(root)
    if not batches:
        raise RuleError(f"來源下未找到數字命名子資料夾：{root}")
    for b in batches:
        target = b / "Target"
        face = b / "Face"
        if not target.exists() or not target.is_dir():
            raise RuleError(f"缺少 Target 資料夾：{target}")
        if not face.exists() or not face.is_dir():
            raise RuleError(f"缺少 Face 資料夾：{face}")
        if not _has_image_anywhere(target):
            raise RuleError(f"Target 無任何圖片：{target}")
        if not _has_image_anywhere(face):
            raise RuleError(f"Face 無任何圖片：{face}")
    return batches


def prepare_staging_dirs(
    staging_root: Path,
    batch_names: List[str],
    subfolders: Tuple[str, ...] = ("Target", "Face"),
) -> Dict[str, Dict[str, Path]]:
    """
    建立中繼（staging）目錄結構：
      staging_root/<batch>/<subfolder>/
    預設 subfolders = ("Target", "Face")
    回傳：
      { "01": {"Target": Path(...), "Face": Path(...)}, ... }
    """
    staging_root = Path(staging_root)
    staging_root.mkdir(parents=True, exist_ok=True)

    result: Dict[str, Dict[str, Path]] = {}
    for name in batch_names:
        batch_dir = staging_root / name
        batch_dir.mkdir(parents=True, exist_ok=True)
        result[name] = {}
        for sub in subfolders:
            d = batch_dir / sub
            d.mkdir(parents=True, exist_ok=True)
            result[name][sub] = d
    return result


def ensure_staging_layout(
    staging_root: Path,
    subfolders: Tuple[str, ...] = ("Target", "Face"),
    require_images: bool = False,
) -> List[Path]:
    """
    檢查 staging 層級：staging_root/NN/<subfolders...>
    - 預設不強制要求各資料夾已有圖片（require_images=False），若要更嚴格可設 True
    回傳批次資料夾清單（Path）
    """
    staging_root = Path(staging_root)
    batches = list_numeric_batches(staging_root)
    if not batches:
        raise RuleError(f"staging 下未找到數字命名子資料夾：{staging_root}")
    for b in batches:
        for sub in subfolders:
            d = b / sub
            if not d.exists() or not d.is_dir():
                raise RuleError(f"缺少 staging 子資料夾：{d}")
            if require_images and not _has_image_anywhere(d):
                raise RuleError(f"staging/{b.name}/{sub} 無任何圖片：{d}")
    return batches


def first_image(p: Path) -> Path:
    """
    回傳資料夾中的第一張圖片（以檔名排序）。
    用於某些僅需「至少一張圖」的檢核或舊版 fallback。
    """
    p = Path(p)
    if not p.exists() or not p.is_dir():
        raise RuleError(f"路徑不是資料夾：{p}")
    # 先快速以副檔名掃描
    for ext in sorted(IMG_EXTS):
        imgs = sorted(p.glob(f"*{ext}"))
        if imgs:
            return imgs[0]
    # fallback：全掃
    for img in sorted(p.iterdir()):
        if img.is_file() and img.suffix.lower() in IMG_EXTS:
            return img
    raise RuleError(f"找不到圖片於：{p}")


__all__ = [
    "RuleError",
    "is_numeric_name",
    "list_numeric_batches",
    "list_images",
    "count_images",
    "ensure_source_layout",
    "prepare_staging_dirs",
    "ensure_staging_layout",
    "first_image",
]
