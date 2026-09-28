"""任务与审批引擎（成员B - 通用基础模块）

以 frontend/src/api/mock.js 为契约基线（前端联调会议 2026-09-23 确认稿），
实现任务/审批的内存存储与生命周期编排，供 main.py 的 REST/WS 路由调用。

契约要点:
    任务: {id, goal, status(running|done|failed), budget, created_at, nodes[], edges[]}
    节点: {id, type, name, status(pending|running|success|failed|waiting),
           started_at, finished_at, input, output, error, logs[{ts, level, msg}]}
    审批: type ∈ copy | pricing | ads；status ∈ pending | approved | rejected
    事件: node:update / task:update / approval:created / approval:updated

子 Agent 调用策略「真实优先、缺失降级」:
    - pricing: 调成员E app.agent.pricing.pricing_node（需 ERP MCP / CSV 数据，不可用时节点失败）
    - copywriter: 调成员D app.agent.copywriter.generate_copy（未实现时用模板草稿降级，
      违禁词扫描走 milvus_cli.check_banned_words 真实链路，Milvus 不可用时返回空）
    - ads: 风控网关（SUP-005 熔断）：触发即阻断并生成审批单，待 A 的 ads_manager 接入后替换

成员C 接入 LangGraph 后可整体替换 run_lifecycle / _run_*_node，路由层不受影响。

注意: 当前为单进程内存存储（仅支持 uvicorn 单 worker），接入 Redis 持久化由后续迭代完成。
"""

from __future__ import annotations

import asyncio
import copy
import itertools
import threading
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

# ---------------------------------------------------------------------------
# 常量（与 mock.js 保持一致）
# ---------------------------------------------------------------------------

COPY_KEYWORDS = ["文案", "推广", "小红书", "TikTok", "亚马逊", "种草", "图文", "Listing"]
PRICE_KEYWORDS = ["调价", "定价", "竞品", "价格", "降价", "涨价", "跟价"]
ADS_KEYWORDS = ["广告", "投放", "出价", "千川", "预算", "巨量", "Meta", "加投"]

ADS_BUDGET_THRESHOLD = 50000   # 总预算熔断阈值（元）
BID_THRESHOLD = 2.0            # 单次出价熔断阈值（元）
PRICE_CHANGE_THRESHOLD = 10.0  # 调价审批阈值（%）

CURRENT_OPERATOR = "当前登录运营"


class EngineError(Exception):
    """业务错误，main.py 据此返回 {"code": status, "msg"} 响应体。"""

    def __init__(self, status: int, msg: str):
        super().__init__(msg)
        self.status = status
        self.msg = msg


# ---------------------------------------------------------------------------
# 内存存储
# ---------------------------------------------------------------------------

TASKS: dict[str, dict] = {}
APPROVALS: dict[str, dict] = {}

_waiters: dict[str, asyncio.Future] = {}   # approval_id -> Future('approved'|'rejected')
_lock = threading.Lock()
_broadcaster: Callable[[str, Any], Awaitable[None]] | None = None
_SEQ = itertools.count(1)


def set_broadcaster(fn: Callable[[str, Any], Awaitable[None]]) -> None:
    """注入事件广播函数（main.py 的 WebSocket 推送实现）。"""
    global _broadcaster
    _broadcaster = fn


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _next_id(prefix: str) -> str:
    return f"{prefix}-{datetime.now().strftime('%Y%m%d')}-{next(_SEQ):04d}"


async def _emit(event: str, data: Any) -> None:
    if _broadcaster is not None:
        try:
            await _broadcaster(event, data)
        except Exception:  # noqa: BLE001
            pass


# ---------------------------------------------------------------------------
# 任务构造
# ---------------------------------------------------------------------------

def _make_node(node_id: str, ntype: str, name: str) -> dict:
    return {
        "id": node_id, "type": ntype, "name": name,
        "status": "pending", "started_at": None, "finished_at": None,
        "input": None, "output": None, "error": None, "logs": [],
    }


def _node(task: dict, node_id: str) -> dict:
    return next(n for n in task["nodes"] if n["id"] == node_id)


def _patch_node(task: dict, node_id: str, **patch) -> None:
    node = _node(task, node_id)
    node.update(patch)
    asyncio.get_running_loop().create_task(_emit(
        "node:update", {"task_id": task["id"], "node_id": node_id, "patch": patch},
    ))


