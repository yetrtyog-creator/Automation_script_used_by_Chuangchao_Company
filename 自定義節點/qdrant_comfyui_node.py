# qdrant_comfyui_node.py
# --------------------------------------------------------------
# ComfyUI Custom Nodes for Qdrant (Embedded, 100% Local)
# (Update 3) Add path metadata passthrough to payload & outputs.
# - New optional inputs (STRING): file_name, parent_dir, full_path
#   in UpsertOne / UpdatePayload; UpsertBatchNPZ gains paths_json.
# - When provided, these are merged into each point's payload.
# - Search/Retrieve results now echo these fields at top-level for
#   convenience (and still include full payload when requested).
# - Backward compatible: if you don't connect these inputs, behavior
#   is unchanged.
# --------------------------------------------------------------

import json
import os
import uuid
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

try:
    import torch
except Exception:
    torch = None

from qdrant_client import QdrantClient, models


_DISTANCE_MAP = {
    "COSINE": models.Distance.COSINE,
    "EUCLID": models.Distance.EUCLID,
    "DOT": models.Distance.DOT,
}

_CLIENTS: Dict[str, QdrantClient] = {}


def _get_client(db_path: str) -> QdrantClient:
    path = os.path.abspath(db_path)
    os.makedirs(path, exist_ok=True)
    if path not in _CLIENTS:
        _CLIENTS[path] = QdrantClient(path=path)
    return _CLIENTS[path]


def _parse_vector_any(v: Any, expected_dim: Optional[int] = None) -> List[float]:
    if v is None:
        raise ValueError("Vector is None")

    if torch is not None and isinstance(v, torch.Tensor):
        arr = v.detach().cpu().float().numpy().reshape(-1)
        return arr.astype(np.float32).tolist()

    if isinstance(v, np.ndarray):
        return v.astype(np.float32).reshape(-1).tolist()

    if isinstance(v, (list, tuple)):
        return np.asarray(v, dtype=np.float32).reshape(-1).tolist()

    if isinstance(v, str):
        s = v.strip()
        if s.startswith('[') and s.endswith(']'):
            data = json.loads(s)
            return np.asarray(data, dtype=np.float32).reshape(-1).tolist()
        if ',' in s:
            parts = [float(x.strip()) for x in s.split(',') if x.strip() != '']
            return np.asarray(parts, dtype=np.float32).reshape(-1).tolist()
        try:
            val = float(s)
            if expected_dim is None:
                return [val]
            return [val] * int(expected_dim)
        except Exception:
            pass

    raise ValueError("Unsupported vector format. Provide JSON list, comma-separated floats, list/array/tensor.")


def _parse_ids_any(ids_text: str) -> List[Any]:
    if ids_text is None:
        raise ValueError("ids_text is None")
    s = ids_text.strip()
    if s.startswith('[') and s.endswith(']'):
        data = json.loads(s)
        if isinstance(data, list):
            return data
    if ',' in s:
        parts = [x.strip() for x in s.split(',') if x.strip() != '']
        out = []
        for p in parts:
            try:
                out.append(int(p))
            except Exception:
                out.append(p)
        return out
    try:
        return [int(s)]
    except Exception:
        return [s]


def _coerce_point_id(pid: Any) -> Any:
    """
    Qdrant accepts integer IDs or UUID strings.
    - If pid can be int -> return int
    - If pid is valid UUID string -> return the same string
    - Else -> convert to deterministic UUID5 (NAMESPACE_DNS) from the string
    """
    # already int-like?
    try:
        if isinstance(pid, (int, np.integer)):
            return int(pid)
        # try to cast numeric strings to int
        if isinstance(pid, str) and pid.isdigit():
            return int(pid)
    except Exception:
        pass

    # try uuid
    try:
        u = uuid.UUID(str(pid))
        return str(u)
    except Exception:
        # deterministic UUID from string
        u = uuid.uuid5(uuid.NAMESPACE_URL, str(pid))
        return str(u)


def _maybe_json_payload(payload_text: str) -> Optional[Dict[str, Any]]:
    if payload_text is None or payload_text.strip() == "":
        return None
    try:
        data = json.loads(payload_text)
        if isinstance(data, dict):
            return data
        return {"value": data}
    except Exception:
        return {"text": payload_text}


