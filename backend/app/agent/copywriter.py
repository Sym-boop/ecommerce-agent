"""
爆款文案 Agent —— 结合卖点 + 向量检索 + LLM 生成多平台图文草稿
依赖：
  - B 的 milvus_cli（向量召回参考文案）
  - 大模型 API（通过 .env 配置，支持 DeepSeek / OpenAI 兼容协议）
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
# 保证 backend/ 在 path 上，使 `app.*` 在任意启动目录都可导入（单一模块实例）
_BACKEND = Path(__file__).resolve().parents[2]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

try:
    from dotenv import load_dotenv

    load_dotenv(_ROOT / ".env", override=True)
except Exception:
    pass

from pydantic import BaseModel

# ============================================================
# 一、Pydantic 输入 / 输出模型
# ============================================================
class CopyWriterInput(BaseModel):
    product_name: str
    product_sell_points: List[str]
    product_category: str
    platform: str  # xiaohongshu | tiktok | amazon
    language: str = "zh-CN"
    reference_count: int = 3


class CopyWriterResult(BaseModel):
    title: str
    content: str
    tags: List[str]
    platform: str
    language: str
    forbidden_hit: List[str]
    reference_snippets: List[str]


# ============================================================
# 二、违禁词过滤（统一走 app.common.banned_words，热加载）
# ============================================================
# 说明：这里原先自己维护了一份词表 + `word in text` 子串匹配，与
# app.common.banned_words 形成两套实现，两边口径不一致（单字误报、
# 冗余命中都只修一处会漏）。现统一委托给 banned_words，只保留
# `_FORBIDDEN_WORDS` 作为可读的词表快照。
_FORBIDDEN_WORDS: List[str] = []


def _load_forbidden_words() -> None:
    """刷新本地词表快照（真正的加载与匹配在 app.common.banned_words）。"""
    global _FORBIDDEN_WORDS
    try:
        from app.common.banned_words import reload as _reload
        from app.common.banned_words import words as _words

        _reload()
        _FORBIDDEN_WORDS = _words()
    except Exception as exc:  # noqa: BLE001
        print(f"[copywriter] 违禁词表加载失败: {exc}", file=sys.stderr)
        _FORBIDDEN_WORDS = []


_load_forbidden_words()


def filter_forbidden_words(text: str) -> List[str]:
    """返回文本中命中的违禁词列表（分词 + 词边界 + 最长优先去重）。

    strict=False：生成阶段只做提示性检测，词表不可用时返回空列表，
    真正的准入判定由 pipeline.check_copy（fail-closed）负责。
    """
    from app.common.banned_words import filter_words

    return filter_words(text)


def reload_forbidden_words() -> None:
    """支持热更新违禁词表（无需重启服务）。"""
    _load_forbidden_words()


# ============================================================
# 三、向量检索（对接 B 的 milvus_cli）
# ============================================================
def judge_relevance(category: str, references: List[dict[str, Any]],
                    *, timeout: float = 20.0) -> Dict[str, Any]:
    """用 LLM 复核召回结果是否真的与目标类目相关（第二级拒识）。

    **为什么在阈值之后还要 LLM 复核**

    相似度阈值是**词面/向量层面**的过滤：它能挡住"完全不沾边"的，
    但挡不住"字面像、其实不是同一类"的情况。LLM 能读懂语义，判得更准。

    **代价与边界**

    这是一次额外的模型调用，会多花 token、多 1–2 秒。所以设计成
    **两级**：先用便宜的阈值粗筛（`RETRIEVAL_MIN_SCORE`），只剩少量候选
    时才让 LLM 复核。全局开关 `RAG_LLM_JUDGE`（默认开），失败则降级为
    "全部保留"——**复核失败不能反过来把正常召回归零**。
    """
    if not references:
        return {"kept": [], "dropped": [], "judged": False, "reason": "无候选"}

    if os.getenv("RAG_LLM_JUDGE", "1").strip() not in ("1", "true", "True", "yes"):
        return {"kept": references, "dropped": [], "judged": False,
                "reason": "RAG_LLM_JUDGE 已关闭"}

    lines = []
    for i, ref in enumerate(references):
        title = str(ref.get("original_title") or "")
        text = str(ref.get("text") or "")[:200]
        lines.append(f"[{i}] 类目:{ref.get('category', '')} 平台:{ref.get('platform', '')} "
                     f"标题:{title} 正文:{text}")
    prompt = (
        f"目标商品类目：{category}\n\n"
        f"以下是待判断的参考文案：\n" + "\n".join(lines) + "\n\n"
        "请判断每一条是否与目标类目属于**同一商品品类**（可用于参考该品类的文案写法）。"
        "只输出 JSON，格式：{\"relevant\": [下标数组]}，不要输出其他内容。"
    )

    try:
        import json as _json

        raw = _call_llm(
            [{"role": "system",
              "content": "你是电商文案检索的相关性审核员。只输出 JSON。"},
             {"role": "user", "content": prompt}],
            temperature=0.0,
        )
        # 容错：模型可能包 ```json 或带前言，取第一个花括号块
        start, end = raw.find("{"), raw.rfind("}")
        if start < 0 or end <= start:
            raise ValueError(f"模型未返回 JSON: {raw[:120]}")
        parsed = _json.loads(raw[start:end + 1])
        idxs = parsed.get("relevant")
        if not isinstance(idxs, list):
            raise ValueError("relevant 字段缺失或类型错误")
        keep = {int(i) for i in idxs if isinstance(i, (int, float)) or str(i).isdigit()}
        kept = [r for i, r in enumerate(references) if i in keep]
        dropped = [r for i, r in enumerate(references) if i not in keep]
        return {"kept": kept, "dropped": dropped, "judged": True,
                "reason": f"LLM 保留 {len(kept)}/{len(references)}"}
    except Exception as exc:  # noqa: BLE001
        # 复核失败 → 保持全部候选（不能因为判官挂了就把正常召回归零）
        print(f"[copywriter] LLM 相关性复核失败（保留全部候选）: {exc}", file=sys.stderr)
        return {"kept": references, "dropped": [], "judged": False,
                "reason": f"复核失败已降级: {type(exc).__name__}"}


def retrieve_with_judge(category: str, platform: str, top_k: int = 3) -> Dict[str, Any]:
    """召回 + LLM 相关性复核，返回候选与复核结果。

    两级拒识：
    1. **阈值**（`RETRIEVAL_MIN_SCORE`）——便宜的确定性过滤，挡掉完全不沾边的
    2. **LLM 复核**（`RAG_LLM_JUDGE`）——读语义，挡掉"字面像但不是一个品类"的

    返回：
        {"references": [...], "judge": {"judged": bool, "kept": n, "dropped": n, "reason": str}}
    """
    refs = retrieve_similar_copy(category, platform, top_k=top_k)
    if not refs:
        return {"references": [], "judge": {"judged": False, "kept": 0,
                                            "dropped": 0, "reason": "阈值过滤后无候选"}}

    verdict = judge_relevance(category, refs)
    kept = verdict["kept"]
    return {
        "references": kept,
        "judge": {
            "judged": verdict["judged"],
            "kept": len(kept),
            "dropped": len(verdict["dropped"]),
            "reason": verdict["reason"],
            # 全部被判定不相关时，等同于「拒识」
            "rejected_all": bool(verdict["judged"] and not kept),
        },
    }


def retrieve_similar_copy(category: str, platform: str, top_k: int = 3) -> List[dict[str, Any]]:
    """从向量库召回与类目相关的爆款文案，并优先同平台。

    返回: [{"text": str, "platform": str, "category": str, "original_title": str, ...}]
    失败时 fallback 到空列表（不阻断流程）。

    **为什么不再按平台硬过滤**

    实测（`scripts/evaluate_quality.py`，87 条样本 / 28 类目 / 84 个类目×平台组合）：

        平台硬过滤    Recall@3=1.00  Precision@3=0.0345  MRR=0.052
        不硬过滤      Recall@5=0.9885 Precision@5=0.6782 MRR=0.839

    原因是语料里「类目×平台」平均只有约 1 条，硬过滤后向量库只能返回
    **同平台但不同类目**的文案当参考 —— 等于往 Prompt 里塞错品类的示范，
    反而拉低生成质量。

    改成**软偏好**：先按类目跨平台多召回一些，再把同平台的排到前面。
    既保住了平台风格偏好，又不会因语料稀疏拿到无关参考。
    """
    try:
        from app.common.milvus_cli import search  # type: ignore

        need = max(top_k * 3, 9)
        candidates = search(query_text=category, top_k=need, filters=None)
        if not candidates:
            return []

        plat = str(platform or "").strip()
        same = [d for d in candidates if str(d.get("platform", "")).strip() == plat]
        other = [d for d in candidates if str(d.get("platform", "")).strip() != plat]
        # 同平台优先，但各自内部保持相似度降序（search 已排序）
        ordered = same + other

        # 第三段：Cross-Encoder 精排。
        #
        # 为什么放在同平台优先之后：先保住平台风格偏好（这是业务约束），
        # 再在同一批候选里用 query-doc 交互重新排相关性。
        # 向量召回是双塔、各自编码后算余弦，丢掉了 query 与 doc 的细粒度交互；
        # Cross-Encoder 把两者拼起来过一遍模型，精度更高、代价是每条候选一次前向。
        try:
            from app.common import rerank as rerank_mod

            out = rerank_mod.rerank(category, ordered, top_k=top_k,
                                     prefer_platform=plat)
            return out["docs"]
        except Exception as exc:  # noqa: BLE001
            print(f"[copywriter] 精排异常（保持召回顺序）: {exc}", file=sys.stderr)
            return ordered[:top_k]
    except Exception as exc:  # noqa: BLE001
        print(f"[copywriter] 向量检索失败（回退到空参考）: {exc}", file=sys.stderr)
        return []


def _extract_snippets(docs: List[dict[str, Any]]) -> List[str]:
    """从检索结果中提取纯文本片段供 prompt 使用。"""
    snippets: List[str] = []
    for doc in docs:
        text = doc.get("text", "")
        title = doc.get("original_title", "")
        if title and title != text:
            snippets.append(f"[{title}] {text}")
        elif text:
            snippets.append(text)
    return snippets


# ============================================================
# 四、多平台 Prompt 模板
# ============================================================
PLATFORM_PROMPTS: dict[str, str] = {
    "xiaohongshu": """你是小红书种草博主，擅长用真实体验感极强的口语化风格推荐好物。