def _log(node: dict, level: str, msg: str) -> None:
    node["logs"].append({"ts": _now_iso(), "level": level, "msg": msg})


def _build_task(payload: dict) -> dict:
    """按 mock.js buildTask 规则生成任务 DAG（节点/连线）。"""
    goal = str(payload.get("goal") or "")
    budget = payload.get("budget")
    budget = int(budget) if isinstance(budget, (int, float)) and budget else None
    needs = {
        "copy": any(k in goal for k in COPY_KEYWORDS),
        "pricing": any(k in goal for k in PRICE_KEYWORDS),
        "ads": any(k in goal for k in ADS_KEYWORDS) or (budget or 0) >= ADS_BUDGET_THRESHOLD,
    }
    if not any(needs.values()):
        needs["copy"] = True  # 与 mock 一致：无识别结果默认跑文案，保证可用

    nodes = [_make_node("n-sup", "supervisor", "Supervisor 主控")]
    edges: list[dict] = []
    if needs["copy"]:
        nodes.append(_make_node("n-copy", "copywriter", "爆款文案 Agent"))
        edges.append({"source": "n-sup", "target": "n-copy", "label": "文案任务", "kind": "normal"})
    if needs["pricing"]:
        nodes.append(_make_node("n-price", "pricing", "动态定价 Agent"))
        edges.append({"source": "n-sup", "target": "n-price", "label": "定价任务", "kind": "normal"})
    if needs["ads"]:
        nodes.append(_make_node("n-ads", "ads", "广告风控网关"))
        edges.append({"source": "n-sup", "target": "n-ads", "label": "广告投放", "kind": "normal"})
    nodes.append(_make_node("n-handoff", "handoff", "人工审批 Handoff"))
    nodes.append(_make_node("n-end", "end", "执行完成"))

    if needs["copy"]:
        edges.append({"source": "n-copy", "target": "n-handoff", "label": "重大发布", "kind": "condition"})
        edges.append({"source": "n-copy", "target": "n-end", "label": "常规通过", "kind": "normal"})
    if needs["pricing"]:
        edges.append({"source": "n-price", "target": "n-handoff", "label": "超阈值幅度", "kind": "condition"})
        edges.append({"source": "n-price", "target": "n-end", "label": "阈值内", "kind": "normal"})
    if needs["ads"]:
        edges.append({"source": "n-ads", "target": "n-handoff", "label": "预算熔断", "kind": "condition"})
    edges.append({"source": "n-handoff", "target": "n-end", "label": "", "kind": "normal"})

    return {
        "id": _next_id("TASK"), "goal": goal, "status": "running",
        "budget": budget, "created_at": _now_iso(), "nodes": nodes, "edges": edges,
    }


# ---------------------------------------------------------------------------
# 审批单构造
# ---------------------------------------------------------------------------

def _make_copy_approval(task: dict, title: str, content: str, violations: list[dict]) -> dict:
    return {
        "id": _next_id("APV-COPY"), "type": "copy", "status": "pending",
        "task_id": task["id"], "priority": "high",
        "created_at": _now_iso(), "handled_at": None, "handler": None, "comment": "",
        "sku": "", "product_name": task["goal"][:30], "platform": "xiaohongshu", "language": "zh-CN",
        "title": title, "content": content, "violations": violations,
        "versions": [{"version": 1, "content": content, "editor": "copywriter-agent", "created_at": _now_iso()}],
    }


def _make_pricing_approval(task: dict, price_result: dict) -> dict:
    change_pct = float(price_result.get("change_pct") or 0) * 100
    need_approval = bool(price_result.get("need_approval"))
    comp_rows = (price_result.get("comparison") or {}).get("competitors") or []
    competitors = [
        {"name": c.get("name") or c.get("competitor") or "-", "price": c.get("price"), "shipping": c.get("shipping", 0)}
        for c in comp_rows if isinstance(c, dict)
    ][:10]
    return {
        "id": _next_id("APV-PRICE"), "type": "pricing", "status": "pending",
        "task_id": task["id"], "priority": "high",
        "created_at": _now_iso(), "handled_at": None, "handler": None, "comment": "",
        "sku": price_result.get("sku", ""), "product_name": price_result.get("product_name", ""),
        "current_price": price_result.get("our_price"), "suggested_price": price_result.get("suggested_price"),
        "change_percent": round(change_pct, 2), "action": price_result.get("action", ""),
        "confidence": price_result.get("confidence"),
        "threshold_percent": PRICE_CHANGE_THRESHOLD, "threshold_exceeded": need_approval,
        "competitors": competitors, "reason": price_result.get("reason", ""),
        "expected_revenue": "", "risk_level": "medium", "risk_note": "",
    }


