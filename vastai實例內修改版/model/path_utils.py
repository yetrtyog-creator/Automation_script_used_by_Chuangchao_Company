# path_utils.py
# -*- coding: utf-8 -*-
"""
path_utils.py — 影像枚舉 / 父與祖父資料夾名稱 / 數字索引正規化 小工具集

【用途簡介】
- 影像檔枚舉：以副檔名（jpg/png/webp/bmp/tif...）掃描資料夾，支援遞迴、跟隨符號連結、自然排序。
- 路徑剖析：快速取得「父資料夾」與「祖父資料夾」名稱，方便像 `.../01/Face/img.jpg` 這類層級邏輯。
- 索引工具：從名稱萃取第一段數字（如 '01'、'12a'→1/12）、依資料夾實際命名寬度做零填充（'01','002' 等）。

【常見情境】
1) 針對資料集掃描所有影像檔，維持穩定且「人類友善」排序（img2 < img10）。
2) 從路徑還原分群索引，例如祖父層 '01'、父層 'Face'。
3) 依既有子資料夾命名（'1','02','120'）自動推斷 padding 寬度，統一輸出 '01','02','120' 等格式。

【主要 API】
- 影像判斷 / 枚舉
    - is_image_file(p) -> bool
    - iter_images(root, recursive=True, patterns=None, follow_symlinks=False, sort=True) -> Iterator[Path]
    - list_images(...) -> list[Path]

- 路徑父/祖父名稱
    - get_parent_name(p) -> Optional[str]
    - get_grandparent_name(p) -> Optional[str]
    - get_parent_and_grandparent(p) -> tuple[Optional[str], Optional[str]]

- 數字索引
    - extract_index_from_name(name) -> Optional[int]
    - normalize_index(idx, width=2) -> str
    - suggest_index_width(folder) -> int
    - normalize_index_for_folder(idx, folder) -> str
    - parent_index(p, width=None, folder_for_width=None) -> Optional[str]

- 便利範例
    - example_face_and_index(p) -> (parent, grandparent)

【快速範例】
>>> get_parent_name("big/01/Face/switch01.jpg")
'Face'
>>> get_grandparent_name("big/01/Face/switch01.jpg")
'01'
>>> list_images("big", recursive=True)[:3]
[Path('big/01/Face/a.jpg'), Path('big/01/Face/b.jpg'), ...]
>>> extract_index_from_name("folder012x")  # -> 12
12
>>> suggest_index_width("big")             # 若有 '1','02','120' -> 3
3
>>> normalize_index_for_folder(7, "big")   # 依上例 -> '007'
'007'

【邊界處理說明】
- 根路徑或層級不足：`get_parent_name` / `get_grandparent_name` 會回傳 `None`（不會回傳 '.' 或空字串）。
- 權限/IO 例外：影像枚舉遇到 OSError 會安靜跳過（回空列表/迭代器）。
- 非數字名稱：`normalize_index("abc")` 會原樣回傳 'abc'，不強制轉換。

【變更摘要（2025-09-14）】
- 修正 `get_parent_name` / `get_grandparent_name` 的條件運算與層級檢查，避免回傳 '.' 或空字串。
"""

from __future__ import annotations

from pathlib import Path, PurePath
from typing import Iterator, Optional, Sequence, Union
import re
import os

PathLike = Union[str, os.PathLike]


# -------------------------------
# Basic path-name helpers
# -------------------------------

def get_parent_name(p: PathLike) -> Optional[str]:
    """
    取得路徑上一級資料夾的名稱。
    Get the immediate parent folder name of a path.

    Returns:
        名稱字串；若無上級資料夾則回傳 None。
    """
    pp = PurePath(p)
    if len(pp.parts) <= 1:
        return None
    # 對於根目錄（如 '/' 或 'C:\\'）其 name 可能為空字串，轉為 None
    name = pp.parent.name
    return name or None


def get_grandparent_name(p: PathLike) -> Optional[str]:
    """
    取得路徑上上級（祖父）資料夾的名稱。
    Get the grandparent folder name of a path.

    Returns:
        名稱字串；若無上上級資料夾則回傳 None。
    """
    pp = PurePath(p)
    if len(pp.parts) <= 2:
        return None
    gp = pp.parent.parent
    name = gp.name
    return name or None


def get_parent_and_grandparent(p: PathLike) -> tuple[Optional[str], Optional[str]]:
    """
    同時回傳父與祖父資料夾名稱。
    Return (parent_name, grandparent_name).
    """
    return get_parent_name(p), get_grandparent_name(p)


# -------------------------------
# Image enumeration
# -------------------------------

_DEFAULT_PATTERNS: tuple[str, ...] = ("*.jpg", "*.jpeg", "*.png", "*.webp", "*.bmp", "*.tif", "*.tiff")


def is_image_file(p: PathLike) -> bool:
    """
    粗略判斷是否為常見影像檔（副檔名比對）。
    Simple extension-based image checker.
    """
    s = str(p)
    ext = s.lower().rsplit(".", 1)[-1] if "." in s else ""
    return ext in {"jpg", "jpeg", "png", "webp", "bmp", "tif", "tiff"}


def _natural_key(s: str) -> list[Union[int, str]]:
    """
    用於人類友善排序：'img2' < 'img10'。
    Natural sort key: splits digits to ints.
    """
    return [int(t) if t.isdigit() else t.lower() for t in re.findall(r"\d+|\D+", s)]


