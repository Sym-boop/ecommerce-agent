"""系统管理 API：配置自检 / 指标 / 审计 / 质量 / 版本 / 数据飞轮。

这些是「可观测性」与「治理」的入口，原先系统只暴露了 `/health`，
出问题时看不到成本、失败率、谁改了什么。

    GET  /api/v1/system/config-status   启动配置自检结果（fail-fast 提示）
    GET  /api/v1/system/metrics         调用量 / 耗时 / 失败率 / 成本估算
    GET  /api/v1/system/audit           操作审计日志（支持过滤）
    GET  /api/v1/system/audit/stats     审计概览
    GET  /api/v1/system/quality         质量评估看板
    GET  /api/v1/system/quality/evals   原始评估记录
    GET  /api/v1/system/versions/{ad_id}        版本链
    GET  /api/v1/system/versions/{ad_id}/diff   版本 diff
    GET  /api/v1/system/flywheel        数据飞轮概览
    POST /api/v1/system/flywheel/flush  把待入库样本补写进向量库
    GET  /api/v1/system/rbac            角色权限矩阵
"""
from __future__ import annotations

import os

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse

from app.api import deps
from app.api.deps import Actor
from app.common import rbac

router = APIRouter(prefix="/api/v1/system", tags=["system"])


def ok(data=None) -> dict:
    return {"code": 0, "msg": "ok", "data": data}


def fail(status: int, msg: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"code": status, "msg": msg})


# ---------------------------------------------------------------------------
# 配置自检
# ---------------------------------------------------------------------------

@router.get("/config-status")
async def config_status():
    """启动配置自检（Key 是否配置、模型名是否有效、词表是否可用等）。

    这是对「静默降级」的防线：`.env` 里模型名写错时，
    文案会悄悄退回模板，只有这里能一眼看出来。
    """
    from app.common import config_check
    return ok(config_check.collect())


# ---------------------------------------------------------------------------
# 指标与成本
# ---------------------------------------------------------------------------

@router.get("/metrics")
async def metrics_snapshot():
    """调用量 / 失败率 / 降级次数 / P95 耗时 / 成本估算。"""
    from app.common import metrics
    return ok(metrics.snapshot())


# ---------------------------------------------------------------------------
# 审计日志
# ---------------------------------------------------------------------------

@router.get("/audit")
async def audit_log(limit: int = Query(default=200, ge=1, le=2000),
                    action: str = "", target: str = "", actor: str = "",
                    _: Actor = Depends(deps.require_perm(rbac.PERM_ADMIN))):
    """操作审计日志（倒序）。

    「谁、何时、对哪条素材做了什么、改前改后是什么」都可查。
    """
    from app.common import audit
    return ok(audit.read(limit=limit, action=action, target=target, actor=actor))


@router.get("/audit/stats")
async def audit_stats(_: Actor = Depends(deps.require_perm(rbac.PERM_ADMIN))):
    from app.common import audit
    return ok(audit.stats())


# ---------------------------------------------------------------------------
# 质量评估
# ---------------------------------------------------------------------------

@router.get("/quality")
async def quality_dashboard(limit: int = Query(default=500, ge=1, le=5000)):
    """质量看板：平均分、指标通过率、分数分布、低分样本。"""
    from app.common import quality_store
    return ok(quality_store.dashboard(limit=limit))


@router.get("/quality/evals")
async def quality_evals(limit: int = Query(default=100, ge=1, le=1000), platform: str = ""):
    from app.common import quality_store
    return ok(quality_store.list_evals(limit=limit, platform=platform))


@router.post("/quality/judge")
async def quality_judge(payload: dict | None = None):
    """LLM 自动评分（需 LLM_JUDGE_ENABLED=true，会产生真实 API 成本）。"""
    from app.common import quality

    body = payload or {}
    result = quality.llm_judge(
        title=str(body.get("title") or ""),
        content=str(body.get("content") or ""),
        platform=str(body.get("platform") or "xiaohongshu"),
        sell_points=body.get("sell_points") or [],
    )
    return ok(result)


# ---------------------------------------------------------------------------
# 素材版本
# ---------------------------------------------------------------------------

@router.get("/versions/{ad_id}")
async def versions_of(ad_id: str):
    """某条素材的版本链（谁在什么时候改了什么）。"""
    from app.common import versions
    return ok({"ad_id": ad_id, "versions": versions.history(ad_id), "stats": versions.stats()})


@router.get("/versions/{ad_id}/diff")
async def versions_diff(ad_id: str, from_version: int | None = None,
                        to_version: int | None = None, field: str = "content"):
    """两个版本之间的差异（默认：第一版 → 最新版）。"""
    from app.common import versions
    return ok(versions.diff(ad_id, from_version, to_version, field))


# ---------------------------------------------------------------------------
# 数据飞轮
# ---------------------------------------------------------------------------

@router.get("/flywheel")
async def flywheel_stats():
    """数据飞轮概览：已回流样本数、待补写数。"""
    from app.common import knowledge_feedback
    return ok(knowledge_feedback.stats())


@router.get("/flywheel/samples")
async def flywheel_samples(limit: int = Query(default=50, ge=1, le=500)):
    """最近回流的人工认可样本。"""
    from app.common import knowledge_feedback
    return ok(knowledge_feedback.read_queue(limit=limit))


@router.post("/flywheel/flush")
async def flywheel_flush(actor: Actor = Depends(deps.require_perm(rbac.PERM_ADMIN))):
    """把待入库队列补写进向量库（Milvus 恢复后调用）。"""
    from app.common import audit, knowledge_feedback

    result = knowledge_feedback.flush_queue()
    audit.record("flywheel.flush", target="knowledge_base", target_type="config",
                 detail=result, result="ok" if result.get("succeeded") else "queued")
    return ok(result)


