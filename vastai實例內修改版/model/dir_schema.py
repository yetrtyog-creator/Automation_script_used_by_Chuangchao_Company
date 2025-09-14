# -*- coding: utf-8 -*-
"""
dir_schema.py — 來源/暫存資料夾結構檢查與鏡像建立工具（繁體中文說明）

功能總覽
--------
本模組協助你的管線在「第一階段/第二階段」開始之前，先對檔案系統做結構健檢，並在暫存區建立
與來源相同的「數字批次資料夾」骨架（可選擇是否同時建立 Target/Face 兩個子資料夾），避免
後續工作流因資料夾缺漏而失敗。

核心規範（預設）
----------------
1) 來源根目錄下的直屬子資料夾，**必須**全部符合「1~4 位純數字」命名（允許前導 0，但不可全 0，
   例如：01、001、0001、12、999；不允許 0000）。
2) 每個數字批次資料夾底下，**必須**存在名稱為 **Target** 與 **Face** 的子資料夾（預設大小寫敏感，
   可切換成大小寫不敏感）。
3) 若 `require_images=True`，則 Target 與 Face 內各自需要至少一張允許的圖片（副檔名預設：
   jpg/jpeg/png/webp；可關閉遞迴搜尋或開啟 `recursive_image_search=True`）。

本版新增
--------
- `ignore_hidden=True`：自動忽略以「.」開頭的隱藏資料夾（如 `.ipynb_checkpoints`）。
- `extra_ignores=('.ipynb_checkpoints', '.git', '__pycache__')`：可擴充忽略白名單。
- 鏡像函式 `mirror_to_staging`、`mirror_batches_only` 同步支援上述忽略規則。

匯出（__all__）
----------------
- 例外類別：
  - `SchemaError`
  - `NoValidBatchFoundError`
  - `InvalidChildNameError`
  - `MissingRequiredFolderError`
  - `MissingImagesError`
- 函式：
  - `check_source_schema(...) -> list[str]`
  - `mirror_to_staging(...) -> dict[str, dict[str, Path]]`
  - `mirror_batches_only(...) -> dict[str, Path]`
"""

from __future__ import annotations

from pathlib import Path
import re
from typing import Iterable

# ===== 自訂例外 =====
class SchemaError(Exception):
    """來源/暫存結構檢查通用錯誤。"""

class NoValidBatchFoundError(SchemaError):
    """來源根目錄下沒有任何合法的數字批次資料夾。"""

class InvalidChildNameError(SchemaError):
    """來源根目錄下存在非 1~4 位數字命名的子資料夾。"""

class MissingRequiredFolderError(SchemaError):
    """某批次缺少 Target 或 Face 子資料夾。"""

class MissingImagesError(SchemaError):
    """Target/Face 中缺少至少一張允許的圖片。"""

# ===== 內部工具 =====
_NUMERIC_RE = re.compile(r"^(?!0+$)\d{1,4}$")  # 1~4 位數字且不可全為 0

def _is_numeric_batch(name: str) -> bool:
    return bool(_NUMERIC_RE.match(name))

def _list_immediate_dirs(root: Path) -> list[Path]:
    return [p for p in root.iterdir() if p.is_dir()]

def _contains_image(folder: Path, exts: Iterable[str], recursive: bool = False) -> bool:
    if not folder.is_dir():
        return False
    norm = {e.lower().lstrip(".") for e in exts}
    it = folder.rglob("*") if recursive else folder.iterdir()
    for p in it:
        if p.is_file() and p.suffix.lower().lstrip(".") in norm:
            return True
    return False

def _find_child_dir(parent: Path, name: str, case_insensitive: bool) -> Path | None:
    if not parent.is_dir():
        return None
    if not case_insensitive:
        p = parent / name
        return p if p.is_dir() else None
    lname = name.lower()
    for p in parent.iterdir():
        if p.is_dir() and p.name.lower() == lname:
            return p
    return None

def _filter_children(children: list[Path], ignore_hidden: bool, extra_ignores: set[str]) -> list[Path]:
    out = []
    for d in children:
        name = d.name
        if ignore_hidden and name.startswith("."):
            continue
        if name in extra_ignores:
            continue
        out.append(d)
    return out