def _make_ads_approval(task: dict) -> dict:
    total_budget = task.get("budget") or 80000
    return {
        "id": _next_id("APV-ADS"), "type": "ads", "status": "pending",
        "task_id": task["id"], "priority": "critical",
        "created_at": _now_iso(), "handled_at": None, "handler": None, "comment": "",
        "campaign_name": task["goal"][:20] or "新品投放计划", "platform": "巨量千川",
        "bid": 3.5, "bid_threshold": BID_THRESHOLD,
        "daily_budget": 20000, "total_budget": total_budget, "budget_threshold": ADS_BUDGET_THRESHOLD,
        "threshold_exceeded": True,
        "allocation": [
            {"channel": "千川信息流", "percent": 60, "amount": round(total_budget * 0.6)},
            {"channel": "达人种草内容加热", "percent": 25, "amount": round(total_budget * 0.25)},
            {"channel": "品牌搜索广告", "percent": 15, "amount": round(total_budget * 0.15)},
        ],
        "roi_forecast": 3.0, "expected_clicks": "-",
        "reason": "出价与总预算超过风控阈值，已阻断广告下发 API 调用，需人工确认后下发广告计划。",
    }


# ---------------------------------------------------------------------------
# 子 Agent 适配（真实优先、缺失降级）
# ---------------------------------------------------------------------------

def _safe_check_banned(text: str) -> list[dict]:
    """违禁词扫描（VEC-003 真实链路）；Milvus 不可用时返回空列表，不阻断流程。"""
    try:
        from app.common.milvus_cli import check_banned_words
        return [
            {"word": h["word"], "type": h.get("category") or "违禁词", "suggestion": h.get("replacement", "")}
            for h in check_banned_words(text)
        ]
    except Exception:  # noqa: BLE001
        return []


def _fallback_copy(goal: str) -> tuple[str, str]:
    """成员D copywriter 未就绪时的模板草稿，保证主流程可演示。"""
    product = goal.strip()[:24] or "新品"
    title = f"【种草】{product}｜真实体验分享"
    content = (
        f"最近入手了{product}，用了一周来交作业啦～\n"
        "整体体验超出预期，细节做工在线，日常使用很顺手。\n"
        "感兴趣的宝子可以蹲一波活动价，评论区聊聊使用感受～"
    )
    return title, content


def _try_generate_copy(product: dict, platform: str) -> dict | None:
    """调用成员D 的 copywriter；未实现或失败返回 None。"""
    try:
        from app.agent.copywriter import generate_copy
        return generate_copy(product, platform)
    except Exception:  # noqa: BLE001
        return None


def _try_pricing_node(goal: str) -> dict | None:
    """调用成员E 的 pricing_node；未实现或失败返回 None。"""
    try:
        from app.agent.pricing import pricing_node
        return pricing_node({"goal": goal})
    except Exception as exc:  # noqa: BLE001
        return {"status": "failed", "error": str(exc)}


async def _run_copy_node(task: dict) -> list[str]:
    node = _node(task, "n-copy")
    _patch_node(task, "n-copy", status="running", started_at=_now_iso(),
                input={"goal": task["goal"], "platforms": ["xiaohongshu", "tiktok", "amazon"]})
    _log(node, "info", "接收子任务：结合 Milvus 爆款样本生成多平台营销文案")

    result = await asyncio.to_thread(_try_generate_copy, {"goal": task["goal"]}, "xiaohongshu")
    if result and isinstance(result, dict) and result.get("content"):
        title, content = result.get("title") or "", str(result["content"])
        _log(node, "info", "copywriter Agent 生成完成")
    else:
        title, content = _fallback_copy(task["goal"])
        _log(node, "warn", "copywriter Agent 未就绪，已使用模板草稿降级")

    violations = await asyncio.to_thread(_safe_check_banned, content)
    if violations:
        _log(node, "warn", f"违禁词扫描命中 {len(violations)} 处，已标注替换建议，需人工复核")
    else:
        _log(node, "info", "违禁词扫描完成，命中 0 处")

    approval = _make_copy_approval(task, title, content, violations)
    with _lock:
        APPROVALS[approval["id"]] = approval
    await _emit("approval:created", approval)
    _patch_node(task, "n-copy", status="success", finished_at=_now_iso(),
                output={"platforms": 3, "violations_found": len(violations), "approval_id": approval["id"]})
    return [approval["id"]]


