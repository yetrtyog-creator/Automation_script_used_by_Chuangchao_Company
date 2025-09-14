# dir_schema.py
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

匯出（__all__）
----------------
- 例外類別：
  - `SchemaError`：通用結構檢查錯誤基底類別
  - `NoValidBatchFoundError`：來源沒有任何合規的數字批次資料夾
  - `InvalidChildNameError`：來源包含非 1~4 位數字命名的子資料夾
  - `MissingRequiredFolderError`：某批次缺少 Target 或 Face
  - `MissingImagesError`：Target/Face 子資料夾內未檢出任何允許圖片
- 函式：
  - `check_source_schema(...) -> list[str]`
      嚴格檢查來源資料夾結構，必要時同時檢查是否含有圖片。
      成功回傳「排序後」的批次名稱列表（字串）；否則拋出對應例外。
  - `mirror_to_staging(...) -> dict[str, dict[str, Path]]`
      於暫存根目錄鏡像建立 `<批次>/Target` 與 `<批次>/Face` 空資料夾骨架（不複製檔案）。
      可選擇建立前先做嚴格檢查。
  - `mirror_batches_only(...) -> dict[str, Path]`
      僅鏡像建立數字批次層級 `<批次>`（不建立 Target/Face、不複製檔案）。
      可選擇建立前先做嚴格檢查。

參數重點
--------
- `require_images`（bool, 預設 True）：
    `check_source_schema()` 在驗證 Target/Face 時，是否要求至少一張圖片。
- `allowed_image_exts`（Iterable[str]）：
    認定為圖片的副檔名清單，大小寫不敏感（例如 "JPG" 也會被視為 "jpg"）。
- `recursive_image_search`（bool, 預設 False）：
    是否遞迴搜尋圖片（False 則只檢查直屬檔案）。
- `case_insensitive_required_folders`（bool, 預設 False）：
    是否以大小寫不敏感的方式尋找 Target/Face。
- `ensure_valid`（bool, 預設 True）：
    在鏡像建立前是否先呼叫 `check_source_schema()` 做嚴格驗證
    （注意：`mirror_batches_only()` 的嚴格驗證也會連同圖片條件一併檢查）。

回傳型別
--------
- `check_source_schema(...) -> list[str]`
    合規的批次資料夾名稱列表（字串），例如 `["01", "02", "12"]`。
- `mirror_to_staging(...) -> dict[str, dict[str, Path]]`
    例如：`{"01": {"root": Path(.../01), "Target": Path(.../01/Target), "Face": Path(.../01/Face)}, ...}`
- `mirror_batches_only(...) -> dict[str, Path]`
    例如：`{"01": Path(.../01), "02": Path(.../02)}`

常見例外
--------
- `SchemaError`：來源不是資料夾或其它通用錯誤。
- `NoValidBatchFoundError`：來源根目錄下找不到任何 1~4 位純數字批次資料夾。
- `InvalidChildNameError`：來源根目錄下存在非數字命名子資料夾。
- `MissingRequiredFolderError`：某批次缺少 Target 或 Face。
- `MissingImagesError`：在要求檢查圖片時，某批次的 Target/Face 找不到任何允許副檔名圖片。

簡易示例
--------
>>> from pathlib import Path
>>> # 嚴格檢查（含圖片）
>>> batches = check_source_schema("/data/source", require_images=True)
>>> # 鏡像建立到暫存（建立 <批次>/Target 與 <批次>/Face 兩層，僅資料夾）
>>> layout = mirror_to_staging("/data/source", "/data/staging", ensure_valid=True)
>>> # 僅鏡像批次層級（不建 Target/Face）
>>> batches_map = mirror_batches_only("/data/source", "/data/staging_only", ensure_valid=False)

平台差異
--------
- Windows 預設檔案系統大小寫不敏感；但本模組仍預設「大小寫敏感」尋找 Target/Face，
  以避免跨平台時產生歧義。若需要不敏感搜尋，請將 `case_insensitive_required_folders=True`。

效能提示
--------
- 大型資料樹若啟用 `recursive_image_search=True` 會增加 I/O；只在必要時開啟。
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

# ===== 1) 檢查函數 =====
def check_source_schema(
    source_root: str | Path,
    *,
    require_images: bool = True,
    allowed_image_exts: Iterable[str] = ("jpg", "jpeg", "png", "webp"),
    recursive_image_search: bool = False,
    case_insensitive_required_folders: bool = False,
) -> list[str]:
    """
    檢查來源資料夾是否符合規範：
      - 來源下一級（直屬）子資料夾皆須為 1~4 位純數字（例如 01、001、0001、12、999；不允許 0000）。
      - 每個數字批次資料夾的下一級需存在 Target 與 Face 兩個子資料夾（預設大小寫敏感）。
      - 若 require_images=True，則 Target/Face 內各自至少需有一張允許副檔名圖片（預設不遞迴）。

    成功則回傳排序後的批次名稱列表；任何不合規會拋出對應例外。
    """
    src = Path(source_root)
    if not src.exists() or not src.is_dir():
        raise SchemaError(f"來源不存在或不是資料夾：{src}")

    child_dirs = _list_immediate_dirs(src)
    numeric_batches = [d for d in child_dirs if _is_numeric_batch(d.name)]
    non_numeric = [d for d in child_dirs if not _is_numeric_batch(d.name)]

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
) -> dict[str, dict[str, Path]]:
    """
    於 staging_root 下鏡像建立來源的數字批次目錄：
      <staging_root>/<批次>/Target
      <staging_root>/<批次>/Face
    - 僅建立資料夾，不複製任何檔案。
    - ensure_valid=True 時會先呼叫 check_source_schema() 進行嚴格檢查。

    回傳：
      { "<批次>": {"root": Path, "Target": Path, "Face": Path}, ... }
    """
    src = Path(source_root)
    dst = Path(staging_root)

    if not src.exists() or not src.is_dir():
        raise SchemaError(f"來源不存在或不是資料夾：{src}")

    if ensure_valid:
        batches = check_source_schema(src)
    else:
        batches = sorted(
            [p.name for p in _list_immediate_dirs(src) if _is_numeric_batch(p.name)]
        )
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
) -> dict[str, Path]:
    """
    僅在 staging_root 下鏡像建立「數字批次」資料夾層級：
      <staging_root>/<批次>
    - 不建立 Target/Face，也不複製任何檔案。
    - ensure_valid=True 時仍會以 check_source_schema() 嚴格驗證來源結構；
      若只想驗證「有合法數字批次」而不檢查 Target/Face，可將 ensure_valid=False。

    回傳：
      { "<批次>": Path, ... }
    """
    src = Path(source_root)
    dst = Path(staging_root)

    if not src.exists() or not src.is_dir():
        raise SchemaError(f"來源不存在或不是資料夾：{src}")

    if ensure_valid:
        batches = check_source_schema(src)  # 嚴格：含 Target/Face(+可選圖片) 檢查
    else:
        batches = sorted(
            [p.name for p in _list_immediate_dirs(src) if _is_numeric_batch(p.name)]
        )
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