# ===== 1) 檢查函數 =====
def check_source_schema(
    source_root: str | Path,
    *,
    require_images: bool = True,
    allowed_image_exts: Iterable[str] = ("jpg", "jpeg", "png", "webp"),
    recursive_image_search: bool = False,
    case_insensitive_required_folders: bool = False,
    ignore_hidden: bool = True,
    extra_ignores: Iterable[str] = (".ipynb_checkpoints", ".git", "__pycache__"),
) -> list[str]:
    """
    檢查來源資料夾是否符合規範：
      - 來源下一級（直屬）子資料夾皆須為 1~4 位純數字（例如 01、001、0001、12、999；不允許 0000）。
      - 每個數字批次資料夾的下一級需存在 Target 與 Face 兩個子資料夾（預設大小寫敏感）。
      - 若 require_images=True，則 Target/Face 內各自至少需有一張允許副檔名圖片（預設不遞迴）。
      - 允許忽略隱藏與白名單資料夾。
    成功則回傳排序後的批次名稱列表；任何不合規會拋出對應例外。
    """
    src = Path(source_root)
    if not src.exists() or not src.is_dir():
        raise SchemaError(f"來源不存在或不是資料夾：{src}")

    extra_ignores_set = set(extra_ignores)

    # 先列直屬資料夾並套用忽略規則
    raw_children = _list_immediate_dirs(src)
    children = _filter_children(raw_children, ignore_hidden=ignore_hidden, extra_ignores=extra_ignores_set)

    numeric_batches = [d for d in children if _is_numeric_batch(d.name)]
    non_numeric = [d for d in children if not _is_numeric_batch(d.name)]

    if not numeric_batches:
        raise NoValidBatchFoundError(f"找不到任何 1~4 位純數字的批次資料夾於：{src}")

    if non_numeric:
        bad = ", ".join(sorted(d.name for d in non_numeric))
        raise InvalidChildNameError(
            f"來源下存在非數字命名子資料夾：{bad}；要求 1~4 位數字（例如 01/001/0001/12/999）。"
        )

    batch_names: list[str] = []
    for d in sorted(numeric_batches, key=lambda p: p.name):
        t_dir = _find_child_dir(d, "Target", case_insensitive_required_folders)
        f_dir = _find_child_dir(d, "Face", case_insensitive_required_folders)
        missing = []
        if t_dir is None: missing.append("Target")
        if f_dir is None: missing.append("Face")
        if missing:
            raise MissingRequiredFolderError(
                f"批次 {d.name} 缺少必需子資料夾：{', '.join(missing)}；位置：{d}"
            )

        if require_images:
            if not _contains_image(t_dir, allowed_image_exts, recursive_image_search):
                exts = ", ".join(allowed_image_exts)
                raise MissingImagesError(
                    f"批次 {d.name} 的 Target 中沒有任何圖片（允許：{exts}）；位置：{t_dir}"
                )
            if not _contains_image(f_dir, allowed_image_exts, recursive_image_search):
                exts = ", ".join(allowed_image_exts)
                raise MissingImagesError(
                    f"批次 {d.name} 的 Face 中沒有任何圖片（允許：{exts}）；位置：{f_dir}"
                )

        batch_names.append(d.name)

    return batch_names