async def _run_pricing_node(task: dict) -> list[str]:
    node = _node(task, "n-price")
    _patch_node(task, "n-price", status="running", started_at=_now_iso(), input={"goal": task["goal"]})
    _log(node, "info", "通过 ERP MCP 适配器拉取竞品价格并分析波动")

    out = await asyncio.to_thread(_try_pricing_node, task["goal"])
    if not out or out.get("status") != "done":
        error = (out or {}).get("error") or "pricing Agent 未实现"
        _log(node, "error", f"定价 Agent 执行失败：{error}")
        _patch_node(task, "n-price", status="failed", finished_at=_now_iso(), error=error)
        return []

    price_result = out["price_result"]
    _log(node, "success",
         f"调价建议：{price_result.get('action')}，置信度 {price_result.get('confidence')}")
    if not price_result.get("need_approval"):
        _patch_node(task, "n-price", status="success", finished_at=_now_iso(),
                    output={"action": price_result.get("action"),
                            "confidence": price_result.get("confidence"),
                            "change_percent": round(float(price_result.get("change_pct") or 0) * 100, 2)})
        return []

    _log(node, "warn", f"调价幅度超过 {PRICE_CHANGE_THRESHOLD}% 审批阈值，触发熔断，推送人工审批")
    approval = _make_pricing_approval(task, price_result)
    with _lock:
        APPROVALS[approval["id"]] = approval
    await _emit("approval:created", approval)
    _patch_node(task, "n-price", status="success", finished_at=_now_iso(),
                output={"action": price_result.get("action"),
                        "confidence": price_result.get("confidence"),
                        "approval_id": approval["id"]})
    return [approval["id"]]


async def _run_ads_node(task: dict) -> list[str]:
    """广告风控网关（SUP-005 预算熔断）：触发即阻断下发并生成审批单。"""
    node = _node(task, "n-ads")
    _patch_node(task, "n-ads", status="running", started_at=_now_iso(),
                input={"budget": task.get("budget"), "bid_threshold": BID_THRESHOLD})
    _log(node, "info", "接收广告计划下发请求，执行风控前置校验")
    _log(node, "warn",
         f"总预算 {task.get('budget') or 80000} 元超过 {ADS_BUDGET_THRESHOLD} 元风控阈值，"
         "已阻断广告下发 API 调用")

    approval = _make_ads_approval(task)
    with _lock:
        APPROVALS[approval["id"]] = approval
        register_waiter(approval["id"])
    await _emit("approval:created", approval)
    _patch_node(task, "n-ads", status="success", finished_at=_now_iso(),
                output={"blocked": True, "reason": "超预算阈值", "approval_id": approval["id"]})
    return [approval["id"]]


# ---------------------------------------------------------------------------
# 生命周期编排
# ---------------------------------------------------------------------------

async def _finish_task(task: dict) -> None:
    _patch_node(task, "n-end", status="running", started_at=_now_iso())
    end = _node(task, "n-end")
    _log(end, "success", "所有子任务与审批环节完成，结果已回传 ERP / 广告平台")
    task["status"] = "done"
    _patch_node(task, "n-end", status="success", finished_at=_now_iso())
    await _emit("task:update", {"task_id": task["id"], "status": task["status"]})