要求：
- 标题要有冲击力，善用数字和感叹，但不能使用违禁词（如"最"、"第一"、"100%"等）
- 正文用第一人称真实体验叙述，带 emoji，分 3-5 段，每段 2-3 句
- 结尾输出 3-5 个相关话题标签（#开头，空格分隔）
- 严禁使用违禁词，如有违禁词会直接导致审核不通过

输出格式（严格按以下格式，不要输出其他内容）：
标题：xxx
正文：xxx
标签：#xxx #xxx #xxx""",

    "tiktok": """你是 TikTok 短视频脚本创作者，擅长用 hook-痛点-解决方案-行动号召的结构写脚本。
要求：
- 开头 3 秒必须有强 hook（悬念/反常识/数字冲击）
- 脚本结构：Hook（吸引眼球）→ Problem（痛点共鸣）→ Solution（产品解决）→ CTA（引导互动/购买）
- 语言口语化，短句为主，适合配音朗读
- 使用适量 emoji，但不宜过多
- 结尾引导点赞关注或点击链接

输出格式（严格按以下格式，不要输出其他内容）：
Hook：xxx
Script：xxx
CTA：xxx
Hashtags：#xxx #xxx #xxx""",

    "amazon": """你是亚马逊（Amazon）专业产品 Listing 撰写师，擅长 SEO 优化和转化提升。
要求：
- 标题：品牌名 + 核心关键词 + 关键特性，控制在 150 字符以内，自然融入搜索关键词
- 五点描述（Bullet Points）：5 条，每条以大写关键词开头，突出卖点和使用场景，长度 100-200 字符
- 产品描述：100-200 词，叙述性描述，强调用户体验和差异化优势
- 不使用违禁词（如"best"、"guarantee"、"100%"等）
- 纯英文输出

输出格式（严格按以下格式，不要输出其他内容）：
Title: xxx
Bullet Points:
1. xxx
2. xxx
3. xxx
4. xxx
5. xxx
Product Description: xxx""",
}