# ---------------------------------------------------------------------------
# 图片理解校验
# ---------------------------------------------------------------------------

@router.get("/image-check/config")
async def image_check_config():
    """图片校验能力：客观预检总是可用；语义校验需配置视觉模型。"""
    from app.common import image_check
    return ok({
        "prescreen_available": True,
        "vision_available": image_check.vision_enabled(),
        "vision_model": os.getenv("VISION_MODEL", ""),
        "note": "客观预检检查分辨率与宽高比一致性；语义一致性校验需要独立的视觉模型 API",
    })


@router.post("/image-check")
async def image_check_run(payload: dict | None = None):
    """校验一张图文素材与文案是否一致。

    请求体::

        {"result_url": "/media/images/x.png", "title": "...", "content": "...",
         "input_image": "/media/uploads/y.png"}

    返回分两层：`prescreen`（客观，总是可用）与 `vision`（语义，可选）。
    """
    from app.common import image_check

    body = payload or {}
    result_url = str(body.get("result_url") or "").strip()
    if not result_url:
        return fail(400, "请提供 result_url（成品图地址）")

    result = image_check.check_asset(
        result_url=result_url,
        title=str(body.get("title") or ""),
        content=str(body.get("content") or ""),
        input_image=body.get("input_image"),
    )
    return ok(result)


# ---------------------------------------------------------------------------
# 投放效果对照（A/B）
# ---------------------------------------------------------------------------

@router.get("/ab-report")
async def ab_report(limit: int = Query(default=500, ge=1, le=5000)):
    """投放效果对照报告：按「AI 直出 / 人工介入」与平台两个维度对照。

    合规率与质量分来自真实数据；CTR/CVR 需接入平台回传指标（当前投递为 mock）。
    """
    from app.common import ab_report as report
    return ok(report.build_report(limit=limit))


@router.post("/ab-report/metrics/{ad_id}")
async def ab_record_metrics(ad_id: str, payload: dict | None = None):
    """写入某条素材的真实投放指标（曝光/点击/转化）。

    这是对接平台数据的入口：写入后 `/ab-report` 会自动计算 CTR 与 CVR。
    """
    from app.common import ab_report as report

    body = payload or {}
    updated = report.record_metrics(
        ad_id,
        impressions=int(body.get("impressions") or 0),
        clicks=int(body.get("clicks") or 0),
        conversions=int(body.get("conversions") or 0),
    )
    if updated is None:
        return fail(404, "广告物料不存在")
    return ok({"id": ad_id, "impressions": updated.get("impressions"),
               "clicks": updated.get("clicks"), "conversions": updated.get("conversions")})


# ---------------------------------------------------------------------------
# 存储后端
# ---------------------------------------------------------------------------

@router.get("/storage")
async def storage_backend():
    """当前存储后端（json / sqlite）与容量情况。

    用 `STORE_BACKEND=sqlite` 可切到 SQLite（WAL + 事务）：
    解决 JSON 全量重写的写放大，以及多 worker 互相覆盖的问题。
    """
    from app.common import store_backend

    info = store_backend.backend_info()
    try:
        from app.common import json_store

        info["data_dir"] = str(json_store.DATA_DIR)
    except Exception:  # noqa: BLE001
        pass
    return ok(info)


# ---------------------------------------------------------------------------
# 异步任务
# ---------------------------------------------------------------------------

@router.get("/jobs/backend")
async def jobs_backend():
    """当前异步任务后端（线程池 / Celery）与选择原因。

    这是对「以为在用 Celery 其实没有」的防线：Redis 不可达时会自动回退线程池，
    功能可用但不支持多 worker。
    """
    from app.common import jobs
    return ok(jobs.backend_info())


@router.get("/jobs")
async def jobs_list(limit: int = Query(default=50, ge=1, le=500),
                    kind: str = "", status: str = ""):
    """任务列表（倒序）。"""
    from app.common import jobs
    return ok({"items": jobs.list_jobs(limit=limit, kind=kind, status=status),
               "stats": jobs.stats()})


@router.get("/jobs/{job_id}")
async def jobs_get(job_id: str):
    """查询单个任务状态与结果。"""
    from app.common import jobs

    job = jobs.get(job_id)
    if job is None:
        return fail(404, "任务不存在（可能已被回收）")
    return ok(job)


@router.post("/jobs/{job_id}/cancel")
async def jobs_cancel(job_id: str, actor: Actor = Depends(deps.require_perm(rbac.PERM_ADMIN))):
    """取消任务（仅对尚未开始执行的任务有效）。"""
    from app.common import audit, jobs

    job = jobs.get(job_id)
    if job is None:
        return fail(404, "任务不存在")
    if job.get("status") in ("succeeded", "failed", "cancelled"):
        return fail(409, f"任务已处于终态（{job.get('status')}），无法取消")

    cancelled = jobs.cancel(job_id)
    audit.record("job.cancel", target=job_id, target_type="job",
                 detail={"kind": job.get("kind"), "cancelled": cancelled},
                 result="ok" if cancelled else "not_cancellable")
    if not cancelled:
        return fail(409, "任务已开始执行，无法取消（Python 线程不可中断）")
    return ok(jobs.get(job_id))


# ---------------------------------------------------------------------------
# 角色权限
# ---------------------------------------------------------------------------

@router.get("/rbac")
async def rbac_matrix(actor: Actor = Depends(deps.current_actor)):
    """角色权限矩阵 + 当前登录角色。"""
    return ok({**rbac.describe(), "current": actor.to_dict(),
               "current_permissions": sorted(rbac.permissions_for(actor.role))})