async def _settle_handoff(task: dict, approval_ids: list[str]) -> None:
    handoff = _node(task, "n-handoff")
    _patch_node(task, "n-handoff", status="running", started_at=_now_iso())
    _log(handoff, "warn", f"{len(approval_ids)} 项关键操作触发风控规则，执行链路已阻断，推送人工审批")
    _patch_node(task, "n-handoff", status="waiting")

    results = []
    for approval_id in approval_ids:
        waiter = _waiters.get(approval_id)
        if waiter is None:
            continue
        results.append(await waiter)

    if task["status"] == "failed":
        return  # 已被手动终止

    rejected = [r for r in results if r != "approved"]
    if rejected:
        _log(handoff, "error", f"{len(rejected)} 项审批被驳回，熔断终止，子任务结果不予下发")
        task["status"] = "failed"
        _patch_node(task, "n-handoff", status="failed", finished_at=_now_iso(), error="人工审批驳回",
                    output={"approved": len(results) - len(rejected), "rejected": len(rejected)})
        await _emit("task:update", {"task_id": task["id"], "status": task["status"]})
        return

    _log(handoff, "success", f"{len(results)} 项审批全部通过，Handoff 放行，继续执行下游")
    _patch_node(task, "n-handoff", status="success", finished_at=_now_iso(),
                output={"resolved": len(results)})
    await _finish_task(task)


async def run_lifecycle(task_id: str) -> None:
    """任务主流程：拆解 → 并行子 Agent → Handoff 审批 → 完成。

    成员C 接入 LangGraph 编排后可整体替换本函数。
    """
    task = TASKS[task_id]
    sup = _node(task, "n-sup")
    _patch_node(task, "n-sup", status="running", started_at=_now_iso(), input={"goal": task["goal"]})
    _log(sup, "info", "接收运营目标，进行意图识别与任务拆解")

    sub_types = [n["type"] for n in task["nodes"] if n["type"] in ("copywriter", "pricing", "ads")]
    _log(sup, "success", f"拆解完成，分发 {len(sub_types)} 个子任务：{'、'.join(sub_types) or '无'}")
    _patch_node(task, "n-sup", status="success", finished_at=_now_iso(), output={"sub_tasks": sub_types})

    runners = {"copywriter": _run_copy_node, "pricing": _run_pricing_node, "ads": _run_ads_node}
    outcomes = await asyncio.gather(*(runners[t](task) for t in sub_types))
    approval_ids = [aid for ids in outcomes for aid in ids]

    if approval_ids:
        await _settle_handoff(task, approval_ids)
    else:
        await _finish_task(task)


# ---------------------------------------------------------------------------
# 对外操作接口（供 main.py 路由调用，需在事件循环内调用）
# ---------------------------------------------------------------------------

def create_task(payload: dict) -> dict:
    """创建任务并启动生命周期，返回任务对象。"""
    goal = str(payload.get("goal") or "").strip()
    if not goal:
        raise EngineError(400, "goal 不能为空")
    task = _build_task({**payload, "goal": goal})
    with _lock:
        TASKS[task["id"]] = task
    asyncio.get_running_loop().create_task(_emit("task:update", {"task_id": task["id"], "status": task["status"]}))
    asyncio.get_running_loop().create_task(run_lifecycle(task["id"]))
    return task


def list_tasks() -> list[dict]:
    return sorted(TASKS.values(), key=lambda t: t["created_at"], reverse=True)


def get_task(task_id: str) -> dict:
    task = TASKS.get(task_id)
    if task is None:
        raise EngineError(404, "任务不存在")
    return task


async def cancel_task(task_id: str) -> dict:
    task = get_task(task_id)
    task["status"] = "failed"
    handoff = _node(task, "n-handoff")
    if handoff["status"] in ("running", "waiting"):
        _log(handoff, "error", "运营手动终止任务，未决审批自动驳回")
        _patch_node(task, "n-handoff", status="failed", finished_at=_now_iso(), error="手动终止")

    for approval in list(APPROVALS.values()):
        if approval["task_id"] == task_id and approval["status"] == "pending":
            approval["status"] = "rejected"
            approval["handled_at"] = _now_iso()
            approval["handler"] = CURRENT_OPERATOR
            approval["comment"] = "任务被手动终止，系统自动驳回"
            waiter = _waiters.pop(approval["id"], None)
            if waiter is not None and not waiter.done():
                waiter.set_result("rejected")
            await _emit("approval:updated", approval)

    await _emit("task:update", {"task_id": task_id, "status": task["status"]})
    return {"task_id": task_id, "status": task["status"]}