def _merge_path_fields(payload: Optional[Dict[str, Any]], file_name: str = "", parent_dir: str = "", full_path: str = "") -> Dict[str, Any]:
    """Return new dict with provided path fields injected (overrides existing keys if non-empty)."""
    out = dict(payload or {})
    if isinstance(file_name, str) and file_name:
        out["file_name"] = file_name
    if isinstance(parent_dir, str) and parent_dir:
        out["parent_dir"] = parent_dir
    if isinstance(full_path, str) and full_path:
        out["full_path"] = full_path
    return out


def _extract_path_fields(payload: Optional[Dict[str, Any]]) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    if not isinstance(payload, dict):
        return None, None, None
    return payload.get("file_name"), payload.get("parent_dir"), payload.get("full_path")


def _result_json(ok: bool, **kwargs) -> Tuple[str]:
    obj = {"ok": ok}
    obj.update(kwargs)
    return (json.dumps(obj, ensure_ascii=False),)


class QdrantEnsureCollection:
    CATEGORY = "Qdrant / Local"
    FUNCTION = "run"
    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("result_json",)
    OUTPUT_IS_LIST = (False,)

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "db_path": ("STRING", {"default": r"./qdrant_data"}),
                "collection_name": ("STRING", {"default": "bodies"}),
                "vector_dim": ("INT", {"default": 1024, "min": 1, "max": 65536}),
                "distance": (["COSINE", "EUCLID", "DOT"], {"default": "COSINE"}),
                "recreate": ("BOOLEAN", {"default": False}),
            },
            "optional": {
                "hnsw_m": ("INT", {"default": 32, "min": 4, "max": 128}),
                "hnsw_ef_construct": ("INT", {"default": 128, "min": 16, "max": 4096}),
                "full_scan_threshold": ("INT", {"default": 10000, "min": 0, "max": 100000000}),
            }
        }

    def run(self, db_path: str, collection_name: str, vector_dim: int,
            distance: str, recreate: bool, hnsw_m: int = 32, hnsw_ef_construct: int = 128,
            full_scan_threshold: int = 10000):
        client = _get_client(db_path)
        vec_cfg = models.VectorParams(size=int(vector_dim), distance=_DISTANCE_MAP[distance])
        hcfg = models.HnswConfig(m=hnsw_m, ef_construct=hnsw_ef_construct, full_scan_threshold=full_scan_threshold)

        try:
            if recreate:
                client.recreate_collection(
                    collection_name=collection_name,
                    vectors_config=vec_cfg,
                    hnsw_config=hcfg,
                )
                return _result_json(True, action="recreate", collection=collection_name)
            else:
                try:
                    client.get_collection(collection_name)
                    return _result_json(True, action="exists", collection=collection_name)
                except Exception:
                    client.create_collection(
                        collection_name=collection_name,
                        vectors_config=vec_cfg,
                        hnsw_config=hcfg,
                    )
                    return _result_json(True, action="create", collection=collection_name)
        except Exception as e:
            return _result_json(False, error=f"{type(e).__name__}: {e}")


class QdrantUpsertOne:
    CATEGORY = "Qdrant / Local"
    FUNCTION = "run"
    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("result_json",)
    OUTPUT_IS_LIST = (False,)

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "db_path": ("STRING", {"default": r"./qdrant_data"}),
                "collection_name": ("STRING", {"default": "bodies"}),
                "point_id": ("STRING", {"default": "item_0001"}),
                "vector": ("STRING", {"multiline": True, "default": "[0.0, 0.1, 0.2]"}),
            },
            "optional": {
                "expected_dim": ("INT", {"default": 0, "min": 0, "max": 65536}),
                "payload_json": ("STRING", {"multiline": True, "default": ""}),
                # New optional path fields (STRING, green connectors)
                "file_name": ("STRING", {"default": ""}),
                "parent_dir": ("STRING", {"default": ""}),
                "full_path": ("STRING", {"default": ""}),
            }
        }

    def run(self, db_path: str, collection_name: str, point_id: Any, vector: Any,
            expected_dim: int = 0, payload_json: str = "",
            file_name: str = "", parent_dir: str = "", full_path: str = ""):
        client = _get_client(db_path)
        try:
            expected = int(expected_dim) if expected_dim and expected_dim > 0 else None
            vec = _parse_vector_any(vector, expected_dim=expected)
            vec = np.asarray(vec, dtype=np.float32).tolist()
            base_payload = _maybe_json_payload(payload_json)
            payload = _merge_path_fields(base_payload, file_name=file_name, parent_dir=parent_dir, full_path=full_path)
            pid = _coerce_point_id(point_id)

            point = models.PointStruct(id=pid, vector=vec, payload=payload)
            res = client.upsert(collection_name=collection_name, points=[point])

            # echo path fields in result_json for convenience
            f, p, fp = _extract_path_fields(payload)
            return _result_json(True, upserted=1, status=str(res.status), id=pid,
                                file_name=f, parent_dir=p, full_path=fp)
        except Exception as e:
            return _result_json(False, error=f"{type(e).__name__}: {e}")