DEFAULT_PLATFORM = "xiaohongshu"


def build_prompt(input_data: CopyWriterInput, references: List[dict[str, Any]]) -> str:
    """根据平台生成对应的 system prompt + user prompt。"""
    plat = input_data.platform or DEFAULT_PLATFORM
    system_template = PLATFORM_PROMPTS.get(plat, PLATFORM_PROMPTS[DEFAULT_PLATFORM])

    snippets = _extract_snippets(references)
    ref_text = "\n".join(f"- {s}" for s in snippets) if snippets else "（暂无参考文案）"

    sell_points_text = "、".join(input_data.product_sell_points)

    user_prompt = (
        f"商品名称：{input_data.product_name}\n"
        f"商品卖点：{sell_points_text}\n"
        f"商品类目：{input_data.product_category}\n"
        f"目标平台：{plat}\n"
        f"目标语言：{input_data.language}\n\n"
        f"参考爆款文案：\n{ref_text}\n\n"
        f"请根据以上信息，按照要求生成文案。"
    )

    return f"{system_template}\n\n---\n\n{user_prompt}"


# ============================================================
# 五、LLM 调用（兼容 DeepSeek / OpenAI 协议）
# ============================================================
LLM_API_KEY = os.getenv("LLM_API_KEY", "").strip()
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://api.deepseek.com").rstrip("/")
LLM_MODEL = os.getenv("LLM_MODEL", "deepseek-chat").strip()

