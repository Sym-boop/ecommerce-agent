"""广告生成 API（统一入口，取代原「文案 / 图文 / 视频生成」三个页面）。

链路：选商品（可自定义） → 生成文案（可重生成，商品/关键词不变） → 文案审查
      → 选择继续生成 图文 / 视频（可同时，各可重生成） → 存到投放 / 暂停存半成品 / 删除

本路由以 LangGraph 主图（`app.agent.ad_graph`）驱动文案生成与审查：
    POST /api/v1/adgen/copy                 生成 / 重新生成文案（自动送文案审查）
    POST /api/v1/adgen/review/{session_id}  命中违禁词后的人工处置（放行 / 驳回）→ 恢复图执行
    POST /api/v1/adgen/asset                基于已通过审查的文案生成 图文 / 视频（异步任务）
    GET  /api/v1/adgen/asset/status/{id}    查询图文 / 视频生成状态
    GET  /api/v1/adgen/session/{id}         会话快照（供横向步骤卡回填）
    POST /api/v1/adgen/save                 存到投放界面（进入广告流水线）+ 写入历史记录
    POST /api/v1/adgen/pause                暂停生成并退出（写入半成品历史记录）
    POST /api/v1/adgen/discard              删除此次生成（未保存则仅清会话）
"""
from __future__ import annotations

import json
import logging
import shutil
import threading
import uuid
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, File, Form, UploadFile
from fastapi.responses import JSONResponse

from app.agent import ad_graph
from app.common import history_store, json_store, pipeline

logger = logging.getLogger("uvicorn.error")

router = APIRouter(prefix="/api/v1/adgen", tags=["adgen"])

UPLOAD_DIR = Path("data/media/uploads")
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
ALLOWED_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}

_LOCK = threading.Lock()
_ADGEN_FILE = "adgen_sessions.json"
# 生成会话落盘到 <仓库根>/data/adgen_sessions.json，刷新页面 / 重启进程后仍可回填
_SESSIONS: dict[str, dict] = json_store.read_dict(_ADGEN_FILE, {})
# 图文 / 视频异步任务为运行期状态，进程重启即失效，无需落盘
_ASSET_TASKS: dict[str, dict] = {}


def _persist_sessions() -> None:
    """把全部生成会话原子写盘（调用方需持有 _LOCK）。"""
    json_store.write_json(_ADGEN_FILE, _SESSIONS)


def ok(data=None) -> dict:
    return {"code": 0, "msg": "ok", "data": data}


def fail(status: int, msg: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"code": status, "msg": msg})


def _actor() -> str:
    """当前操作人（由鉴权依赖写入 audit 的线程本地存储）。"""
    try:
        from app.common import audit
        return audit.current_actor().get("username") or ""
    except Exception:  # noqa: BLE001
        return ""


def _new_session() -> dict:
    session = {
        "session_id": f"AGEN-{datetime.now().strftime('%Y%m%d')}-{uuid.uuid4().hex[:6]}",
        "product": {},
        "keyword": "",
        "platform": "xiaohongshu",
        "copy": {},
        "review": {},
        "image": {},
        "video": {},
        "record_id": None,
        "thread_id": "",
        "status": "draft",
        "created_at": datetime.now().isoformat(timespec="seconds"),
    }
    with _LOCK:
        _SESSIONS[session["session_id"]] = session
        _persist_sessions()
    return session


def _get_session(session_id: str) -> dict | None:
    with _LOCK:
        return _SESSIONS.get(session_id)


def _save_upload(image: UploadFile | None) -> Path | None:
    """保存上传的商品图片，返回本地路径。"""
    if image is None or not image.filename:
        return None
    suffix = Path(image.filename).suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        raise ValueError("请上传 png / jpg / jpeg / webp / bmp 格式的图片")
    path = UPLOAD_DIR / f"{uuid.uuid4().hex[:8]}{suffix}"
    with open(path, "wb") as f:
        shutil.copyfileobj(image.file, f)
    return path


# ---------------------------------------------------------------------------
# 文案生成 / 审查
# ---------------------------------------------------------------------------

@router.post("/copy")
async def generate_copy_api(payload: dict | None = None):
    """生成文案（同步）；`regenerate=true` 时沿用原商品与关键词，只重出文案。

    大模型调用通常 5~15 秒，会挂住请求。需要不阻塞时用 `POST /adgen/copy-async`。
    """
    return generate_copy_core(payload or {})


