"""素材诊断 API：让 LLM 自主调用只读工具做投放效果诊断。"""
from __future__ import annotations

from fastapi import APIRouter

from app.agent import diagnose_agent

router = APIRouter(prefix="/api/v1/diagnose", tags=["diagnose"])


def ok(data=None) -> dict:
    return {"code": 0, "msg": "ok", "data": data}


def fail(status: int, msg: str):
    from fastapi.responses import JSONResponse

    return JSONResponse(status_code=status, content={"code": status, "msg": msg, "data": None})


@router.get("/tools")
async def list_tools():
    """列出诊断可用的工具（**只读**），便于前端展示「模型能查什么」。"""
    return ok({
        "count": len(diagnose_agent.TOOLS_SCHEMA),
        "read_only": True,
        "tools": [
            {
                "name": t["function"]["name"],
                "description": t["function"]["description"],
                "required": t["function"]["parameters"].get("required", []),
            }
            for t in diagnose_agent.TOOLS_SCHEMA
        ],
        "max_steps": diagnose_agent.MAX_STEPS,
        "thresholds": {
            "low_ctr_ratio": diagnose_agent.LOW_CTR_RATIO,
            "min_impressions": diagnose_agent.MIN_IMPRESSIONS,
        },
    })


@router.post("/{ad_id}")
async def diagnose(ad_id: str, payload: dict | None = None):
    """诊断一条素材。

    模型会自己决定调哪些只读工具（查素材指标 / 查平台基线 / 查版本链 / 查违规），
    然后给出「正常 / 偏低 / 样本不足」的结论与下一步建议。

    LLM 不可用时自动退化为规则诊断（`fallback=true`），接口不会失败。
    """
    body = payload or {}
    max_steps = int(body.get("max_steps") or diagnose_agent.MAX_STEPS)
    try:
        result = diagnose_agent.diagnose(ad_id, max_steps=max_steps)
        return ok(result)
    except ValueError as exc:
        return fail(400, str(exc))
    except Exception as exc:  # noqa: BLE001
        return fail(500, str(exc))


@router.get("/{ad_id}/rule")
async def rule_diagnose(ad_id: str):
    """只跑规则诊断（不调 LLM），用于对照「模型诊断 vs 规则诊断」。"""
    try:
        return ok(diagnose_agent.rule_based_diagnosis(ad_id))
    except Exception as exc:  # noqa: BLE001
        return fail(500, str(exc))