def iter_images(
    root: PathLike,
    *,
    recursive: bool = True,
    patterns: Optional[Sequence[str]] = None,
    follow_symlinks: bool = False,
    sort: bool = True,
) -> Iterator[Path]:
    """
    枚舉 root 下的影像檔。

    Args:
        root: 來源資料夾。
        recursive: 是否遞迴尋找。
        patterns: glob 樣式（預設常見影像副檔名）。
        follow_symlinks: 遞迴時是否追蹤符號連結（可能造成循環）。
        sort: 是否以自然排序回傳穩定順序。

    Yields:
        Path 物件（存在的檔案）。
    """
    root_path = Path(root)
    pats = tuple(patterns) if patterns else _DEFAULT_PATTERNS

    # 先收集，避免多個 pattern 造成重複
    found: list[Path] = []
    try:
        if recursive:
            # Path.rglob 不提供 follow_symlinks 控制；用 os.walk 支援之
            for dirpath, dirnames, filenames in os.walk(root_path, followlinks=follow_symlinks):
                d = Path(dirpath)
                for pat in pats:
                    for p in d.glob(pat):
                        if p.is_file():
                            found.append(p)
        else:
            for pat in pats:
                for p in root_path.glob(pat):
                    if p.is_file():
                        found.append(p)
    except OSError:
        # 權限/IO 問題時，不丟出；改為安靜跳過
        found = []

    # 去重並保序
    uniq = list(dict.fromkeys(found))  # preserves order

    if sort:
        # 以相對於 root 的字串做自然排序，確保穩定
        def _rel(p: Path) -> str:
            try:
                return str(p.relative_to(root_path))
            except Exception:
                return str(p)
        uniq.sort(key=lambda p: _natural_key(_rel(p)))

    for p in uniq:
        yield p


def list_images(
    root: PathLike,
    *,
    recursive: bool = True,
    patterns: Optional[Sequence[str]] = None,
    follow_symlinks: bool = False,
    sort: bool = True,
) -> list[Path]:
    """
    回傳影像清單（list 版本）。
    List variant of iter_images.
    """
    return list(
        iter_images(
            root,
            recursive=recursive,
            patterns=patterns,
            follow_symlinks=follow_symlinks,
            sort=sort,
        )
    )


# -------------------------------
# Index parsing & normalization
# -------------------------------

_NUM_RE = re.compile(r"(?P<num>\d+)")


def extract_index_from_name(name: str) -> Optional[int]:
    """
    從名稱中擷取第一段連續數字作為索引（如 '01'、'12a' -> 1/12）。
    Extract the first contiguous digit run from a string and return it as int.

    Returns:
        int 或 None（找不到數字）。
    """
    m = _NUM_RE.search(name)
    return int(m.group("num")) if m else None


def normalize_index(idx: Union[int, str], *, width: int = 2) -> str:
    """
    將索引正規化為零填充字串（預設兩位：01, 02, ...）。
    Normalize an index into a zero-padded string.

    Examples:
        normalize_index(3) -> '03'
        normalize_index("12", width=3) -> '012'
    """
    if isinstance(idx, str):
        # 若給的是 '01'、'folder02' 之類，嘗試抽出數字；若失敗則原樣回傳（但不強行 pad）
        num = extract_index_from_name(idx)
        if num is None:
            try:
                num = int(idx)
            except ValueError:
                return idx
    else:
        num = int(idx)
    return f"{num:0{width}d}"


def suggest_index_width(folder: PathLike) -> int:
    """
    根據資料夾下以『純數字名稱』的子資料夾，推測索引寬度（回傳數字字元最大長度）。
    Suggest a zero-padding width by scanning direct subfolders with purely numeric names.

    Example:
        若存在子資料夾：'1', '02', '120' -> 回傳 3
    """
    path = Path(folder)
    widths = []
    try:
        for child in path.iterdir():
            if child.is_dir() and child.name.isdigit():
                widths.append(len(child.name))
    except OSError:
        pass
    return max(widths, default=2)


def normalize_index_for_folder(idx: Union[int, str], folder: PathLike) -> str:
    """
    以資料夾情況自動推測寬度後，正規化索引。
    Normalize an index using auto-suggested width from sibling numeric folders.
    """
    width = suggest_index_width(folder)
    return normalize_index(idx, width=width)


def parent_index(p: PathLike, *, width: Optional[int] = None, folder_for_width: Optional[PathLike] = None) -> Optional[str]:
    """
    擷取『父資料夾』中的數字索引並（可選）正規化。
    Extract numeric index from the parent folder name and optionally normalize.

    Args:
        width: 若提供則以此寬度零填充；不提供則不填充（原始數字）。
        folder_for_width: 若提供，會以此資料夾推測寬度（當 width 未指定時）。

    Returns:
        正規化後的字串或 None（父名非數字或不存在）。
    """
    name = get_parent_name(p)
    if not name:
        return None
    num = extract_index_from_name(name)
    if num is None:
        return None
    if width is not None:
        return normalize_index(num, width=width)
    if folder_for_width is not None:
        w = suggest_index_width(folder_for_width)
        return normalize_index(num, width=w)
    return str(num)


# -------------------------------
# Convenience example for your case
# -------------------------------

def example_face_and_index(p: PathLike) -> tuple[Optional[str], Optional[str]]:
    """
    針對像『大資料夾/01/Face/switch01.jpg』這類路徑，
    回傳 (父='Face', 祖父='01')。

    Returns:
        (parent_name, grandparent_name)
    """
    return get_parent_and_grandparent(p)