def generate_copy_core(body: dict):
    """文案生成核心逻辑（同步与异步端点共用，避免两套实现）。"""
    session_id = str(body.get("session_id") or "").strip()
    regenerate = bool(body.get("regenerate"))

    session = _get_session(session_id) if session_id else None
    if session is None:
        session = _new_session()

    if regenerate and session.get("product"):
        product = session["product"]
        keyword = session.get("keyword") or ""
    else:
        product = body.get("product") or session.get("product") or {}
        keyword = str(body.get("keyword") if body.get("keyword") is not None
                      else session.get("keyword") or "").strip()

    if not (product.get("name") or "").strip():
        return fail(400, "请选择或自定义商品")

    platform = str(body.get("platform") or session.get("platform") or "xiaohongshu")
    attempt = int(session.get("attempt") or 0) + 1
    thread_id = f"{session['session_id']}-a{attempt}"

    try:
        result = ad_graph.run_ad_generation(
            thread_id,
            product=product, keyword=keyword, platform=platform,
            want_image=False, want_video=False,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("文案生成失败 session=%s: %s", session["session_id"], exc)
        return fail(500, f"文案生成失败：{exc}")

    if result.get("status") == "failed":
        return fail(500, result.get("error") or "文案生成失败")

    with _LOCK:
        session.update({
            "product": result.get("product") or product,
            "keyword": keyword,
            "platform": platform,
            "copy": result.get("copy") or {},
            "review": result.get("review") or {},
            "attempt": attempt,
            "thread_id": thread_id,
            "image": {},
            "video": {},
            "status": "review_required" if ad_graph.is_interrupted(result) else "copy_ready",
        })
        # 质量评估：客观指标（零成本），供质量看板沉淀趋势
        try:
            from app.common import quality_store
            copy = session.get("copy") or {}
            review = session.get("review") or {}
            evaluation = quality_store.record(
                session_id=session["session_id"],
                platform=platform,
                title=copy.get("title", ""),
                content=copy.get("content", ""),
                tags=copy.get("tags") or [],
                sell_points=session["product"].get("sell_points") or [],
                violations=review.get("violations") or [],
                source="generation",
            )
            session["quality"] = {
                "score": evaluation.get("score"),
                "metrics": evaluation.get("metrics"),
                "eval_id": evaluation.get("id"),
            }
        except Exception as exc:  # noqa: BLE001
            logger.warning("质量评估失败（不影响生成）: %s", exc)
        _persist_sessions()
        snapshot = dict(session)

    return ok(snapshot)


@router.post("/copy-async")
async def generate_copy_async_api(payload: dict | None = None):
    """生成文案（**异步**）：立即返回 job_id，前端轮询 `/api/v1/system/jobs/{id}`。

    用途：批量生成、或前端希望在等待期间保持可交互，避免长请求占住连接。
    """
    from app.common import jobs

    body = payload or {}
    body["_async"] = True
    job = jobs.submit("adgen.copy", _generate_copy_job, body, actor=_actor())
    return ok({"job_id": job["job_id"], "status": job["status"],
               "poll": f"/api/v1/system/jobs/{job['job_id']}",
               "backend": job.get("backend")})


def _generate_copy_job(body: dict):
    """异步任务体（模块级函数，便于 Celery 序列化）。"""
    resp = generate_copy_core(body)
    # 同步端点返回的是 fastapi Response（ok 是 dict / fail 是 JSONResponse）
    if isinstance(resp, dict):
        return {"code": 0, "msg": "ok", "data": resp.get("data")}
    try:
        payload = json.loads(bytes(resp.body).decode("utf-8"))
    except Exception:  # noqa: BLE001
        payload = {"code": resp.status_code, "msg": "生成失败"}
    if payload.get("code") != 0:
        raise RuntimeError(payload.get("msg") or "文案生成失败")
    return payload


@router.post("/review/{session_id}")
async def review_copy_api(session_id: str, payload: dict | None = None):
    """文案命中违禁词后的人工处置：approve 放行 / reject 驳回，并恢复图执行。"""
    body = payload or {}
    decision = str(body.get("decision") or "").strip().lower()
    if decision not in ("approve", "reject"):
        return fail(400, "请选择 放行(approve) 或 驳回(reject)")

    session = _get_session(session_id)
    if session is None:
        return fail(404, "生成会话不存在")
    if session.get("status") != "review_required":
        return fail(409, "当前会话不在待人工审查状态")

    try:
        result = ad_graph.resume_ad_generation(session["thread_id"], decision)
    except Exception as exc:  # noqa: BLE001
        return fail(500, f"审查处置失败：{exc}")

    with _LOCK:
        session.update({
            "copy": result.get("copy") or session.get("copy") or {},
            "review": result.get("review") or {},
            "status": "review_rejected" if result.get("status") == "rejected" else "copy_ready",
        })
        _persist_sessions()
        snapshot = dict(session)
    return ok(snapshot)


# ---------------------------------------------------------------------------
# 图文 / 视频生成（基于已通过审查的文案）
# ---------------------------------------------------------------------------

def _run_asset(task_id: str, session_id: str, asset_type: str,
               keyword: str, image_path: str | None) -> None:
    task = _ASSET_TASKS.get(task_id)
    if task is None:
        return
    session = _get_session(session_id) or {}
    state = {
        "copy": session.get("copy") or {},
        "product": session.get("product") or {},
        "keyword": keyword,
        "input_image_path": image_path,
    }
    task["status"] = "processing"
    try:
        if asset_type == "image":
            out = ad_graph.gen_image_node(state)
            image = out.get("image") or {}
            task.update(status="succeeded", url=image.get("url"),
                        error=image.get("error"))
            # 图片一致性校验：客观预检（零成本）总是执行；
            # 语义校验仅在配置了视觉模型时执行，能力边界写进返回值
            if image.get("url") and not image.get("error"):
                try:
                    from app.common import image_check
                    copy = session.get("copy") or {}
                    image["consistency"] = image_check.check_asset(
                        result_url=image["url"],
                        title=copy.get("title", ""),
                        content=copy.get("content", ""),
                        input_image=session.get("uploaded_image"),
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning("图片一致性校验失败（不影响生成）: %s", exc)
            with _LOCK:
                session["image"] = image
                _persist_sessions()
        else:
            out = ad_graph.gen_video_node(state)
            task.update(status="succeeded", url=(out.get("video") or {}).get("url"),
                        error=(out.get("video") or {}).get("error"))
            with _LOCK:
                session["video"] = out.get("video") or {}
                _persist_sessions()
        if task.get("error"):
            task["status"] = "failed"
    except Exception as exc:  # noqa: BLE001
        logger.warning("%s 生成失败 task=%s: %s", asset_type, task_id, exc)
        task["status"] = "failed"
        task["error"] = str(exc)


@router.post("/asset")
async def generate_asset_api(
    background: BackgroundTasks,
    session_id: str = Form(...),
    type: str = Form(...),
    keyword: str = Form(""),
    image: UploadFile | None = File(None),
):
    """基于已通过审查的文案生成图文 / 视频；图文与视频均可选提供图片与关键词。"""
    asset_type = (type or "").strip().lower()
    if asset_type not in ("image", "video"):
        return fail(400, "生成类型仅支持 image / video")

    session = _get_session(session_id)
    if session is None:
        return fail(404, "生成会话不存在")
    if not (session.get("copy") or {}).get("content"):
        return fail(400, "请先生成通过审查的文案")
    if session.get("status") == "review_required":
        return fail(409, "文案命中违禁词，请先在生成页完成审查处置")
    if session.get("status") == "review_rejected":
        return fail(409, "文案审查已被驳回，无法继续生成图文 / 视频")

    try:
        saved = _save_upload(image)
    except ValueError as exc:
        return fail(400, str(exc))

    task_id = f"{'IMG' if asset_type == 'image' else 'VID'}-{datetime.now().strftime('%Y%m%d')}-{uuid.uuid4().hex[:6]}"
    _ASSET_TASKS[task_id] = {
        "task_id": task_id,
        "type": asset_type,
        "session_id": session_id,
        "status": "pending",
        "keyword": keyword,
        "input_image_url": f"/media/uploads/{saved.name}" if saved else None,
        "url": None,
        "error": None,
    }
    background.add_task(
        _run_asset, task_id, session_id, asset_type, keyword, str(saved) if saved else None,
    )
    return ok({"task_id": task_id})


@router.get("/asset/status/{task_id}")
async def asset_status_api(task_id: str):
    task = _ASSET_TASKS.get(task_id)
    if task is None:
        return fail(404, "生成任务不存在")
    return ok(task)


@router.get("/session/{session_id}")
async def get_session_api(session_id: str):
    session = _get_session(session_id)
    if session is None:
        return fail(404, "生成会话不存在")
    return ok(session)


@router.post("/import")
async def import_history_api(payload: dict | None = None):
    """把一条历史记录载入为生成会话，作为模板继续生成图文 / 视频。"""
    body = payload or {}
    record_id = str(body.get("record_id") or "").strip()
    if not record_id:
        return fail(400, "请提供历史记录 id")
    record = history_store.get_record(record_id)
    if record is None:
        return fail(404, "历史记录不存在")

    session = _new_session()
    with _LOCK:
        session.update({
            "product": record.get("product") or {},
            "keyword": record.get("keyword") or "",
            "platform": record.get("platform") or "xiaohongshu",
            "copy": record.get("copy") or {},
            "image": {"url": record.get("image_url")} if record.get("image_url") else {},
            "video": {"url": record.get("video_url")} if record.get("video_url") else {},
            "record_id": record_id,
            "status": "copy_ready",
            "from_history": True,
        })
        _persist_sessions()
        snapshot = dict(session)
    return ok(snapshot)


# ---------------------------------------------------------------------------
# 存到投放 / 暂停存半成品 / 删除
# ---------------------------------------------------------------------------

@router.post("/save")
async def save_to_delivery_api(payload: dict | None = None):
    """存到投放界面：把本次生成的广告登记进广告流水线（审查→审批→投放），并写入历史记录。"""
    body = payload or {}
    session_id = str(body.get("session_id") or "").strip()
    session = _get_session(session_id)
    if session is None:
        return fail(404, "生成会话不存在")

    copy = session.get("copy") or {}
    if not copy.get("content"):
        return fail(400, "尚无文案可保存")

    product = session.get("product") or {}
    product_name = product.get("name") or "未命名商品"
    prompt = "；".join(p for p in [product_name, session.get("keyword"), copy.get("title")] if p)

    from app.common import audit, rbac, versions

    actor = _actor()
    category = str(product.get("category") or "")

    ad_ids: list[str] = []
    deduped: list[str] = []

    def _register(**kwargs) -> None:
        ad = pipeline.register_ad(
            prompt=prompt, platform=session.get("platform", ""),
            actor=actor, category=category, source="ai",
            session_id=session_id, **kwargs,
        )
        ad_ids.append(ad["id"])
        # 幂等命中：同一份内容重复保存时复用既有物料，不新增
        if ad.get("deduped"):
            deduped.append(ad["id"])

    # 1) 文案广告
    _register(ad_type="copy", title=copy.get("title", ""), content=copy.get("content", ""))
    # 2) 图文广告（如有）
    image_url = (session.get("image") or {}).get("url")
    if image_url:
        _register(ad_type="image", title=copy.get("title", ""), content=copy.get("content", ""),
                  input_image=session.get("uploaded_image"), result_url=image_url)
    # 3) 视频广告（如有）
    video_url = (session.get("video") or {}).get("url")
    if video_url:
        _register(ad_type="video", title=copy.get("title", ""),
                  input_image=session.get("uploaded_image"), result_url=video_url)

    record = history_store.save_record(
        product=product, keyword=session.get("keyword", ""), platform=session.get("platform", ""),
        copy=copy, image_url=image_url, video_url=video_url,
        stage=history_store.STAGE_NORMAL, record_id=session.get("record_id"),
    )
    with _LOCK:
        session["record_id"] = record["id"]
        session["status"] = "saved"
        _persist_sessions()

    audit.record("ad.save", target=session_id, target_type="session",
                 detail={"ad_ids": ad_ids, "deduped": deduped, "record_id": record["id"]})

    message = "已进入广告流水线，待审查与审批通过后可在「广告投放」投放"
    if deduped:
        message = f"其中 {len(deduped)} 条与既有物料内容相同，已复用未重复入库；" + message
    return ok({"record": record, "ad_ids": ad_ids, "deduped_ids": deduped,
               "message": message})


@router.post("/pause")
async def pause_generation_api(payload: dict | None = None):
    """暂停生成并退出：把当前进度保存为半成品历史记录（前端红色标签）。"""
    body = payload or {}
    session_id = str(body.get("session_id") or "").strip()
    session = _get_session(session_id)
    if session is None:
        return fail(404, "生成会话不存在")

    if not (session.get("copy") or {}).get("content"):
        return fail(400, "尚无任何生成内容，无法保存半成品")

    record = history_store.save_record(
        product=session.get("product") or {}, keyword=session.get("keyword", ""),
        platform=session.get("platform", ""), copy=session.get("copy") or {},
        image_url=(session.get("image") or {}).get("url"),
        video_url=(session.get("video") or {}).get("url"),
        stage=history_store.STAGE_WIP, record_id=session.get("record_id"),
    )
    with _LOCK:
        session["record_id"] = record["id"]
        session["status"] = "paused"
        _persist_sessions()
    return ok(record)


@router.post("/discard")
async def discard_generation_api(payload: dict | None = None):
    """删除此次生成：若已落盘历史记录则一并删除，并清理会话。"""
    body = payload or {}
    session_id = str(body.get("session_id") or "").strip()
    session = _get_session(session_id)
    removed = False
    if session and session.get("record_id"):
        removed = history_store.delete_record(session["record_id"])
    with _LOCK:
        _SESSIONS.pop(session_id, None)
        _persist_sessions()
    return ok({"session_id": session_id, "record_deleted": removed})