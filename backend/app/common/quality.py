"""生成质量评估（客观指标 + 可选 LLM 自动评分）。

**为什么需要**

原先没有任何质量度量，Prompt 改好改坏只能"感觉"。
要让优化变成**可度量的迭代**，必须先有指标。

**三层指标**

1. **客观指标（全自动、零成本）**——本模块默认计算：
   - `format_ok`：标题 / 正文 / 标签三要素是否齐全（格式解析成功率）
   - `violation_free`：是否零违禁词命中
   - `length_ok`：正文长度是否落在平台合理区间
   - `sell_point_coverage`：输入卖点在文案中的**覆盖率**（相关性代理指标）
   - `tag_count_ok`：标签数量是否在 3~5 个（小红书风格要求）
2. **LLM 自动评分（可选，需显式开启）**：用大模型按「可读性 / 说服力 / 平台适配度」打分。
   **默认关闭**，因为它会产生真实 API 成本；开启后用 `LLM_JUDGE_MODEL` 指定评审模型
   （建议与被评模型不同，降低自评偏差）。
3. **人工基线（离线）**：抽样人评，用于校准 1 和 2 的口径——本模块只留出记录字段，
   不做实现（需要真实标注流程）。

**为什么用「卖点覆盖率」当相关性指标**

没有 ground truth 的情况下，最便宜可靠的相关性代理就是：
**输入给模型的卖点，有多少真的出现在输出里**。
覆盖率低通常意味着模型跑偏了（写了很多但没讲卖点），这是很实用的告警信号。
"""
from __future__ import annotations

import logging
import os
import re
from typing import Any, Optional

logger = logging.getLogger("uvicorn.error")

# 平台正文长度合理区间（字符数）
LENGTH_RANGE = {
    "xiaohongshu": (80, 1200),
    "tiktok": (60, 1500),
    "amazon": (200, 4000),
    "default": (40, 2000),
}

TAG_RANGE = (3, 5)


def _platform_range(platform: str) -> tuple[int, int]:
    return LENGTH_RANGE.get((platform or "").lower(), LENGTH_RANGE["default"])


def evaluate(*, title: str = "", content: str = "", tags: Optional[list] = None,
             platform: str = "xiaohongshu", sell_points: Optional[list] = None,
             violations: Optional[list] = None,
             reference_count: int = 0) -> dict[str, Any]:
    """对一条生成结果做客观质量评估，返回指标明细与总分（0~100）。

    纯函数、无副作用、零 API 成本，可在每次生成后调用。
    """
    title = (title or "").strip()
    content = (content or "").strip()
    tags = [str(t).strip() for t in (tags or []) if str(t).strip()]
    points = [str(p).strip() for p in (sell_points or []) if str(p).strip()]
    violations = violations or []

    text = f"{title}\n{content}"
    lo, hi = _platform_range(platform)

    # 1) 格式完整性：三要素齐全
    format_ok = bool(title) and bool(content) and len(tags) > 0

    # 2) 合规：零违禁词
    violation_free = len(violations) == 0

    # 3) 长度合规
    length_ok = lo <= len(content) <= hi

    # 4) 卖点覆盖率（相关性代理）
    covered = [p for p in points if p and p in text]
    coverage = round(len(covered) / len(points), 4) if points else 1.0

    # 5) 标签数量（小红书风格要求 3~5 个）
    tag_count_ok = TAG_RANGE[0] <= len(tags) <= TAG_RANGE[1] if platform == "xiaohongshu" else len(tags) > 0

    # 6) 是否有 emoji（小红书/TikTok 风格信号，仅作参考不计分）
    emoji_count = len(re.findall(r"[\U0001F300-\U0001FAFF\u2600-\u27BF]", text))

    # 7) 段落数（可读性代理）
    paragraphs = len([p for p in re.split(r"\n+", content) if p.strip()])

    # 加权总分：合规最重（一票否决式扣分），其次格式，其次相关性
    score = 100.0
    if not violation_free:
        score -= 40
    if not format_ok:
        score -= 25
    if not length_ok:
        score -= 10
    if not tag_count_ok:
        score -= 5
    # 覆盖率不足按比例扣分（最多扣 20）
    score -= round((1 - coverage) * 20, 2)
    score = max(0.0, min(100.0, round(score, 1)))

    return {
        "score": score,
        "platform": platform,
        "metrics": {
            "format_ok": format_ok,
            "violation_free": violation_free,
            "length_ok": length_ok,
            "tag_count_ok": tag_count_ok,
            "length": len(content),
            "length_range": [lo, hi],
            "tag_count": len(tags),
            "sell_point_coverage": coverage,
            "sell_points_covered": covered,
            "sell_points_missing": [p for p in points if p not in covered],
            "paragraphs": paragraphs,
            "emoji_count": emoji_count,
            "reference_count": reference_count,
        },
    }