_CHAT_COMPLETIONS_URLS: dict[str, str] = {
    "deepseek": "https://api.deepseek.com/v1/chat/completions",
}


def _chat_url() -> str:
    if LLM_BASE_URL in _CHAT_COMPLETIONS_URLS:
        return _CHAT_COMPLETIONS_URLS[LLM_BASE_URL]
    base = LLM_BASE_URL
    if base.endswith("/v1"):
        return f"{base}/chat/completions"
    return f"{base}/v1/chat/completions"


def _call_llm(messages: List[dict[str, str]], temperature: float = 0.7) -> str:
    """调用 OpenAI 兼容协议的 LLM，返回文本内容（带指标与成本统计）。"""
    from app.common import metrics

    if not LLM_API_KEY or LLM_API_KEY.startswith("sk-xxxx"):
        raise RuntimeError(
            "未配置有效 LLM_API_KEY，请在项目根目录 .env 中设置（参考 .env.example）"
        )

    try:
        import httpx
    except ImportError as exc:
        raise RuntimeError("需要 httpx：pip install httpx") from exc

    url = _chat_url()
    headers = {
        "Authorization": f"Bearer {LLM_API_KEY}",
        "Content-Type": "application/json",
    }
    body: dict[str, Any] = {
        "model": LLM_MODEL,
        "messages": messages,
        "temperature": temperature,
    }

    with metrics.track("llm_call") as t:
        with httpx.Client(timeout=60.0) as client:
            resp = client.post(url, headers=headers, json=body)
            if resp.status_code >= 400:
                raise RuntimeError(
                    f"LLM 调用失败 HTTP {resp.status_code}: {resp.text[:500]}"
                )
            data = resp.json()

        # 记录真实 token 用量（服务商返回 usage 时），用于成本估算
        usage = data.get("usage") or {}
        if usage:
            pt = int(usage.get("prompt_tokens") or 0)
            ct = int(usage.get("completion_tokens") or 0)
            t.tokens(input=pt, output=ct)
            # 再落一本按日/按月可聚合的账本：
            # metrics 是进程内累加（重启归零、无时间维度），答不了「这个月用了多少」
            try:
                from app.common import token_usage

                token_usage.record(model=LLM_MODEL, prompt_tokens=pt,
                                   completion_tokens=ct, scene="copywriter")
            except Exception as exc:  # noqa: BLE001
                print(f"[copywriter] token 账本记录失败: {exc}", file=sys.stderr)

    try:
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError(f"LLM 返回格式异常: {data}") from exc


