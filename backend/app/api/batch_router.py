"""批量生成 API（Dify 风格分叉树）。

一次批量生成以「商品 → 文案（批量）→ 图文 / 视频（每个文案再批量）」的分叉树展开：

    POST /api/v1/batch/copy              批量生成文案（1~8 条，自动送文案审查）
    POST /api/v1/batch/asset             为某条文案批量生成 图文 / 视频（1~5 个）
    POST /api/v1/batch/regenerate        重新生成单个节点（文案 / 图文 / 视频）
    GET  /api/v1/batch/session/{id}      分叉树快照（供前端渲染）
    POST /api/v1/batch/save              保存到投放界面 + 历史记录（可指定文案节点）
    POST /api/v1/batch/discard           丢弃整棵分叉树

生成均为异步（后台线程 + 并发上限 5），前端轮询 session 快照即可看到节点状态变化。
"""
from __future__ import annotations

import logging
import shutil
import threading
import uuid
from pathlib import Path

from fastapi import APIRouter, File, Form, UploadFile
from fastapi.responses import JSONResponse

from app.agent import ad_graph
from app.common import batch_store, history_store, pipeline

logger = logging.getLogger("uvicorn.error")

router = APIRouter(prefix="/api/v1/batch", tags=["batch"])

UPLOAD_DIR = Path("data/media/uploads")
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
ALLOWED_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}

# 并发上限：一次最多同时生成 5 个（文案 / 图文 / 视频共用）
_GEN_SEMA = threading.Semaphore(5)


def ok(data=None) -> dict:
    return {"code": 0, "msg": "ok", "data": data}


def fail(status: int, msg: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"code": status, "msg": msg})


def _spawn(fn, *args) -> None:
    threading.Thread(target=fn, args=args, daemon=True).start()


def _save_upload(image: UploadFile | None) -> Path | None:
    if image is None or not image.filename:
        return None
    suffix = Path(image.filename).suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        raise ValueError("请上传 png / jpg / jpeg / webp / bmp 格式的图片")
    path = UPLOAD_DIR / f"{uuid.uuid4().hex[:8]}{suffix}"
    with open(path, "wb") as f:
        shutil.copyfileobj(image.file, f)
    return path


def _cluster(value: int, lo: int, hi: int, default: int) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        n = default
    return max(lo, min(hi, n))


# ---------------------------------------------------------------------------
# 生成内核（复用 LangGraph 主图的节点函数，避免两套实现）
# ---------------------------------------------------------------------------

def _gen_copy_payload(product: dict, keyword: str, platform: str) -> tuple[dict, dict]:
    """生成一条文案并做违禁词审查，返回 (copy, review)；失败抛异常。"""
    state = ad_graph.select_product({"product": product or {}})
    state["platform"] = platform
    state["keyword"] = keyword
    out = ad_graph.gen_copy(state)
    copy = out.get("copy") or {}
    if not copy.get("content"):
        raise RuntimeError(out.get("error") or "文案生成失败")
    review = ad_graph.review_copy({"copy": copy}).get("review") or {}
    return copy, review


def _gen_asset_payload(copy_node: dict, asset_type: str, keyword: str,
                       image_path: str | None) -> dict:
    """基于一条文案生成图文 / 视频，返回 {'url': ...} 或抛异常。"""
    state = {
        "copy": {
            "title": copy_node.get("title", ""),
            "content": copy_node.get("content", ""),
            "tags": list(copy_node.get("tags") or []),
        },
        "keyword": keyword,
        "input_image_path": image_path,
    }
    if asset_type == "image":
        res = (ad_graph.gen_image_node(state)).get("image") or {}
    else:
        res = (ad_graph.gen_video_node(state)).get("video") or {}
    if res.get("error"):
        raise RuntimeError(res["error"])
    return res


