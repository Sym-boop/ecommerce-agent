"""素材诊断 Agent：LLM 驱动的**只读**工具调用。

**这个模块存在的理由**

项目的其余 Agent 都是**固定流程**——工具由状态机决定，模型只负责生成文本。
这在合规链路上是对的（要确定性），但缺了另一类能力：**开放式信息收集**。

诊断恰好属于后者：一条素材 CTR 偏低，原因可能是文案弱、图不吸引、流量错配、
或者本来就该等一等。**该查哪些维度，事前写不死**——这正是模型擅长的。

所以这里做的是**第一处让模型自己决定调什么工具的地方**，但刻意限定在三件事上：

1. **只读**：所有工具都只是查数据，没有任何写操作
2. **有轮次上限**：最多 `MAX_STEPS` 轮，避免模型无限调用
3. **失败可退**：LLM 不可用或调用失败 → 退化成确定性规则诊断，不让诊断功能消失

**为什么不把它放进投放链路**

投放是不可逆动作，要的是「每一步都可预期」。诊断是只读的、可重试的，
模型在这里探索不会造成损害。**判断哪个环节该允许自主性，是设计的一部分。**
"""
from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, Callable, Optional

logger = logging.getLogger("uvicorn.error")

# 跟随项目惯例：模块级加载 .env。否则单独调用本模块（不经 main.py）时读不到 LLM 配置，
# 会静默退化成规则诊断 —— 这个坑我在测试时踩到过。
_ROOT = Path(__file__).resolve().parents[3]
try:
    from dotenv import load_dotenv

    load_dotenv(_ROOT / ".env", override=False)
except Exception:  # noqa: BLE001
    pass

MAX_STEPS = int(os.getenv("DIAGNOSE_MAX_STEPS", "4"))

# 低 CTR 判定阈值：低于同平台均值的这个比例，视为「明显偏低」
LOW_CTR_RATIO = float(os.getenv("DIAGNOSE_LOW_CTR_RATIO", "0.7"))
# 样本量门槛：曝光太少时 CTR 噪声大，不下结论
MIN_IMPRESSIONS = int(os.getenv("DIAGNOSE_MIN_IMPRESSIONS", "1000"))


# ---------------------------------------------------------------------------
# 工具实现（全部只读）
# ---------------------------------------------------------------------------

def _tool_get_ad_detail(ad_id: str = "") -> dict:
    """查单条素材的指标与状态。"""
    from app.common import ad_store

    ad = ad_store.get_ad(ad_id) if ad_id else None
    if not ad:
        return {"error": f"未找到素材 {ad_id}"}
    return {
        "ad_id": ad.get("id"),
        "title": ad.get("title"),
        "platform": ad.get("platform"),
        "type": ad.get("type"),
        "stage": ad.get("stage"),
        "status": ad.get("status"),
        "budget": ad.get("budget"),
        "impressions": ad.get("impressions", 0),
        "clicks": ad.get("clicks", 0),
        "conversions": ad.get("conversions", 0),
        "spend": ad.get("spend", 0),
        "ctr": ad.get("ctr"),
        "cvr": ad.get("cvr"),
        "violations": [v.get("word") for v in (ad.get("violations") or [])],
        "copy_ok": ad.get("copy_ok"),
        "budget_ok": ad.get("budget_ok"),
        "created_at": ad.get("created_at"),
    }


def _tool_get_platform_baseline(platform: str = "") -> dict:
    """查同平台基线（CTR / CVR / CPA）。

    **注意这是"同平台"不是"同类目"**：物料表没有 category 字段，
    按类目聚合会得到空集合。用平台基线作为代理，并在返回里标明口径，
    避免下游把"平台均值"误读成"类目均值"。
    """
    from app.common import metrics_ingest

    s = metrics_ingest.summary()
    overall = s.get("overall") or {}
    by_platform = s.get("by_platform") or {}
    base = by_platform.get(platform) or {}
    return {
        "basis": "platform",          # 口径：平台基线（非类目）
        "platform": platform,
        "platform_baseline": {
            "ctr": base.get("ctr"),
            "cvr": base.get("cvr"),
            "cpa": base.get("cpa"),
            "impressions": base.get("impressions"),
            "rows": base.get("rows"),
        },
        "overall": {
            "ctr": overall.get("ctr"),
            "cvr": overall.get("cvr"),
            "cpa": overall.get("cpa"),
            "impressions": overall.get("impressions"),
        },
        "available_platforms": list(by_platform.keys()),
    }


