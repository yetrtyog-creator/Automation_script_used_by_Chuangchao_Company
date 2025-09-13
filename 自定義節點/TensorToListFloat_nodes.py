# ComfyUI custom node: Tensor512ToFloatList
# - Input:  any (同 Debug Tensor Shape 的綠色任意型別)
# - Output: (any 一維 list[float], STRING JSON)
# 放置路徑：ComfyUI/custom_nodes/tensor_512_to_list/nodes.py

import json
import numpy as np

try:
    import torch
except ImportError:
    torch = None

# 盡量與 comfyui_essentials 一致：使用 AnyType("*") 產生綠色插孔
try:
    from ComfyUI_essentials.utils import AnyType  # 與 Debug Tensor Shape 相同來源
    any = AnyType("*")
except Exception:
    # 後備：若沒裝 essentials，也能運作
    class AnyType(str):
        def __ne__(self, other):  # 與 essentials 的 AnyType 行為一致
            return False
    any = AnyType("*")

class Tensor512ToFloatList:
    """將 [[1,512]] 或任意張量/陣列擷取為 512 維一維 list[float]。"""

    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                "tensor": (any, {}),  # 綠色「任意型別」輸入，與 Debug Tensor Shape 相同
            },
        }

    RETURN_TYPES = (any, "STRING")      # (list[float], json)
    RETURN_NAMES = ("float_list", "json")
    FUNCTION = "to_list"
    CATEGORY = "utils/tensor"

    def _first_array_like(self, x):
        """從巢狀 dict/list 中找出第一個可轉成 torch/numpy 的陣列。"""
        # dict：遞迴 values
        if isinstance(x, dict):
            for v in x.values():
                t = self._first_array_like(v)
                if t is not None:
                    return t
            return None
        # list/tuple：遞迴元素
        if isinstance(x, (list, tuple)):
            for v in x:
                t = self._first_array_like(v)
                if t is not None:
                    return t
            return None
        # numpy ndarray
        if isinstance(x, np.ndarray):
            return x
        # torch Tensor
        if torch is not None and "torch" in str(type(x)).lower() and hasattr(x, "shape"):
            return x
        # 其他型別
        return None

    def _to_torch(self, arr):
        if torch is None:
            raise RuntimeError("PyTorch 未安裝，無法處理張量。")
        if isinstance(arr, np.ndarray):
            return torch.from_numpy(arr)
        return arr  # 已是 torch.Tensor

    def _as_512_vector(self, t):
        # 先確保在 CPU、float
        if hasattr(t, "detach"):
            t = t.detach()
        if hasattr(t, "cpu"):
            t = t.cpu()
        # 嘗試處理典型形狀
        # Case A: [..., 512] -> 取第 0 列（若有 batch 維）
        if hasattr(t, "shape") and len(t.shape) >= 1:
            if t.ndim == 2 and t.shape[1] >= 512:
                t = t[0, :512]
            elif t.ndim == 1 and t.numel() >= 512:
                t = t[:512]
            else:
                # 其他情況：直接攤平成一維再取前 512
                t = t.reshape(-1)
                if t.numel() < 512:
                    # 若長度不足，直接按現有長度輸出（不強制補零）
                    return t.float()
                t = t[:512]
        return t.float()

    def to_list(self, tensor):
        arr = self._first_array_like(tensor)
        if arr is None:
            # 允許直接輸入 list[float] 的情況
            if isinstance(tensor, list) and all(isinstance(v, (int, float)) for v in tensor):
                floats = [float(v) for v in tensor[:512]]
                return (floats, json.dumps(floats, ensure_ascii=False))
            raise ValueError("無法在輸入中找到可解析的張量/陣列。")

        t = self._to_torch(arr)
        vec = self._as_512_vector(t)
        floats = [float(v) for v in vec.flatten().tolist()]
        return (floats, json.dumps(floats, ensure_ascii=False))

# ComfyUI 掃描需要的對應表
NODE_CLASS_MAPPINGS = {
    "Tensor512ToFloatList": Tensor512ToFloatList,
}
NODE_DISPLAY_NAME_MAPPINGS = {
    "Tensor512ToFloatList": "Tensor → 512 list(float)",
}