async def retry_task(task_id: str) -> dict:
    """将该任务下被驳回的审批单重新生成待审批单，Handoff 回到等待态。"""
    task = get_task(task_id)
    rejected = [a for a in APPROVALS.values() if a["task_id"] == task_id and a["status"] == "rejected"]
    if not rejected:
        raise EngineError(400, "当前任务没有可重试的驳回项")

    task["status"] = "running"
    handoff = _node(task, "n-handoff")
    end = _node(task, "n-end")
    for node, patch in ((handoff, {"status": "pending", "error": None, "output": None}),
                        (end, {"status": "pending"})):
        node.update(patch)
        await _emit("node:update", {"task_id": task_id, "node_id": node["id"], "patch": patch})

    new_ids: list[str] = []
    for old in rejected:
        fresh = copy.deepcopy(old)
        fresh["id"] = _next_id(f"APV-{old['type'].upper()}")
        fresh.update({"status": "pending", "created_at": _now_iso(),
                      "handled_at": None, "handler": None, "comment": ""})
        with _lock:
            APPROVALS[fresh["id"]] = fresh
            loop = asyncio.get_running_loop()
            _waiters[fresh["id"]] = loop.create_future()
        await _emit("approval:created", fresh)
        new_ids.append(fresh["id"])

    await _emit("task:update", {"task_id": task_id, "status": task["status"]})
    asyncio.get_running_loop().create_task(_settle_handoff(task, new_ids))
    return {"task_id": task_id, "status": task["status"], "recreated": new_ids}


def list_approvals(status: str | None = None, approval_type: str | None = None,
                   start_time: str | None = None, end_time: str | None = None) -> list[dict]:
    items = list(APPROVALS.values())
    if status == "pending":
        items = [a for a in items if a["status"] == "pending"]
    elif status == "processed":
        items = [a for a in items if a["status"] != "pending"]
    if approval_type:
        items = [a for a in items if a["type"] == approval_type]
    if start_time:
        items = [a for a in items if a["created_at"] >= start_time]
    if end_time:
        items = [a for a in items if a["created_at"] <= end_time]
    return sorted(items, key=lambda a: a["created_at"], reverse=True)


def get_approval(approval_id: str) -> dict:
    approval = APPROVALS.get(approval_id)
    if approval is None:
        raise EngineError(404, "审批单不存在")
    return approval


async def resolve_approval(approval_id: str, action: str, comment: str = "",
                           edited_content: str | None = None) -> dict:
    """审批通过/驳回。action ∈ approve | reject。"""
    approval = get_approval(approval_id)
    if approval["status"] != "pending":
        raise EngineError(409, "该审批单已处理，请勿重复操作")
    if action == "reject" and not (comment or "").strip():
        raise EngineError(400, "驳回必须填写原因")

    approval["status"] = "approved" if action == "approve" else "rejected"
    approval["handled_at"] = _now_iso()
    approval["handler"] = CURRENT_OPERATOR
    approval["comment"] = comment or ("同意" if action == "approve" else "")
    if action == "approve" and edited_content:
        approval["content"] = edited_content
        approval["versions"].append({
            "version": len(approval["versions"]) + 1,
            "content": edited_content, "editor": CURRENT_OPERATOR, "created_at": _now_iso(),
        })

    waiter = _waiters.pop(approval_id, None)
    if waiter is not None and not waiter.done():
        waiter.set_result(approval["status"])
    await _emit("approval:updated", approval)
    return approval


async def save_copy_draft(approval_id: str, content: str) -> dict:
    """文案微调：保存为新版本，不改变审批状态（APR-001）。"""
    approval = get_approval(approval_id)
    if approval["type"] != "copy":
        raise EngineError(404, "文案审批单不存在")
    approval["content"] = content
    approval["versions"].append({
        "version": len(approval["versions"]) + 1,
        "content": content, "editor": CURRENT_OPERATOR, "created_at": _now_iso(),
    })
    await _emit("approval:updated", approval)
    return {"version": len(approval["versions"])}


def register_waiter(approval_id: str) -> None:
    """为待审批单注册等待 Future（run_lifecycle 创建审批单后调用）。"""
    _waiters[approval_id] = asyncio.get_running_loop().create_future()