def _tool_get_asset_versions(ad_id: str = "") -> dict:
    """查素材的版本链（改过几版、每版改了什么）。"""
    from app.common import versions

    try:
        hist = versions.history(ad_id) if ad_id else []
    except Exception as exc:  # noqa: BLE001
        return {"error": f"版本链读取失败: {exc}"}
    return {
        "ad_id": ad_id,
        "version_count": len(hist),
        "versions": [
            {
                "version": h.get("version"),
                "editor": h.get("editor"),
                "created_at": h.get("created_at"),
                "changed_fields": h.get("changed_fields") or h.get("fields"),
            }
            for h in hist[:10]
        ],
    }


def _tool_get_violation_history(ad_id: str = "") -> dict:
    """查违规历史（含审查是否复核过）。"""
    from app.common import ad_store

    ad = ad_store.get_ad(ad_id) if ad_id else None
    if not ad:
        return {"error": f"未找到素材 {ad_id}"}
    violations = ad.get("violations") or []
    return {
        "ad_id": ad_id,
        "violation_count": len(violations),
        "violations": [
            {"word": v.get("word"), "category": v.get("category"),
             "basis": v.get("basis"), "replacement": v.get("replacement")}
            for v in violations[:10]
        ],
        "copy_ok": ad.get("copy_ok"),
        "budget_ok": ad.get("budget_ok"),
        "budget_verdict": ad.get("budget_verdict"),
        "revalidated_at": ad.get("review_revalidated_at"),
    }


_TOOL_IMPL: dict[str, Callable[..., dict]] = {
    "get_ad_detail": _tool_get_ad_detail,
    "get_platform_baseline": _tool_get_platform_baseline,
    "get_asset_versions": _tool_get_asset_versions,
    "get_violation_history": _tool_get_violation_history,
}