def _parse_llm_output(raw: str, platform: str) -> dict[str, Any]:
    """从 LLM 原始输出中解析各平台格式字段。"""
    raw = (raw or "").strip()
    result: dict[str, Any] = {}

    if platform == "xiaohongshu" or platform == "tiktok":
        # 中文平台：按标签行解析
        title_m = re.search(r"标题[：:]\s*(.+)", raw)
        if title_m:
            result["title"] = title_m.group(1).strip()
        else:
            # 取第一行作为标题
            first_line = raw.split("\n")[0].strip()
            result["title"] = first_line[:80]

        # 正文：标题行之后、标签行之前的内容（去除"正文："等前缀）
        lines = raw.split("\n")
        content_lines: List[str] = []
        in_content = False
        for line in lines:
            stripped = line.strip()
            if re.match(r"^标题[：:]", stripped):
                in_content = True
                continue
            if re.match(r"^(标签|标签[：:])", stripped) or re.match(r"Hashtags[：:]", stripped):
                break
            if in_content:
                # 去除"正文："等引导前缀
                cleaned = re.sub(r"^(正文|内容|Content)[：:]\s*", "", stripped)
                content_lines.append(cleaned)
        result["content"] = "\n".join(content_lines).strip() if content_lines else raw

        # 标签
        tag_m = re.search(r"(?:标签|Hashtags)[：:]\s*(.+)", raw)
        if tag_m:
            tags_text = tag_m.group(1).strip()
            result["tags"] = [t.strip() for t in re.split(r"\s+", tags_text) if t.strip()]
        else:
            result["tags"] = []

    elif platform == "amazon":
        # 英文平台：分段解析
        title_m = re.search(r"Title[：:]\s*(.+)", raw, re.IGNORECASE)
        if title_m:
            result["title"] = title_m.group(1).strip()[:150]
        else:
            first_line = raw.split("\n")[0].strip()
            result["title"] = first_line[:150]

        bullet_m = re.findall(r"\d+\.\s*(.+)", raw)
        result["content"] = "\n".join(
            f"{i+1}. {b}" for i, b in enumerate(bullet_m[:5])
        ) if bullet_m else raw[:500]
        result["tags"] = []

    else:
        # 默认：全文作为 content，标题取第一行
        lines = raw.split("\n")
        result["title"] = lines[0].strip()[:80] if lines else raw[:80]
        result["content"] = raw
        result["tags"] = []

    # 兜底：确保必填字段不为空
    result.setdefault("title", "（生成失败，请检查 LLM 配置）")
    result.setdefault("content", raw)
    result.setdefault("tags", [])
    return result


# ============================================================
# 六、主入口函数（供 Supervisor 调度）
# ============================================================
def generate_copy(input_data: CopyWriterInput) -> CopyWriterResult:
    """
    主流程：召回参考文案 → 构建 prompt → LLM 生成 → 违禁词检测 → 返回结果。
    若 LLM 调用失败，自动 fallback 到模板文案（保证不返回空结果）。
    """
    from app.common import metrics

    # 1. 召回参考文案（向量检索，失败降级为空参考）
    with metrics.track("rag_retrieval") as rag:
        outcome = retrieve_with_judge(
            category=input_data.product_category,
            platform=input_data.platform or DEFAULT_PLATFORM,
            top_k=input_data.reference_count,
        )
        references = outcome["references"]
        judge = outcome["judge"]
        if judge.get("judged"):
            metrics.incr("rag_llm_judged")
        if judge.get("dropped"):
            metrics.incr("rag_llm_dropped", judge["dropped"])
        if judge.get("rejected_all"):
            metrics.incr("rag_rejected_by_llm")
        if not references:
            rag.degraded()
            metrics.incr("rag_empty_fallback")
    reference_snippets = _extract_snippets(references)

    # 2. 构建 prompt
    prompt = build_prompt(input_data, references)

    # 3. 调用 LLM（失败时 fallback）
    llm_output: str = ""
    try:
        messages = [{"role": "user", "content": prompt}]
        llm_output = _call_llm(messages)
    except Exception as exc:  # noqa: BLE001
        # 静默降级是历史坑：日志只有一行 warning，很容易以为功能正常。
        # 这里同时打指标（degradation_rate），让"没调通"变得可观测。
        from app.common import metrics

        metrics.incr("llm_degraded_to_template")
        print(f"[copywriter] LLM 调用失败，使用模板文案: {exc}", file=sys.stderr)
        llm_output = ""

    # 4. 解析 LLM 输出 / 降级到模板
    if llm_output.strip():
        parsed = _parse_llm_output(llm_output, input_data.platform or DEFAULT_PLATFORM)
        out_title = parsed.get("title", "")
        out_content = parsed.get("content", "")
        out_tags = parsed.get("tags", [])
    else:
        plat = input_data.platform or DEFAULT_PLATFORM
        if plat == "tiktok":
            out_title = f"POV: {input_data.product_name} changed everything 🤯"
            out_content = (
                f"This is the {input_data.product_name} and I'm obsessed.\n"
                f"{' '.join(input_data.product_sell_points)}\n"
                f"Link in bio!"
            )
            out_tags = [f"#{input_data.product_category}", "#AmazonFinds", "#TikTokMadeMeBuyIt"]
        elif plat == "amazon":
            out_title = f"{input_data.product_name} - {input_data.product_category} | " + " | ".join(
                input_data.product_sell_points[:3]
            )
            out_content = "\n".join(
                f"{i+1}. {sp}" for i, sp in enumerate(input_data.product_sell_points)
            )
            out_tags = [f"#{input_data.product_category}", "#AmazonFind"]
        else:
            out_title = f"{input_data.product_name}｜真的太好用啦✨"
            out_content = (
                f"实测这款{input_data.product_name}！\n"
                + "；".join(input_data.product_sell_points)
                + "\n日常使用很合适～"
            )
            out_tags = ["#好物分享", f"#{input_data.product_category}"]

    full_text = out_title + out_content

    # 5. 违禁词检测
    forbidden_hit = filter_forbidden_words(full_text)

    return CopyWriterResult(
        title=out_title,
        content=out_content,
        tags=out_tags,
        platform=input_data.platform,
        language=input_data.language,
        forbidden_hit=forbidden_hit,
        reference_snippets=reference_snippets,
    )


