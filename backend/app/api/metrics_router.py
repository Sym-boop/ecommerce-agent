"""投放效果回流 API。

三个端点：
    POST /api/v1/metrics/report    平台回传（单条 / 批量）
    GET  /api/v1/metrics/summary   汇总（整体 / 分平台 / 分日 / AI vs 人工）
    GET  /api/v1/metrics/rows      明细

设计要点见 `app/common/metrics_ingest.py`：
按 (ad_id, platform, date) 幂等覆盖、整批校验失败整批拒收、
写入后可选把累计值回写到素材记录供 A/B 报告直接使用。
"""
from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, ValidationError

from app.common import metrics_ingest

router = APIRouter(prefix="/api/v1/metrics", tags=["metrics"])


def ok(data=None) -> dict:
    return {"code": 200, "msg": "ok", "data": data}


def fail(status: int, msg: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"code": status, "msg": msg})


class MetricItem(BaseModel):
    ad_id: str = Field(..., description="素材 ID")
    platform: str = Field("", description="投放平台")
    date: str = Field("", description="统计日期 YYYY-MM-DD，缺省今天")
    impressions: int = Field(0, ge=0, description="曝光数")
    clicks: int = Field(0, ge=0, description="点击数")
    conversions: int = Field(0, ge=0, description="转化数")
    spend: float = Field(0.0, ge=0, description="花费（元）")


class ReportPayload(BaseModel):
    items: list[MetricItem] = Field(default_factory=list, description="批量回传")
    sync: bool = Field(True, description="是否把累计值回写到素材记录")


@router.post("/report")
def report(payload: dict):
    """平台回传效果数据。

    单条：`{"ad_id": "...", "platform": "...", "date": "2026-10-27", ...}`
    批量：`{"items": [ ... ]}`

    校验不通过返回 400，并给出**具体是哪一条、哪个字段、为什么**。
    """
    try:
        # 支持单条 / 批量两种形态
        if "items" in payload:
            try:
                model = ReportPayload(**payload)
            except ValidationError as exc:
                return fail(400, _humanize(exc))
            result = metrics_ingest.record_batch([i.model_dump() for i in model.items])
            sync = model.sync
        else:
            try:
                item = MetricItem(**payload)
            except ValidationError as exc:
                return fail(400, _humanize(exc))
            result = metrics_ingest.record(item.model_dump())
            sync = bool(payload.get("sync", True))

        if sync:
            result["sync"] = metrics_ingest.sync_to_ads()
        return ok(result)
    except metrics_ingest.MetricsError as exc:
        return fail(400, str(exc))
    except Exception as exc:  # noqa: BLE001
        return fail(400, f"回传失败：{exc}")


def _humanize(exc: "ValidationError") -> str:
    """把 pydantic 的报错压成一句人话。"""
    parts = []
    for err in exc.errors():
        field = ".".join(str(x) for x in err.get("loc", []) if x != "body")
        parts.append(f"{field} {err.get('msg', '')}")
    return "回传字段不合法：" + "；".join(parts)


@router.get("/summary")
def summary(days: int = 30, platform: str = ""):
    """效果汇总：CTR / CVR / CPA / CPM + 分维度拆分。"""
    try:
        return ok(metrics_ingest.summary(days=days, platform=platform))
    except Exception as exc:  # noqa: BLE001
        return fail(500, str(exc))


@router.get("/rows")
def rows(ad_id: str = "", limit: int = 200):
    """效果明细（按日期升序）。"""
    try:
        return ok(metrics_ingest.list_rows(ad_id=ad_id, limit=limit))
    except Exception as exc:  # noqa: BLE001
        return fail(500, str(exc))


@router.post("/sync")
def sync():
    """手动把累计指标回写到素材记录。"""
    try:
        return ok(metrics_ingest.sync_to_ads())
    except Exception as exc:  # noqa: BLE001
        return fail(500, str(exc))


# ---------------------------------------------------------------------------
# LLM Token 用量（按日 / 按月可聚合）
# ---------------------------------------------------------------------------

@router.get("/token-usage")
def token_usage_summary(period: str = "month"):
    """LLM token 用量汇总。

    `period` 可选：`today` / `month`（默认）/ `year` / `all` / `30d` / `7d`。

    返回整体（调用次数 / 输入输出 token / 估算成本）、按日、按月、按模型拆分，
    以及「按窗口内日均推算的 30 天用量」。
    """
    try:
        from app.common import token_usage

        return ok(token_usage.summary(period=period))
    except Exception as exc:  # noqa: BLE001
        return fail(500, str(exc))


@router.get("/token-usage/rows")
def token_usage_rows(limit: int = 20):
    """最近 N 条调用明细。"""
    try:
        from app.common import token_usage

        return ok(token_usage.last_n(limit))
    except Exception as exc:  # noqa: BLE001
        return fail(500, str(exc))


@router.post("/token-usage/record")
def token_usage_record(payload: dict):
    """手动记一笔（用于压测/回填；正常调用由 copywriter 自动记录）。"""
    try:
        from app.common import token_usage

        row = token_usage.record(
            model=str(payload.get("model") or ""),
            prompt_tokens=int(payload.get("prompt_tokens") or 0),
            completion_tokens=int(payload.get("completion_tokens") or 0),
            scene=str(payload.get("scene") or "manual"),
        )
        return ok(row)
    except Exception as exc:  # noqa: BLE001
        return fail(400, str(exc))