# OpenAI 兼容的 tools schema
TOOLS_SCHEMA: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "get_ad_detail",
            "description": "查询单条广告素材的投放指标（曝光/点击/转化/花费/CTR/CVR）与当前状态、预算、违禁词命中情况。",
            "parameters": {
                "type": "object",
                "properties": {"ad_id": {"type": "string", "description": "素材 ID"}},
                "required": ["ad_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_platform_baseline",
            "description": "查询同平台的 CTE/CVR/CPA 基线，用于判断该素材是否明显低于平均。返回里 basis 字段标明口径。",
            "parameters": {
                "type": "object",
                "properties": {"platform": {"type": "string", "description": "平台标识，如 xiaohongshu / tiktok / amazon"}},
                "required": ["platform"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_asset_versions",
            "description": "查询素材的版本链：改过几版、谁改的、改了哪些字段。用于判断是否已经尝试过优化。",
            "parameters": {
                "type": "object",
                "properties": {"ad_id": {"type": "string", "description": "素材 ID"}},
                "required": ["ad_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_violation_history",
            "description": "查询素材的违禁词命中历史与合规状态（词、分类、法条依据、建议替换）。",
            "parameters": {
                "type": "object",
                "properties": {"ad_id": {"type": "string", "description": "素材 ID"}},
                "required": ["ad_id"],
            },
        },
    },
]

SYSTEM_PROMPT = """你是电商广告投放的效果诊断师。

任务：判断一条素材的 CTR 是否明显低于平台基线，并给出**可执行的下一步建议**。

工作方式：
- 你可以调用工具查询数据。**按需要决定查哪些**，不必全查。
- 先拿到素材自身指标和平台基线，再判断是否真的偏低。
- 若曝光量过小，要明确指出「样本不足，不建议据此改素材」。
- 若已有多版改动记录，要指出「已经改过 N 版，继续改收益可能有限」。

输出要求（纯文本，不要 JSON）：
1. 结论：正常 / 偏低 / 样本不足
2. 依据：用具体数字对比（素材 CTR vs 平台基线 CTR）
3. 归因：可能的原因，按可能性排序
4. 建议：下一步做什么，一到两条，要具体
"""


# ---------------------------------------------------------------------------
# LLM 调用（带 tools）
# ---------------------------------------------------------------------------

def _chat_url() -> str:
    base = os.getenv("LLM_BASE_URL", "https://api.deepseek.com").rstrip("/")
    if base.endswith("/v1"):
        return f"{base}/chat/completions"
    return f"{base}/v1/chat/completions"


def _call_llm_with_tools(messages: list[dict], tools: list[dict]) -> dict:
    """调一次带 tools 的 completion，返回原始 message 对象。"""
    api_key = os.getenv("LLM_API_KEY", "").strip()
    if not api_key or api_key.startswith("sk-xxxx"):
        raise RuntimeError("未配置有效 LLM_API_KEY")

    import httpx

    body = {
        "model": os.getenv("LLM_MODEL", "deepseek-chat"),
        "messages": messages,
        "tools": tools,
        "tool_choice": "auto",
        "temperature": 0.2,
    }
    with httpx.Client(timeout=60.0) as client:
        resp = client.post(
            _chat_url(),
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=body,
        )
        if resp.status_code >= 400:
            raise RuntimeError(f"LLM HTTP {resp.status_code}: {resp.text[:300]}")
        data = resp.json()

    usage = data.get("usage") or {}
    if usage:
        try:
            from app.common import token_usage

            token_usage.record(
                model=body["model"],
                prompt_tokens=int(usage.get("prompt_tokens") or 0),
                completion_tokens=int(usage.get("completion_tokens") or 0),
                scene="diagnose",
            )
        except Exception:  # noqa: BLE001
            pass

    return data["choices"][0]["message"]


# ---------------------------------------------------------------------------
# 确定性兜底诊断（LLM 不可用时）
# ---------------------------------------------------------------------------

def rule_based_diagnosis(ad_id: str) -> dict:
    """纯规则诊断：不调模型，保证功能不消失。"""
    ad = _tool_get_ad_detail(ad_id)
    if "error" in ad:
        return {"diagnosis": f"无法诊断：{ad['error']}", "fallback": True, "tool_calls": []}

    base = _tool_get_platform_baseline(ad.get("platform") or "")
    plat_ctr = (base.get("platform_baseline") or {}).get("ctr")
    ctr = ad.get("ctr")
    imp = ad.get("impressions") or 0

    if imp < MIN_IMPRESSIONS:
        verdict = f"样本不足（曝光 {imp} < {MIN_IMPRESSIONS}），不建议据此改素材"
    elif ctr is None or plat_ctr in (None, 0):
        verdict = "缺少基线数据，无法判断是否偏低"
    elif ctr < plat_ctr * LOW_CTR_RATIO:
        verdict = (f"偏低：CTR {ctr:.2%} 低于平台基线 {plat_ctr:.2%} 的 "
                   f"{LOW_CTR_RATIO:.0%}（阈值 {plat_ctr * LOW_CTR_RATIO:.2%}）")
    else:
        verdict = f"正常：CTR {ctr:.2%}，未低于平台基线 {plat_ctr:.2%} 的 {LOW_CTR_RATIO:.0%}"

    tips = []
    if ad.get("violations"):
        tips.append(f"素材有 {len(ad['violations'])} 处违禁词命中，先处理合规")
    if ad.get("budget") is None:
        tips.append("未设置预算，投放范围可能未收敛")
    tips.append("建议人工复核：标题前 3 秒/首屏图的吸引力")

    return {
        "diagnosis": f"【规则诊断】{verdict}\n建议：" + "；".join(tips),
        "fallback": True,
        "tool_calls": [],
        "metrics": {"ctr": ctr, "platform_ctr": plat_ctr, "impressions": imp},
    }


# ---------------------------------------------------------------------------
# 主入口：LLM 工具调用循环
# ---------------------------------------------------------------------------

def diagnose(ad_id: str, *, max_steps: int = MAX_STEPS) -> dict:
    """让模型自主调用只读工具做诊断。

    返回：
        {"diagnosis": str, "tool_calls": [...], "steps": int,
         "fallback": bool, "model": str}
    """
    if not ad_id:
        raise ValueError("ad_id 不能为空")

    # 先把素材自身信息拿到，作为初始上下文（避免模型第一步就盲调）
    seed = _tool_get_ad_detail(ad_id)
    if "error" in seed:
        return {"diagnosis": f"无法诊断：{seed['error']}", "fallback": True,
                "tool_calls": [], "steps": 0}

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user",
         "content": (f"请诊断素材 {ad_id} 的投放效果。\n"
                     f"已知信息：{json.dumps(seed, ensure_ascii=False)}\n"
                     f"请按需要调用工具补充数据，然后给出结论。")},
    ]

    tool_calls_log: list[dict[str, Any]] = []
    steps = 0

    try:
        for _ in range(max_steps):
            msg = _call_llm_with_tools(messages, TOOLS_SCHEMA)
            calls = msg.get("tool_calls") or []
            if not calls:
                text = (msg.get("content") or "").strip()
                if not text:
                    raise RuntimeError("模型既没调工具也没给答案")
                return {
                    "diagnosis": text,
                    "tool_calls": tool_calls_log,
                    "steps": steps + 1,
                    "fallback": False,
                    "model": os.getenv("LLM_MODEL", "deepseek-chat"),
                }

            steps += 1
            messages.append({"role": "assistant", "content": msg.get("content") or "",
                             "tool_calls": calls})

            for call in calls:
                fn = (call.get("function") or {})
                name = fn.get("name") or ""
                raw_args = fn.get("arguments") or "{}"
                try:
                    args = json.loads(raw_args) if isinstance(raw_args, str) else (raw_args or {})
                except json.JSONDecodeError:
                    args = {}

                impl = _TOOL_IMPL.get(name)
                if impl is None:
                    result = {"error": f"未知工具 {name}"}
                else:
                    try:
                        result = impl(**(args if isinstance(args, dict) else {}))
                    except TypeError as exc:
                        result = {"error": f"参数错误: {exc}"}
                    except Exception as exc:  # noqa: BLE001
                        result = {"error": f"{type(exc).__name__}: {exc}"}

                tool_calls_log.append({"name": name, "args": args, "ok": "error" not in result})
                messages.append({
                    "role": "tool",
                    "tool_call_id": call.get("id") or f"call_{steps}_{name}",
                    "content": json.dumps(result, ensure_ascii=False)[:4000],
                })

        # 轮次用尽：让模型基于已有信息收口
        messages.append({"role": "user",
                         "content": "工具调用轮次已达上限，请基于现有信息直接给出结论。"})
        msg = _call_llm_with_tools(messages, TOOLS_SCHEMA)
        text = (msg.get("content") or "").strip()
        return {
            "diagnosis": text or "（模型未给出结论）",
            "tool_calls": tool_calls_log,
            "steps": steps + 1,
            "fallback": False,
            "truncated": True,
            "model": os.getenv("LLM_MODEL", "deepseek-chat"),
        }

    except Exception as exc:  # noqa: BLE001
        print(f"[diagnose] LLM 工具调用失败（退回规则诊断）: {exc}", file=sys.stderr)
        out = rule_based_diagnosis(ad_id)
        out["error"] = f"{type(exc).__name__}: {exc}"
        out["steps"] = steps
        return out