class QdrantUpsertBatchNPZ:
    CATEGORY = "Qdrant / Local"
    FUNCTION = "run"
    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("result_json",)
    OUTPUT_IS_LIST = (False,)

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "db_path": ("STRING", {"default": r"./qdrant_data"}),
                "collection_name": ("STRING", {"default": "bodies"}),
                "npz_path": ("STRING", {"default": r"./batch.npz"}),
            },
            "optional": {
                "payloads_json": ("STRING", {"multiline": True, "default": ""}),
                "cast_float16": ("BOOLEAN", {"default": False}),
                # New: allow passing a JSON list parallel to vectors with path fields
                # Example: paths_json = "[{\"file_name\":\"a.jpg\",\"parent_dir\":\"/data\",\"full_path\":\"/data/a.jpg\"}, ...]"
                "paths_json": ("STRING", {"multiline": True, "default": ""}),
            }
        }

    def run(self, db_path: str, collection_name: str, npz_path: str,
            payloads_json: str = "", cast_float16: bool = False, paths_json: str = ""):
        client = _get_client(db_path)
        try:
            if not os.path.isfile(npz_path):
                return _result_json(False, error=f"File not found: {npz_path}")

            data = np.load(npz_path, allow_pickle=True)
            if "ids" not in data or "vectors" not in data:
                return _result_json(False, error="NPZ must contain 'ids' and 'vectors' arrays")

            ids = data["ids"]
            vectors = data["vectors"]
            if vectors.ndim != 2:
                return _result_json(False, error=f"'vectors' must be 2D (N x D), got shape {vectors.shape}")

            payloads = None
            if payloads_json.strip():
                payloads = json.loads(payloads_json)
                if not isinstance(payloads, list) or len(payloads) != len(vectors):
                    return _result_json(False, error="payloads_json must be a JSON list with same length as vectors")

            paths_list = None
            if paths_json.strip():
                paths_list = json.loads(paths_json)
                if not isinstance(paths_list, list) or len(paths_list) != len(vectors):
                    return _result_json(False, error="paths_json must be a JSON list with same length as vectors")

            if cast_float16:
                vectors = vectors.astype(np.float16)
                send_vectors = vectors.astype(np.float32)
            else:
                send_vectors = vectors.astype(np.float32)

            pts = []
            echoed_paths: List[Dict[str, Optional[str]]] = []
            for i in range(len(send_vectors)):
                base_payload = None if payloads is None else payloads[i]
                file_name = parent_dir = full_path = ""
                if paths_list is not None:
                    item = paths_list[i] or {}
                    file_name = item.get("file_name", "") if isinstance(item, dict) else ""
                    parent_dir = item.get("parent_dir", "") if isinstance(item, dict) else ""
                    full_path = item.get("full_path", "") if isinstance(item, dict) else ""
                merged_payload = _merge_path_fields(base_payload, file_name=file_name, parent_dir=parent_dir, full_path=full_path)

                raw_id = ids[i].item() if hasattr(ids[i], "item") else ids[i]
                pid = _coerce_point_id(raw_id)
                pts.append(models.PointStruct(id=pid, vector=send_vectors[i].tolist(), payload=merged_payload))

                f, p, fp = _extract_path_fields(merged_payload)
                echoed_paths.append({"id": pid, "file_name": f, "parent_dir": p, "full_path": fp})

            res = client.upsert(collection_name=collection_name, points=pts)
            return _result_json(True, upserted=len(pts), status=str(res.status), paths=echoed_paths)
        except Exception as e:
            return _result_json(False, error=f"{type(e).__name__}: {e}")


