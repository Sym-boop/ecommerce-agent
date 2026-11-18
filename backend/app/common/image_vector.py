"""图片向量化与视觉检索。

**放进 RAG 的图片用来干什么**

不是「以图搜图找素材」，而是：**把生成过/投放过的优质海报入库，下次生成时召回
相似风格的图作为视觉参考**。这样图文生成的风格也能像文案一样「有先例可循」。

**为什么默认不用 CLIP**

| 方案 | 维度 | 依赖 | 能表达什么 |
|---|---|---|---|
| **HSV 颜色直方图（默认）** | 1024 | 只要 numpy + Pillow | 颜色/色调分布：一眼看上去像不像 |
| CLIP 语义向量（可选） | 512→补零到 1024 | 需联网下载模型（数百 MB） | 语义：图里是不是同一个东西 |

本项目离线环境下载不了模型，所以默认走颜色直方图——**它检索的是「视觉风格相似」，
不是「语义相同」**。这一点会写进返回值的 `provider` 字段，避免夸大。

要切 CLIP：`IMAGE_EMBED_PROVIDER=clip`，并**重建集合**（两种向量不可混用，
否则距离没有意义）。

**1024 维是怎么来的**

HSV 三维各量化：H 16 档 × S 8 档 × V 8 档 = **正好 1024 维**，
和 `knowledge_base` 集合的维度一致，不需要补零或改 schema。

**另外还存一个 dHash（64 位）**

颜色直方图对「同一张图缩放/轻微压缩」不敏感，但对「构图相同、色调不同」会判远。
dHash 是灰度差值哈希，用来做**近重复检测**（比如同一张图被重复上传），
存在 metadata 里，需要时可做二次过滤。
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("uvicorn.error")

# 图片单独的集合：视觉向量和文案文本向量语义不同，混在一起会互相干扰
IMAGE_COLLECTION = os.getenv("RAG_IMAGE_COLLECTION", "image_knowledge")

# 默认用颜色直方图；clip 需联网下载模型
PROVIDER = os.getenv("IMAGE_EMBED_PROVIDER", "hist").strip().lower()

SUPPORTED_IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}

# 直方图维度：16 × 8 × 8 = 1024（与 Milvus 集合维度对齐）
DIM = 1024


class ImageVectorError(RuntimeError):
    """图片向量化失败。"""


def _load_image(path: str | Path):
    try:
        from PIL import Image
    except ImportError as exc:
        raise ImageVectorError("未安装 Pillow（pip install Pillow）") from exc

    p = Path(path)
    if not p.exists():
        raise ImageVectorError(f"图片不存在: {p}")
    if p.suffix.lower() not in SUPPORTED_IMAGE_EXT:
        raise ImageVectorError(f"不支持的图片格式 {p.suffix}，支持: {sorted(SUPPORTED_IMAGE_EXT)}")
    try:
        img = Image.open(p)
        img.load()
        return img.convert("RGB")
    except Exception as exc:  # noqa: BLE001
        raise ImageVectorError(f"图片读取失败: {exc}") from exc


def _descriptor(img) -> list[float]:
    """1024 维视觉描述子（多特征拼接，比单一颜色直方图区分度高得多）。

    | 组成 | 维度 | 表达什么 |
    |---|---|---|
    | 全局 HSV 联合直方图（H8×S8×V8） | 512 | 整体色调分布 |
    | 4×4 网格 × 色调 H8 直方图 | 128 | **空间布局**：左上是暖色还是冷色 |
    | 4×4 网格 × (S4×V4) 直方图 | 256 | 各区域的饱和度/明度 |
    | 4×4 网格 × 8 方向梯度直方图 | 128 | 纹理与边缘走向 |
    | **合计** | **1024** | |

    为什么要加空间和梯度：纯全局颜色直方图对「同模板白底海报」几乎无区分度
    （实测 4 张同模板海报两两余弦都是 1.000）。加了空间网格和梯度方向后，
    构图和纹理不同就能被区分出来。
    """
    import numpy as np

    small = img.resize((128, 128))
    rgb = np.asarray(small, dtype=np.float32)
    hsv = np.asarray(small.convert("HSV"), dtype=np.float32)
    gray = rgb @ np.array([0.299, 0.587, 0.114], dtype=np.float32)

    parts: list[np.ndarray] = []

    # ① 全局 HSV 联合直方图 H8×S8×V8 = 512
    hq = np.minimum((hsv[:, :, 0] * 8 / 256).astype(np.int32), 7)
    sq = np.minimum((hsv[:, :, 1] * 8 / 256).astype(np.int32), 7)
    vq = np.minimum((hsv[:, :, 2] * 8 / 256).astype(np.int32), 7)
    idx = (hq * 64 + sq * 8 + vq).ravel()
    hist = np.bincount(idx, minlength=512).astype(np.float64)[:512]
    parts.append(hist / (hist.sum() or 1.0))

    # 空间网格：4×4
    cells = 4
    step = 128 // cells

    # ② 每格 H8 直方图 → 16 × 8 = 128
    hue_grid = []
    for gy in range(cells):
        for gx in range(cells):
            cell_h = hq[gy * step:(gy + 1) * step, gx * step:(gx + 1) * step].ravel()
            h8 = np.bincount(cell_h, minlength=8).astype(np.float64)[:8]
            hue_grid.append(h8 / (h8.sum() or 1.0))
    parts.append(np.concatenate(hue_grid))

    # ③ 每格 (S4×V4) 直方图 → 16 × 16 = 256
    sv_grid = []
    s4 = np.minimum((hsv[:, :, 1] * 4 / 256).astype(np.int32), 3)
    v4 = np.minimum((hsv[:, :, 2] * 4 / 256).astype(np.int32), 3)
    for gy in range(cells):
        for gx in range(cells):
            cs = s4[gy * step:(gy + 1) * step, gx * step:(gx + 1) * step].ravel()
            cv = v4[gy * step:(gy + 1) * step, gx * step:(gx + 1) * step].ravel()
            joint = np.bincount(cs * 4 + cv, minlength=16).astype(np.float64)[:16]
            sv_grid.append(joint / (joint.sum() or 1.0))
    parts.append(np.concatenate(sv_grid))

    # ④ 每格 8 方向梯度直方图 → 16 × 8 = 128
    gx_ = np.zeros_like(gray)
    gy_ = np.zeros_like(gray)
    gx_[:, 1:-1] = gray[:, 2:] - gray[:, :-2]
    gy_[1:-1, :] = gray[2:, :] - gray[:-2, :]
    mag = np.hypot(gx_, gy_)
    ang = (np.arctan2(gy_, gx_) + np.pi) / (2 * np.pi)          # 0..1
    aq = np.minimum((ang * 8).astype(np.int32), 7)
    grad_grid = []
    for gy_i in range(cells):
        for gx_i in range(cells):
            sl = (slice(gy_i * step, (gy_i + 1) * step), slice(gx_i * step, (gx_i + 1) * step))
            a = aq[sl].ravel()
            m = mag[sl].ravel()
            hist8 = np.bincount(a, weights=m, minlength=8).astype(np.float64)[:8]
            grad_grid.append(hist8 / (hist8.sum() or 1.0))
    parts.append(np.concatenate(grad_grid))

    vec = np.concatenate(parts)
    # 分部加权：颜色是主信号，结构是辅信号。
    # 不加权时「同构图不同色」的两张图余弦能到 0.53（结构部分把相似度拉高了），
    # 会让检索把「布局像但颜色完全不同」的图排到前面。加权后颜色主导排序。
    weights = np.concatenate([
        np.full(512, 1.6),    # 全局 HSV 直方图
        np.full(128, 1.4),    # 空间色调网格
        np.full(256, 0.8),    # 空间饱和度/明度
        np.full(128, 0.7),    # 梯度方向
    ])
    vec = vec * weights
    assert vec.shape[0] == DIM, f"描述子维度 {vec.shape[0]} != {DIM}"
    norm = np.linalg.norm(vec)
    if norm > 0:
        vec = vec / norm
    return [float(x) for x in vec]


def _dhash(img, size: int = 8) -> str:
    """灰度差值哈希（64 位），用于近重复检测。"""
    import numpy as np

    gray = np.asarray(img.convert("L").resize((size + 1, size)), dtype=np.int16)
    diff = gray[:, 1:] > gray[:, :-1]
    bits = "".join("1" if b else "0" for b in diff.ravel())
    return f"{int(bits, 2):016x}"


def _clip_vector(img) -> list[float]:
    """CLIP 语义向量（可选）。模型不可用时抛错，由调用方回退。"""
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError as exc:
        raise ImageVectorError("未安装 sentence-transformers，无法使用 CLIP") from exc

    model_name = os.getenv("CLIP_MODEL", "clip-ViT-B-32")
    try:
        model = SentenceTransformer(model_name)
        vec = model.encode(img, convert_to_numpy=True).tolist()
    except Exception as exc:  # noqa: BLE001
        raise ImageVectorError(f"CLIP 模型加载/推理失败（{exc}）") from exc

    if len(vec) < DIM:
        vec = list(vec) + [0.0] * (DIM - len(vec))
    return [float(x) for x in vec[:DIM]]


def visual_vector(path: str | Path, *, provider: Optional[str] = None) -> tuple[list[float], str]:
    """图片 → 1024 维向量。返回 (向量, 实际使用的 provider)。

    provider=hist 时用 HSV 直方图；provider=clip 时用 CLIP，**失败自动回退 hist**
    并在返回值里标注，避免「以为用了 CLIP」。
    """
    use = (provider or PROVIDER or "hist").lower()
    img = _load_image(path)

    if use == "clip":
        try:
            return _clip_vector(img), "clip"
        except ImageVectorError as exc:
            logger.warning("CLIP 不可用（%s），回退 HSV 直方图", exc)
    return _descriptor(img), "hist"


def insert_image(
    path: str | Path,
    *,
    platform: str = "",
    category: str = "",
    title: str = "",
    prompt: str = "",
    collection: str = IMAGE_COLLECTION,
    provider: Optional[str] = None,
) -> dict[str, Any]:
    """把一张图存入视觉向量库。"""
    from app.common import milvus_cli

    vec, used = visual_vector(path, provider=provider)
    img = _load_image(path)
    p = Path(path)

    item = {
        "_vector": vec,
        "text": f"[图片] {title or p.name}",
        "platform": platform,
        "category": category,
        "original_title": (title or p.name)[:200],
        "_image": {
            "path": str(p),
            "filename": p.name,
            "width": img.width,
            "height": img.height,
            "dhash": _dhash(img),
            "provider": used,
            "prompt": prompt,
        },
    }
    res = milvus_cli.get_wrapper().insert_vectors([item], collection_name=collection)
    return {
        "file": p.name,
        "provider": used,
        "dim": len(vec),
        "dhash": item["_image"]["dhash"],
        "size": f"{img.width}x{img.height}",
        "mode": res.get("mode"),
        "collection": collection,
    }


def search_similar_image(
    path: str | Path,
    *,
    top_k: int = 3,
    platform: str = "",
    collection: str = IMAGE_COLLECTION,
    provider: Optional[str] = None,
    min_score: float = 0.0,
) -> dict[str, Any]:
    """用一张图检索视觉上相似的图。"""
    from app.common import milvus_cli

    vec, used = visual_vector(path, provider=provider)
    filters = {"platform": platform} if platform else None
    hits = milvus_cli.get_wrapper().search_vector(vec, top_k=top_k, collection_name=collection, filters=filters)
    hits = [h for h in hits if float(h.get("score", 0)) >= min_score]
    return {
        "query": Path(path).name,
        "provider": used,
        "top_k": top_k,
        "hits": [
            {
                "score": round(float(h.get("score", 0)), 4),
                "title": h.get("original_title", ""),
                "path": (h.get("_image") or {}).get("path", ""),
                "platform": h.get("platform", ""),
                "dhash": (h.get("_image") or {}).get("dhash", ""),
            }
            for h in hits
        ],
    }


def dedupe_by_hash(paths: list[str | Path]) -> dict[str, Any]:
    """按 dHash 找出近重复图片（同一张图被重复上传时用）。"""
    import numpy as np

    groups: dict[str, list[str]] = {}
    hashes: dict[str, str] = {}
    for p in paths:
        try:
            h = _dhash(_load_image(p))
        except ImageVectorError:
            continue
        hashes[str(p)] = h

    items = list(hashes.items())
    used: set[str] = set()
    dup_pairs: list[dict[str, Any]] = []
    for i, (pa, ha) in enumerate(items):
        if pa in used:
            continue
        for pb, hb in items[i + 1:]:
            if pb in used:
                continue
            dist = bin(int(ha, 16) ^ int(hb, 16)).count("1")
            if dist <= 5:      # 汉明距离 ≤ 5 视为近重复
                dup_pairs.append({"a": Path(pa).name, "b": Path(pb).name, "hamming": dist})
                used.add(pb)
    return {"total": len(items), "near_duplicates": dup_pairs}
