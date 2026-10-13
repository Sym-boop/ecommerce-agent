"""广告生成主图（LangGraph `StateGraph`）。

后端编排自本次改造起统一改用 **LangGraph**（`langgraph`），不再依赖自研的顺序编排。

主图拓扑：

    START → select_product → gen_copy → review_copy ─┬─(命中违禁词)→ human_review ─┐
                                                     │                             │
                                                     └─(通过)──────────────────────┤
                                                                                   ▼
                                          (want_image) → gen_image ─(want_video)→ gen_video → finish → END

设计要点：
    - `review_copy` 后接条件边：命中违禁词的文案进入 `human_review` 节点，
      该节点用 `interrupt()` 暂停图执行，交由人工在「审查中心」处置；
      `resume()` 携带人工决定（approve / reject）恢复执行。
    - 使用 `InMemorySaver` 作为 checkpointer，保证同一 thread_id 的暂停 / 恢复可续跑。
    - 图文 / 视频节点以**已通过审查的文案**为输入，不再单独审查（仅文案审查）。
    - 图文 / 视频节点函数（`gen_image_node` / `gen_video_node`）同时对外暴露，
      供「重新生成图文 / 视频」的独立接口直接复用，避免两套实现。
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import TypedDict

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

logger = logging.getLogger("uvicorn.error")


# ---------------------------------------------------------------------------
# 图状态
# ---------------------------------------------------------------------------

class AdGenState(TypedDict, total=False):
    product: dict           # {name, category, sell_points}
    sell_points: list       # 归一化后的卖点
    keyword: str            # 用户补充关键词
    platform: str           # 目标平台
    input_image_path: str   # 可选：用户上传的商品图片（本地路径）
    copy: dict              # {title, content, tags, platform}
    review: dict            # {copy_ok, violations, overridden?}
    review_decision: str    # approved / rejected
    image: dict             # {url, path} 或 {error}
    video: dict             # {url, path} 或 {error}
    want_image: bool
    want_video: bool
    status: str             # copy_done / done / rejected / failed
    error: str


# ---------------------------------------------------------------------------
# 节点函数
# ---------------------------------------------------------------------------

def select_product(state: AdGenState) -> dict:
    """归一化商品信息（自定义商品与内置商品走同一入口）。"""
    product = state.get("product") or {}
    name = str(product.get("name") or "").strip()
    if not name:
        return {"status": "failed", "error": "未选择商品"}
    points = [str(s).strip() for s in (product.get("sell_points") or []) if str(s).strip()]
    if not points and product.get("category"):
        points = [str(product["category"])]
    return {
        "product": {
            "name": name,
            "category": str(product.get("category") or "通用"),
            "sell_points": points,
        },
        "sell_points": points,
    }


def gen_copy(state: AdGenState) -> dict:
    """调用文案 Agent 生成文案（商品与关键词不变时用于「重新生成」）。"""
    from app.agent.copywriter import CopyWriterInput, generate_copy

    product = state.get("product") or {}
    platform = str(state.get("platform") or "xiaohongshu")
    keyword = str(state.get("keyword") or "").strip()

    points = list(state.get("sell_points") or [])
    if keyword:
        points = points + [keyword]

    try:
        result = generate_copy(CopyWriterInput(
            product_name=product.get("name", ""),
            product_sell_points=points,
            product_category=product.get("category") or "通用",
            platform=platform,
        ))
    except Exception as exc:  # noqa: BLE001
        logger.warning("文案生成失败: %s", exc)
        return {"status": "failed", "error": f"文案生成失败：{exc}"}

    return {
        "copy": {
            "title": result.title,
            "content": result.content,
            "tags": list(result.tags),
            "platform": result.platform,
        },
        "status": "copy_done",
    }


def review_copy(state: AdGenState) -> dict:
    """文案审查：复用违禁词库（milvus_cli.check_banned_words）。"""
    from app.common.pipeline import check_copy

    copy = state.get("copy") or {}
    text = f"{copy.get('title') or ''} {copy.get('content') or ''}"
    violations = check_copy(text)
    return {"review": {"copy_ok": not violations, "violations": violations}}


def human_review(state: AdGenState) -> dict:
    """命中违禁词时暂停图执行，等待人工处置；恢复时携带 approve / reject。"""
    review = state.get("review") or {}
    decision = interrupt({
        "type": "copy_review",
        "product": (state.get("product") or {}).get("name"),
        "copy": state.get("copy"),
        "violations": review.get("violations") or [],
    })
    approved = (
        decision is True
        or decision == "approve"
        or (isinstance(decision, dict) and decision.get("action") == "approve")
    )
    if approved:
        return {
            "review_decision": "approved",
            "review": {**review, "copy_ok": True, "overridden": True},
        }
    return {"review_decision": "rejected"}


def gen_image_node(state: AdGenState) -> dict:
    """图文生成：以已通过审查的文案为输入，可选嵌入用户提供的商品图片。"""
    from app.agent.image_generator import generate_image

    copy = state.get("copy") or {}
    keyword = str(state.get("keyword") or "").strip()
    tags = list(copy.get("tags") or [])
    if keyword:
        tags = tags + [keyword]

    try:
        path = generate_image(
            title=copy.get("title", ""),
            content=copy.get("content", ""),
            tags=tags,
            input_image_path=state.get("input_image_path"),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("图文生成失败: %s", exc)
        return {"image": {"error": f"图文生成失败：{exc}"}}

    return {"image": {"url": f"/media/images/{Path(path).name}", "path": path}}


def gen_video_node(state: AdGenState) -> dict:
    """视频生成：以文案为脚本，基于商品图片（无图片时先按文案渲染首帧）生成 ~10s 短视频。"""
    from app.agent.image_generator import generate_image
    from app.agent.video_generator import generate_short_video

    copy = state.get("copy") or {}
    keyword = str(state.get("keyword") or "").strip()
    prompt = "；".join(p for p in [keyword, copy.get("title"), copy.get("content")] if p) \
        or "商品动态展示"

    image_path = state.get("input_image_path")
    try:
        if not image_path:
            # 未提供图片：先用文案渲染一张海报作为视频首帧
            image_path = generate_image(
                title=copy.get("title", ""),
                content=copy.get("content", ""),
                tags=list(copy.get("tags") or []),
            )
        path = generate_short_video(prompt=prompt, image_path=image_path)
    except Exception as exc:  # noqa: BLE001
        logger.warning("视频生成失败: %s", exc)
        return {"video": {"error": f"视频生成失败：{exc}"}}

    return {"video": {"url": f"/media/videos/{Path(path).name}", "path": path}}


def finish(state: AdGenState) -> dict:
    """收口：给出最终状态（rejected / done）。"""
    if state.get("status") == "failed":
        return {}
    if state.get("review_decision") == "rejected":
        return {"status": "rejected"}
    return {"status": "done"}


# ---------------------------------------------------------------------------
# 条件边
# ---------------------------------------------------------------------------

def route_after_copy(state: AdGenState) -> str:
    return "finish" if state.get("status") == "failed" else "review_copy"


def route_after_review(state: AdGenState) -> str:
    if not (state.get("review") or {}).get("copy_ok", True):
        return "human_review"
    return _next_asset(state)


def route_after_human(state: AdGenState) -> str:
    if state.get("review_decision") == "approved":
        return _next_asset(state)
    return "finish"


def route_after_image(state: AdGenState) -> str:
    return "gen_video" if state.get("want_video") else "finish"


def _next_asset(state: AdGenState) -> str:
    """审查通过后按用户勾选决定下游：图文优先，其次视频，最后收口。"""
    if state.get("want_image"):
        return "gen_image"
    if state.get("want_video"):
        return "gen_video"
    return "finish"


# ---------------------------------------------------------------------------
# 图编译
# ---------------------------------------------------------------------------

_CHECKPOINTER = InMemorySaver()


def build_graph():
    """构建并编译「广告生成主图」。"""
    graph = StateGraph(AdGenState)
    graph.add_node("select_product", select_product)
    graph.add_node("gen_copy", gen_copy)
    graph.add_node("review_copy", review_copy)
    graph.add_node("human_review", human_review)
    graph.add_node("gen_image", gen_image_node)
    graph.add_node("gen_video", gen_video_node)
    graph.add_node("finish", finish)

    graph.add_edge(START, "select_product")
    graph.add_edge("select_product", "gen_copy")
    graph.add_conditional_edges(
        "gen_copy", route_after_copy, {"review_copy": "review_copy", "finish": "finish"},
    )
    graph.add_conditional_edges(
        "review_copy", route_after_review,
        {"human_review": "human_review", "gen_image": "gen_image",
         "gen_video": "gen_video", "finish": "finish"},
    )
    graph.add_conditional_edges(
        "human_review", route_after_human,
        {"gen_image": "gen_image", "gen_video": "gen_video", "finish": "finish"},
    )
    graph.add_conditional_edges(
        "gen_image", route_after_image, {"gen_video": "gen_video", "finish": "finish"},
    )
    graph.add_edge("gen_video", "finish")
    graph.add_edge("finish", END)

    return graph.compile(checkpointer=_CHECKPOINTER)


GRAPH = build_graph()


# ---------------------------------------------------------------------------
# 对外调用封装
# ---------------------------------------------------------------------------

def _config(thread_id: str) -> dict:
    return {"configurable": {"thread_id": thread_id}}


def is_interrupted(result: dict) -> bool:
    """判断一次 invoke 是否因 `interrupt()` 暂停（等待人工审查）。"""
    return bool((result or {}).get("__interrupt__"))


def run_ad_generation(thread_id: str, **state) -> dict:
    """执行广告生成主图；命中违禁词时在 human_review 暂停并返回当前状态。"""
    return GRAPH.invoke(state, _config(thread_id))


def resume_ad_generation(thread_id: str, decision) -> dict:
    """携带人工审查决定恢复被暂停的图执行。"""
    return GRAPH.invoke(Command(resume=decision), _config(thread_id))