class QdrantSearch:
    CATEGORY = "Qdrant / Local"
    FUNCTION = "run"
    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("result_json",)
    OUTPUT_IS_LIST = (False,)

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "db_path": ("STRING", {"default": r"./qdrant_data"}),
                "collection_name": ("STRING", {"default": "bodies"}),
                "query_vector": ("STRING", {"multiline": True, "default": "[0.0, 0.1, 0.2]"}),
                "top_k": ("INT", {"default": 16, "min": 1, "max": 1000}),
            },
            "optional": {
                "expected_dim": ("INT", {"default": 0, "min": 0, "max": 65536}),
                "hnsw_ef": ("INT", {"default": 128, "min": 8, "max": 4096}),
                "exact": ("BOOLEAN", {"default": False}),
                "with_payload": ("BOOLEAN", {"default": True}),
                "with_vectors": ("BOOLEAN", {"default": False}),
                "score_threshold": ("FLOAT", {"default": 0.0, "min": 0.0, "max": 1e9}),
            }
        }

    def run(self, db_path: str, collection_name: str, query_vector: Any, top_k: int,
            expected_dim: int = 0, hnsw_ef: int = 128, exact: bool = False,
            with_payload: bool = True, with_vectors: bool = False, score_threshold: float = 0.0):
        client = _get_client(db_path)
        try:
            expected = int(expected_dim) if expected_dim and expected_dim > 0 else None
            qvec = _parse_vector_any(query_vector, expected_dim=expected)
            qvec = np.asarray(qvec, dtype=np.float32).tolist()

            sp = models.SearchParams(hnsw_ef=int(hnsw_ef), exact=bool(exact))
            res = client.search(
                collection_name=collection_name,
                query_vector=qvec,
                limit=int(top_k),
                with_payload=with_payload,
                with_vectors=with_vectors,
                score_threshold=float(score_threshold) if score_threshold > 0 else None,
                search_params=sp,
            )

            out = []
            for r in res:
                f, p, fp = _extract_path_fields(r.payload if with_payload else None)
                out.append({
                    "id": r.id,
                    "score": float(r.score),
                    "payload": r.payload if with_payload else None,
                    "vector_len": (len(r.vector) if (with_vectors and hasattr(r, "vector") and r.vector is not None) else None),
                    # Echo path metadata (if present in payload)
                    "file_name": f,
                    "parent_dir": p,
                    "full_path": fp,
                })
            return _result_json(True, results=out, count=len(out))
        except Exception as e:
            return _result_json(False, error=f"{type(e).__name__}: {e}")


class QdrantRetrieve:
    CATEGORY = "Qdrant / Local"
    FUNCTION = "run"
    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("result_json",)
    OUTPUT_IS_LIST = (False,)

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "db_path": ("STRING", {"default": r"./qdrant_data"}),
                "collection_name": ("STRING", {"default": "bodies"}),
                "ids_text": ("STRING", {"multiline": False, "default": "[\"item_0001\"]"}),
            },
            "optional": {
                "with_payload": ("BOOLEAN", {"default": True}),
                "with_vectors": ("BOOLEAN", {"default": False}),
            }
        }

    def run(self, db_path: str, collection_name: str, ids_text: str,
            with_payload: bool = True, with_vectors: bool = False):
        client = _get_client(db_path)
        try:
            ids_raw = _parse_ids_any(ids_text)
            ids = [_coerce_point_id(x) for x in ids_raw]
            res = client.retrieve(
                collection_name=collection_name,
                ids=ids,
                with_payload=with_payload,
                with_vectors=with_vectors,
            )
            out = []
            for r in res:
                f, p, fp = _extract_path_fields(r.payload if with_payload else None)
                out.append({
                    "id": r.id,
                    "payload": r.payload if with_payload else None,
                    "vector_len": (len(r.vector) if (with_vectors and hasattr(r, "vector") and r.vector is not None) else None),
                    "file_name": f,
                    "parent_dir": p,
                    "full_path": fp,
                })
            return _result_json(True, results=out, count=len(out))
        except Exception as e:
            return _result_json(False, error=f"{type(e).__name__}: {e}")