# ---------------------------------------------------------------------------
# LLM 自动评分（可选，默认关闭）
# ---------------------------------------------------------------------------

JUDGE_PROMPT = """你是资深电商广告审核专家。请对下面这条广告文案打分。

商品卖点：{points}
目标平台：{platform}

标题：{title}
正文：{content}

请从三个维度分别打分（1~10 分），只输出 JSON，不要输出其他内容：
{{"readability": 数字, "persuasiveness": 数字, "platform_fit": 数字, "comment": "一句话点评"}}"""


def judge_enabled() -> bool:
    """LLM 评分默认关闭——它会产生真实 API 成本，必须显式开启。"""
    return os.getenv("LLM_JUDGE_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}


def llm_judge(*, title: str = "", content: str = "", platform: str = "xiaohongshu",
              sell_points: Optional[list] = None) -> dict[str, Any]:
    """调用大模型做质量评分（需 LLM_JUDGE_ENABLED=true）。

    评审模型优先取 `LLM_JUDGE_MODEL`，否则复用 `LLM_MODEL`。
    任何失败都返回 `{"available": False, "reason": ...}`，不抛异常——
    质量评分是**辅助观测**，不应该影响主流程。
    """
    if not judge_enabled():
        return {"available": False, "reason": "LLM 评分未开启（设置 LLM_JUDGE_ENABLED=true）"}

    try:
        from app.agent import copywriter
        from app.common import metrics
    except Exception as exc:  # noqa: BLE001
        return {"available": False, "reason": f"依赖导入失败: {exc}"}

    judge_model = os.getenv("LLM_JUDGE_MODEL", "").strip() or getattr(copywriter, "LLM_MODEL", "")
    prompt = JUDGE_PROMPT.format(
        points="、".join(sell_points or []) or "（未提供）",
        platform=platform, title=title, content=content[:1500],
    )

    original_model = getattr(copywriter, "LLM_MODEL", "")
    try:
        with metrics.track("llm_judge") as t:
            if judge_model:
                copywriter.LLM_MODEL = judge_model  # 临时切换评审模型
            raw = copywriter._call_llm([{"role": "user", "content": prompt}], temperature=0.1)
            t.tokens(input=len(prompt) // 3, output=len(raw) // 3)  # 粗估 token
    except Exception as exc:  # noqa: BLE001
        return {"available": False, "reason": f"评审调用失败: {exc}"}
    finally:
        copywriter.LLM_MODEL = original_model

    import json

    match = re.search(r"\{.*\}", raw or "", re.S)
    if not match:
        return {"available": False, "reason": "评审返回无法解析", "raw": (raw or "")[:300]}
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return {"available": False, "reason": "评审返回非法 JSON", "raw": (raw or "")[:300]}

    dims = [data.get("readability"), data.get("persuasiveness"), data.get("platform_fit")]
    nums = [float(d) for d in dims if isinstance(d, (int, float))]
    avg = round(sum(nums) / len(nums), 2) if nums else None
    return {
        "available": True,
        "model": judge_model,
        "readability": data.get("readability"),
        "persuasiveness": data.get("persuasiveness"),
        "platform_fit": data.get("platform_fit"),
        "average": avg,
        "comment": data.get("comment", ""),
    }


# ---------------------------------------------------------------------------
# 汇总
# ---------------------------------------------------------------------------

def summarize(records: list[dict]) -> dict[str, Any]:
    """对一批评估结果做汇总（供质量看板）。"""
    if not records:
        return {"count": 0, "avg_score": 0, "by_platform": {}}

    by_platform: dict[str, dict[str, Any]] = {}
    for r in records:
        plat = (r.get("platform") or "unknown")
        bucket = by_platform.setdefault(plat, {"count": 0, "score_sum": 0.0, "violation_free": 0})
        bucket["count"] += 1
        bucket["score_sum"] += float(r.get("score") or 0)
        if (r.get("metrics") or {}).get("violation_free"):
            bucket["violation_free"] += 1

    for plat, b in by_platform.items():
        b["avg_score"] = round(b["score_sum"] / b["count"], 1)
        b["violation_free_rate"] = round(b["violation_free"] / b["count"], 3)
        b.pop("score_sum", None)

    scores = [float(r.get("score") or 0) for r in records]
    coverages = [float((r.get("metrics") or {}).get("sell_point_coverage") or 0) for r in records]
    return {
        "count": len(records),
        "avg_score": round(sum(scores) / len(scores), 1),
        "min_score": round(min(scores), 1),
        "max_score": round(max(scores), 1),
        "avg_sell_point_coverage": round(sum(coverages) / len(coverages), 3),
        "by_platform": by_platform,
    }