# ============================================================
# 七、Supervisor 调度入口（统一 dict 格式）
# ============================================================
import re

def copywriter_node(state: dict) -> dict:
    try:
        product_name = state.get("product_name", "")
        sell_points = state.get("product_sell_points", [])
        category = state.get("product_category", "")
        platform = state.get("platform", "xiaohongshu")
        language = state.get("language", "zh-CN")
        ref_count = int(state.get("reference_count", 3))
        goal = state.get("goal", "")

        # 兜底：从 goal 提取
        if not product_name:
            m = re.search(r"新品\s*([^\s，,。的]+)", goal)
            product_name = m.group(1) if m else (goal[:20] or "新品")

        if not category:
            if any(k in goal for k in ["连衣裙", "裙子", "女装"]):
                category = "女装"
            elif any(k in goal for k in ["保温杯", "水杯", "杯子"]):
                category = "水杯"
            elif any(k in goal for k in ["T恤", "衬衫", "衣服"]):
                category = "服装"
            else:
                category = "通用"

        # 保留原校验（现在应该不会触发了）
        if not product_name or not category:
            return {
                "status": "failed",
                "error": "product_name 和 product_category 不能为空",
            }

        input_data = CopyWriterInput(
            product_name=product_name,
            product_sell_points=sell_points,
            product_category=category,
            platform=platform,
            language=language,
            reference_count=ref_count,
        )
        result = generate_copy(input_data)
        return {
            "copy_result": result.model_dump(),
            "status": "done",
            "forbidden_hit": result.forbidden_hit,
            "reference_snippets": result.reference_snippets,
        }
    except Exception as exc:  # noqa: BLE001
        return {"status": "failed", "error": f"文案 Agent 失败: {exc}"}


# ============================================================
# 八、本地自测入口
# ============================================================
if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")

    test_cases = [
        CopyWriterInput(
            product_name="316不锈钢保温杯",
            product_sell_points=["24小时保温", "轻便", "密封性好"],
            product_category="水杯",
            platform="xiaohongshu",
        ),
        CopyWriterInput(
            product_name="无线蓝牙耳机",
            product_sell_points=["主动降噪", "40小时续航", "IPX5防水"],
            product_category="蓝牙耳机",
            platform="tiktok",
        ),
        CopyWriterInput(
            product_name="Memory Foam Pillow",
            product_sell_points=["Ergonomic design", "Cooling gel", "Hypoallergenic"],
            product_category="枕头",
            platform="amazon",
        ),
    ]

    for tc in test_cases:
        print(f"\n{'='*60}")
        print(f"平台: {tc.platform} | 商品: {tc.product_name}")
        print("=" * 60)
        out = generate_copy(tc)
        print(json.dumps(out.model_dump(), ensure_ascii=False, indent=2))