class QdrantUpdatePayload:
    CATEGORY = "Qdrant / Local"
    FUNCTION = "run"
    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("result_json",)
    OUTPUT_IS_LIST = (False,)

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "db_path": ("STRING", {"default": r"./qdrant_data"}),
                "collection_name": ("STRING", {"default": "bodies"}),
                "point_id": ("STRING", {"default": "item_0001"}),
                "payload_json": ("STRING", {"multiline": True, "default": "{\"label\":\"ok\"}"}) ,
            },
            "optional": {
                # Optional path fields to inject/override
                "file_name": ("STRING", {"default": ""}),
                "parent_dir": ("STRING", {"default": ""}),
                "full_path": ("STRING", {"default": ""}),
            }
        }

    def run(self, db_path: str, collection_name: str, point_id: Any, payload_json: str,
            file_name: str = "", parent_dir: str = "", full_path: str = ""):
        client = _get_client(db_path)
        try:
            base_payload = _maybe_json_payload(payload_json) or {}
            payload = _merge_path_fields(base_payload, file_name=file_name, parent_dir=parent_dir, full_path=full_path)
            pid = _coerce_point_id(point_id)
            client.set_payload(
                collection_name=collection_name,
                payload=payload,
                points=[pid],
            )
            f, p, fp = _extract_path_fields(payload)
            return _result_json(True, updated=1, id=pid, file_name=f, parent_dir=p, full_path=fp)
        except Exception as e:
            return _result_json(False, error=f"{type(e).__name__}: {e}")


class QdrantDeletePoints:
    CATEGORY = "Qdrant / Local"
    FUNCTION = "run"
    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("result_json",)
    OUTPUT_IS_LIST = (False,)

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "db_path": ("STRING", {"default": r"./qdrant_data"}),
                "collection_name": ("STRING", {"default": "bodies"}),
                "ids_text": ("STRING", {"multiline": False, "default": "[\"item_0001\"]"}),
            }
        }

    def run(self, db_path: str, collection_name: str, ids_text: str):
        client = _get_client(db_path)
        try:
            raw_ids = _parse_ids_any(ids_text)
            ids = [_coerce_point_id(x) for x in raw_ids]
            res = client.delete(
                collection_name=collection_name,
                points_selector=models.PointIdsList(points=ids),
            )
            return _result_json(True, deleted=len(ids), status=str(res.status))
        except Exception as e:
            return _result_json(False, error=f"{type(e).__name__}: {e}")


NODE_CLASS_MAPPINGS = {
    "QdrantEnsureCollection": QdrantEnsureCollection,
    "QdrantUpsertOne": QdrantUpsertOne,
    "QdrantUpsertBatchNPZ": QdrantUpsertBatchNPZ,
    "QdrantSearch": QdrantSearch,
    "QdrantRetrieve": QdrantRetrieve,
    "QdrantUpdatePayload": QdrantUpdatePayload,
    "QdrantDeletePoints": QdrantDeletePoints,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "QdrantEnsureCollection": "Qdrant: Ensure/Recreate Collection (Local)",
    "QdrantUpsertOne": "Qdrant: Upsert One (Local)",
    "QdrantUpsertBatchNPZ": "Qdrant: Upsert Batch from NPZ (Local)",
    "QdrantSearch": "Qdrant: Search (Local)",
    "QdrantRetrieve": "Qdrant: Retrieve by IDs (Local)",
    "QdrantUpdatePayload": "Qdrant: Update Payload (Local)",
    "QdrantDeletePoints": "Qdrant: Delete by IDs (Local)",
}
# ============================= NEW NODES (Tensor-ready) =============================
# ---------- NEW: Top-1 search node (returns only the single best hit) ----------
class QdrantSearchTop1:
    CATEGORY = "Qdrant / Local"
    FUNCTION = "run"
    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("result_json",)
    OUTPUT_IS_LIST = (False,)

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "db_path": ("STRING", {"default": r"./qdrant_data"}),
                "collection_name": ("STRING", {"default": "bodies"}),
                "query_vector": ("STRING", {"multiline": True, "default": "[0.0, 0.1, 0.2]"}),
            },
            "optional": {
                "expected_dim": ("INT", {"default": 0, "min": 0, "max": 65536}),
                "hnsw_ef": ("INT", {"default": 128, "min": 8, "max": 4096}),
                "exact": ("BOOLEAN", {"default": False}),
                "with_payload": ("BOOLEAN", {"default": True}),
                "with_vectors": ("BOOLEAN", {"default": False}),
                "score_threshold": ("FLOAT", {"default": 0.0, "min": 0.0, "max": 1e9}),
            }
        }

    def run(self, db_path: str, collection_name: str, query_vector: Any,
            expected_dim: int = 0, hnsw_ef: int = 128, exact: bool = False,
            with_payload: bool = True, with_vectors: bool = False, score_threshold: float = 0.0):
        client = _get_client(db_path)
        try:
            expected = int(expected_dim) if expected_dim and expected_dim > 0 else None
            qvec = _parse_vector_any(query_vector, expected_dim=expected)
            qvec = np.asarray(qvec, dtype=np.float32).tolist()

            sp = models.SearchParams(hnsw_ef=int(hnsw_ef), exact=bool(exact))
            res = client.search(
                collection_name=collection_name,
                query_vector=qvec,
                limit=1,  # ← 只取 Top-1
                with_payload=with_payload,
                with_vectors=with_vectors,
                score_threshold=float(score_threshold) if score_threshold > 0 else None,
                search_params=sp,
            )

            out = []
            for r in res:  # 有就只會有一筆
                f, p, fp = _extract_path_fields(r.payload if with_payload else None)
                out.append({
                    "id": r.id,
                    "score": float(r.score),
                    "payload": r.payload if with_payload else None,
                    "vector_len": (len(r.vector) if (with_vectors and hasattr(r, "vector") and r.vector is not None) else None),
                    "file_name": f,
                    "parent_dir": p,
                    "full_path": fp,
                })
            return _result_json(True, results=out, count=len(out))
        except Exception as e:
            return _result_json(False, error=f"{type(e).__name__}: {e}")