def _run_copy(batch_id: str, node_id: str, product: dict, keyword: str, platform: str) -> None:
    with _GEN_SEMA:
        batch_store.update_node(batch_id, node_id, status="processing")
        try:
            copy, review = _gen_copy_payload(product, keyword, platform)
            batch_store.update_node(
                batch_id, node_id,
                title=copy.get("title", ""), content=copy.get("content", ""),
                tags=list(copy.get("tags") or []), review=review, status="done", error=None,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("批量文案生成失败 node=%s: %s", node_id, exc)
            batch_store.update_node(batch_id, node_id, status="failed", error=str(exc))


def _run_asset(batch_id: str, node_id: str, copy_node: dict, asset_type: str,
               keyword: str, image_path: str | None) -> None:
    with _GEN_SEMA:
        batch_store.update_node(batch_id, node_id, status="processing")
        try:
            res = _gen_asset_payload(copy_node, asset_type, keyword, image_path)
            batch_store.update_node(batch_id, node_id, url=res.get("url"),
                                    status="done", error=None)
        except Exception as exc:  # noqa: BLE001
            logger.warning("批量 %s 生成失败 node=%s: %s", asset_type, node_id, exc)
            batch_store.update_node(batch_id, node_id, status="failed", error=str(exc))


# ---------------------------------------------------------------------------
# 端点
# ---------------------------------------------------------------------------

@router.post("/copy")
async def batch_copy_api(payload: dict | None = None):
    """批量生成文案：count ∈ [1, 8]。首次调用会创建批量会话。"""
    body = payload or {}
    batch_id = str(body.get("batch_id") or "").strip()
    count = _cluster(body.get("count"), 1, batch_store.MAX_COPY_BATCH, 3)

    session = batch_store.get_session(batch_id) if batch_id else None
    if session is None:
        product = body.get("product") or {}
        if not (product.get("name") or "").strip():
            return fail(400, "请选择或自定义商品")
        session = batch_store.create_session(
            product=product,
            keyword=str(body.get("keyword") or "").strip(),
            platform=str(body.get("platform") or "xiaohongshu"),
        )
        batch_id = session["batch_id"]
    else:
        product = session.get("product") or {}
        if body.get("keyword") is not None:
            keyword = str(body.get("keyword") or "").strip()
        else:
            keyword = session.get("keyword") or ""

    product = session.get("product") or product
    keyword = str(body.get("keyword") if body.get("keyword") is not None
                  else session.get("keyword") or "").strip()
    platform = str(body.get("platform") or session.get("platform") or "xiaohongshu")

    # 已有文案数量，用于节点序号延续
    existing = [n for n in (batch_store.snapshot(batch_id) or {}).get("nodes", []) if n["type"] == "copy"]
    start_index = len(existing)

    for i in range(count):
        node = batch_store.add_node(
            batch_id, node_type="copy", parent_id=None, index=start_index + i,
            status="pending",
        )
        if node is None:
            return fail(404, "批量生成会话不存在")
        _spawn(_run_copy, batch_id, node["id"], product, keyword, platform)

    return ok(batch_store.snapshot(batch_id))


@router.post("/asset")
async def batch_asset_api(
    batch_id: str = Form(...),
    copy_id: str = Form(...),
    type: str = Form(...),
    count: str = Form("1"),
    keyword: str = Form(""),
    image: UploadFile | None = File(None),
):
    """为某条文案批量生成图文 / 视频：count ∈ [1, 5]。图文 / 视频均可选提供图片与关键词。"""
    asset_type = (type or "").strip().lower()
    if asset_type not in ("image", "video"):
        return fail(400, "生成类型仅支持 image / video")

    copy_node = batch_store.get_node(batch_id, copy_id)
    if copy_node is None:
        return fail(404, "文案节点不存在")
    if copy_node.get("status") != "done" or not copy_node.get("content"):
        return fail(409, "该文案尚未生成完成，无法继续生成图文 / 视频")

    n = _cluster(count, 1, batch_store.MAX_ASSET_BATCH, 1)

    try:
        saved = _save_upload(image)
    except ValueError as exc:
        return fail(400, str(exc))

    existing = [x for x in (batch_store.snapshot(batch_id) or {}).get("nodes", [])
                if x.get("parent_id") == copy_id and x["type"] == asset_type]
    start_index = len(existing)

    for i in range(n):
        node = batch_store.add_node(
            batch_id, node_type=asset_type, parent_id=copy_id, index=start_index + i,
            status="pending", keyword=keyword,
            input_image_url=f"/media/uploads/{saved.name}" if saved else None,
        )
        if node is None:
            return fail(404, "批量生成会话不存在")
        _spawn(_run_asset, batch_id, node["id"], copy_node, asset_type, keyword,
               str(saved) if saved else None)

    return ok(batch_store.snapshot(batch_id))


@router.post("/regenerate")
async def batch_regenerate_api(payload: dict | None = None):
    """重新生成单个节点（文案 / 图文 / 视频），保持其父级与关键词不变。"""
    body = payload or {}
    batch_id = str(body.get("batch_id") or "").strip()
    node_id = str(body.get("node_id") or "").strip()
    session = batch_store.get_session(batch_id)
    if session is None:
        return fail(404, "批量生成会话不存在")
    node = batch_store.get_node(batch_id, node_id)
    if node is None:
        return fail(404, "节点不存在")

    node_type = node.get("type")
    attempt = int(node.get("attempt") or 1) + 1
    batch_store.update_node(batch_id, node_id, attempt=attempt, error=None)

    if node_type == "copy":
        _spawn(_run_copy, batch_id, node_id, session.get("product") or {},
               session.get("keyword") or "", session.get("platform") or "xiaohongshu")
    else:
        parent = batch_store.get_node(batch_id, node.get("parent_id"))
        if parent is None:
            return fail(409, "父级文案节点已不存在，无法重新生成")
        _spawn(_run_asset, batch_id, node_id, parent, node_type,
               str(node.get("keyword") or ""), None)

    return ok(batch_store.snapshot(batch_id))


@router.get("/session/{batch_id}")
async def batch_session_api(batch_id: str):
    snap = batch_store.snapshot(batch_id)
    if snap is None:
        return fail(404, "批量生成会话不存在")
    return ok(snap)


@router.post("/save")
async def batch_save_api(payload: dict | None = None):
    """保存到投放界面 + 历史记录。

    可传 copy_ids 指定要保存的文案（连同其已生成的图文 / 视频子节点）；
    不传则保存全部已完成的文案。
    """
    body = payload or {}
    batch_id = str(body.get("batch_id") or "").strip()
    session = batch_store.get_session(batch_id)
    if session is None:
        return fail(404, "批量生成会话不存在")

    snap = batch_store.snapshot(batch_id) or {}
    nodes = snap.get("nodes") or []
    copy_nodes = [n for n in nodes if n["type"] == "copy" and n.get("content")]
    wanted = body.get("copy_ids")
    if wanted:
        keep = {str(c) for c in wanted}
        copy_nodes = [n for n in copy_nodes if n["id"] in keep]
    if not copy_nodes:
        return fail(400, "没有可保存的文案")

    product = session.get("product") or {}
    product_name = product.get("name") or "未命名商品"
    platform = session.get("platform") or ""
    keyword = session.get("keyword") or ""

    saved: list[dict] = []
    for cnode in copy_nodes:
        children = [x for x in nodes if x.get("parent_id") == cnode["id"] and x.get("status") == "done"]
        prompt = "；".join(p for p in [product_name, keyword, cnode.get("title")] if p)
        ad_ids = [pipeline.register_ad(
            ad_type="copy", title=cnode.get("title", ""), prompt=prompt,
            platform=platform, content=cnode.get("content", ""),
        )["id"]]
        image_url = video_url = None
        for ch in children:
            if ch["type"] == "image":
                image_url = ch.get("url")
                ad_ids.append(pipeline.register_ad(
                    ad_type="image", title=cnode.get("title", ""), prompt=prompt,
                    platform=platform, content=cnode.get("content", ""),
                    input_image=ch.get("input_image_url"), result_url=image_url,
                )["id"])
            elif ch["type"] == "video":
                video_url = ch.get("url")
                ad_ids.append(pipeline.register_ad(
                    ad_type="video", title=cnode.get("title", ""), prompt=prompt,
                    platform=platform, input_image=ch.get("input_image_url"),
                    result_url=video_url,
                )["id"])

        record = history_store.save_record(
            product=product, keyword=keyword, platform=platform,
            copy={"title": cnode.get("title", ""), "content": cnode.get("content", ""),
                  "tags": cnode.get("tags") or []},
            image_url=image_url, video_url=video_url, stage=history_store.STAGE_NORMAL,
        )
        saved.append({"copy_id": cnode["id"], "ad_ids": ad_ids, "record_id": record["id"]})

    return ok({"saved": saved, "count": len(saved),
               "message": "已存到投放界面并写入历史记录，待审查与审批通过后可在「广告投放」投放"})


@router.post("/discard")
async def batch_discard_api(payload: dict | None = None):
    """丢弃整棵分叉树（不保存任何内容）。"""
    body = payload or {}
    batch_id = str(body.get("batch_id") or "").strip()
    removed = batch_store.delete_session(batch_id)
    return ok({"batch_id": batch_id, "discarded": removed})