# ===== 2) 鏡像建立（建立批次 + 空 Target/Face；不複製檔案） =====
def mirror_to_staging(
    source_root: str | Path,
    staging_root: str | Path,
    *,
    ensure_valid: bool = True,
    create_children: tuple[str, str] = ("Target", "Face"),
    exist_ok: bool = True,
    # 同步忽略規則
    ignore_hidden: bool = True,
    extra_ignores: Iterable[str] = (".ipynb_checkpoints", ".git", "__pycache__"),
) -> dict[str, dict[str, Path]]:
    """
    於 staging_root 下鏡像建立來源的數字批次目錄：
      <staging_root>/<批次>/Target
      <staging_root>/<批次>/Face
    - 僅建立資料夾，不複製任何檔案。
    - ensure_valid=True 時會先呼叫 check_source_schema() 進行嚴格檢查。
    - 忽略規則與 check_source_schema 相同。

    回傳：
      { "<批次>": {"root": Path, "Target": Path, "Face": Path}, ... }
    """
    src = Path(source_root)
    dst = Path(staging_root)

    if not src.exists() or not src.is_dir():
        raise SchemaError(f"來源不存在或不是資料夾：{src}")

    extra_ignores_set = set(extra_ignores)

    if ensure_valid:
        batches = check_source_schema(
            src,
            ignore_hidden=ignore_hidden,
            extra_ignores=extra_ignores_set,
        )
    else:
        raw_children = _list_immediate_dirs(src)
        children = _filter_children(raw_children, ignore_hidden=ignore_hidden, extra_ignores=extra_ignores_set)
        batches = sorted([p.name for p in children if _is_numeric_batch(p.name)])
        if not batches:
            raise NoValidBatchFoundError(f"找不到任何 1~4 位純數字的批次資料夾於：{src}")

    dst.mkdir(parents=True, exist_ok=True)

    result: dict[str, dict[str, Path]] = {}
    for name in batches:
        batch_root = dst / name
        if batch_root.exists() and not batch_root.is_dir():
            raise SchemaError(f"目的地同名檔案阻擋建立資料夾：{batch_root}")
        batch_root.mkdir(exist_ok=exist_ok)
        mapping = {"root": batch_root}

        for child in create_children:
            cdir = batch_root / child
            if cdir.exists() and not cdir.is_dir():
                raise SchemaError(f"目的地下層同名檔案阻擋建立資料夾：{cdir}")
            cdir.mkdir(exist_ok=exist_ok)
            mapping[child] = cdir

        result[name] = mapping

    return result

# ===== 3) 只鏡像批次層級（不建立 Target/Face；不複製檔案） =====
def mirror_batches_only(
    source_root: str | Path,
    staging_root: str | Path,
    *,
    ensure_valid: bool = True,
    exist_ok: bool = True,
    # 同步忽略規則
    ignore_hidden: bool = True,
    extra_ignores: Iterable[str] = (".ipynb_checkpoints", ".git", "__pycache__"),
) -> dict[str, Path]:
    """
    僅在 staging_root 下鏡像建立「數字批次」資料夾層級：
      <staging_root>/<批次>
    - 不建立 Target/Face，也不複製任何檔案。
    - ensure_valid=True 時仍會以 check_source_schema() 嚴格驗證來源結構；
      若只想驗證「有合法數字批次」而不檢查 Target/Face，可將 ensure_valid=False。
    - 忽略規則與 check_source_schema 相同。

    回傳：
      { "<批次>": Path, ... }
    """
    src = Path(source_root)
    dst = Path(staging_root)

    if not src.exists() or not src.is_dir():
        raise SchemaError(f"來源不存在或不是資料夾：{src}")

    extra_ignores_set = set(extra_ignores)

    if ensure_valid:
        batches = check_source_schema(
            src,
            ignore_hidden=ignore_hidden,
            extra_ignores=extra_ignores_set,
        )
    else:
        raw_children = _list_immediate_dirs(src)
        children = _filter_children(raw_children, ignore_hidden=ignore_hidden, extra_ignores=extra_ignores_set)
        batches = sorted([p.name for p in children if _is_numeric_batch(p.name)])
        if not batches:
            raise NoValidBatchFoundError(f"找不到任何 1~4 位純數字的批次資料夾於：{src}")

    dst.mkdir(parents=True, exist_ok=True)

    result: dict[str, Path] = {}
    for name in batches:
        batch_root = dst / name
        if batch_root.exists() and not batch_root.is_dir():
            raise SchemaError(f"目的地同名檔案阻擋建立資料夾：{batch_root}")
        batch_root.mkdir(exist_ok=exist_ok)
        result[name] = batch_root

    return result

__all__ = [
    "SchemaError",
    "NoValidBatchFoundError",
    "InvalidChildNameError",
    "MissingRequiredFolderError",
    "MissingImagesError",
    "check_source_schema",
    "mirror_to_staging",
    "mirror_batches_only",
]