NODE_CLASS_MAPPINGS.update({
    "QdrantSearchTop1": QdrantSearchTop1,
})

NODE_DISPLAY_NAME_MAPPINGS.update({
    "QdrantSearchTop1": "Qdrant: Search Best (Top-1) (Local)",
})
# ---------- NEW: Get vector by point ID ----------
class QdrantGetVectorByID:
    CATEGORY = "Qdrant / Local"
    FUNCTION = "run"
    # 第一個輸出就是向量（JSON 字串，方便直接接到 QdrantSearch 的 query_vector）
    RETURN_TYPES = ("STRING", "STRING",)
    RETURN_NAMES = ("vector_json", "result_json",)
    OUTPUT_IS_LIST = (False, False,)

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "db_path": ("STRING", {"default": r"./qdrant_data"}),
                "collection_name": ("STRING", {"default": "bodies"}),
                "point_id": ("STRING", {"default": "item_0001"}),
            },
            "optional": {
                # 若是命名向量集合，這裡可指定名稱；非命名向量可留空
                "vector_name": ("STRING", {"default": ""}),
            }
        }

    def run(self, db_path: str, collection_name: str, point_id: str, vector_name: str = ""):
        client = _get_client(db_path)
        try:
            pid = _coerce_point_id(point_id)
            recs = client.retrieve(
                collection_name=collection_name,
                ids=[pid],
                with_payload=False,
                with_vectors=True,
            )
            if not recs:
                err = {"ok": False, "error": f"ID not found: {pid}"}
                return (json.dumps([], ensure_ascii=False), json.dumps(err, ensure_ascii=False))

            r = recs[0]
            vec = None

            # 單一向量集合
            if hasattr(r, "vector") and r.vector is not None:
                vec = r.vector
            else:
                # 命名向量集合
                vdict = getattr(r, "vectors", None)
                if isinstance(vdict, dict):
                    if vector_name and vector_name in vdict:
                        vec = vdict[vector_name]
                    elif len(vdict) == 1:
                        # 只有一個命名向量就直接取用
                        vec = list(vdict.values())[0]
                    else:
                        err = {"ok": False, "error": "Multiple named vectors; specify vector_name"}
                        return (json.dumps([], ensure_ascii=False), json.dumps(err, ensure_ascii=False))

            if vec is None:
                err = {"ok": False, "error": "Vector not found on record"}
                return (json.dumps([], ensure_ascii=False), json.dumps(err, ensure_ascii=False))

            # 轉成 float32 一維 list，並輸出為 JSON 字串（綠色 STRING）
            vec = np.asarray(vec, dtype=np.float32).reshape(-1).tolist()
            vec_json = json.dumps(vec, ensure_ascii=False)

            result = {"ok": True, "id": r.id, "vector_len": len(vec)}
            return (vec_json, json.dumps(result, ensure_ascii=False))

        except Exception as e:
            err = {"ok": False, "error": f"{type(e).__name__}: {e}"}
            return (json.dumps([], ensure_ascii=False), json.dumps(err, ensure_ascii=False))


# 註冊到映射
NODE_CLASS_MAPPINGS.update({
    "QdrantGetVectorByID": QdrantGetVectorByID,
})

NODE_DISPLAY_NAME_MAPPINGS.update({
    "QdrantGetVectorByID": "Qdrant: Get Vector by ID (Local)",
})

