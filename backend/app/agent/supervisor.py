# ============================================
# backend/app/agent/supervisor.py
# 运营主控协调 Agent
# ============================================

import sys
import uuid
import logging
from pathlib import Path
from backend.app.agent.image_generator import image_generator_node
from backend.app.agent.video_generator import video_generator_node

_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from typing import TypedDict, Optional, Any
from backend.app.agent.pricing import pricing_node
from backend.app.agent.copywriter import copywriter_node

logger = logging.getLogger(__name__)


# ============================================
# 一、接口定义：SupervisorState
# ============================================
class SupervisorState(TypedDict, total=False):
    # ---- 任务标识 ----
    task_id: str
    thread_id: str
    goal: str

    # ---- 子任务 ----
    sub_tasks: list

    # ---- 子图输入 ----
    product_info: dict
    platform: str
    sku: str
    our_price: float

    # ---- Copywriter 需要的字段 ----
    product_name: str
    product_sell_points: list
    product_category: str
    language: str
    reference_count: int
    forbidden_hit: list
    reference_snippets: list

    # ---- 子图输出 ----
    results: dict
    copy_result: Any
    price_result: dict

    # ---- 审批与熔断 ----
    approval_required: bool
    approval_status: str
    approval_reason: str

    # ---- 执行状态 ----
    status: str
    error: Optional[str]
    retry_count: int
    metadata: dict

    image_result: dict
    video_result: dict


# ============================================
# 二、子 Agent 调用封装
# ============================================
def copywriter_task(state: SupervisorState) -> dict:
    """调用真实 Copywriter Agent"""
    return copywriter_node(state)


def pricing_task(state: SupervisorState) -> dict:
    """调用真实 Pricing Agent"""
    return pricing_node(state)



# ============================================
# 三、任务拆解 + 主控调度
# ============================================
ADS_KEYWORDS = ["广告", "投放", "出价", "千川", "预算", "巨量", "Meta", "加投"]

def decompose_goal(goal: str) -> list:
    sub_tasks = []
    if any(kw in goal for kw in ["文案", "推广", "小红书", "TikTok", "亚马逊"]):
        sub_tasks.append({"type": "copywriter", "payload": {"goal": goal}})
    if any(kw in goal for kw in ["调价", "定价", "竞品", "价格"]):
        sub_tasks.append({"type": "pricing", "payload": {"goal": goal}})
    if any(kw in goal for kw in ADS_KEYWORDS):
        sub_tasks.append({"type": "ads", "payload": {"goal": goal}})

    # 新增
    if any(kw in goal for kw in ["图文", "图片", "配图"]):
        sub_tasks.append({"type": "image"})
    if any(kw in goal for kw in ["视频", "短视频", "动态"]):
        sub_tasks.append({"type": "image"})   # 视频依赖图片
        sub_tasks.append({"type": "video"})
    return sub_tasks


def supervisor_node(state: SupervisorState) -> dict:
    """
    主控节点：接收运营目标 → 拆解 → 调度子 Agent → 汇总结果
    """
    # 1. 生成任务标识
    if not state.get("task_id"):
        state["task_id"] = str(uuid.uuid4())
    if not state.get("thread_id"):
        state["thread_id"] = state["task_id"]

    goal = state.get("goal", "")
    if not goal:
        return {
            "task_id": state["task_id"],
            "thread_id": state["thread_id"],
            "status": "failed",
            "error": "goal 不能为空",
        }

    logger.info("任务 %s 开始执行，目标：%s", state["task_id"], goal)

    sub_tasks = decompose_goal(goal)
    if not sub_tasks:
        return {
            "task_id": state["task_id"],
            "thread_id": state["thread_id"],
            "status": "failed",
            "error": f"无法识别目标类型：{goal}",
        }

    result = {
        "task_id": state["task_id"],
        "thread_id": state["thread_id"],
        "sub_tasks": sub_tasks,
        "status": "running",
        "results": {},
    }
    has_error = False

    for task in sub_tasks:
        if task["type"] == "copywriter":
            sub = copywriter_task(state)
        elif task["type"] == "pricing":
            sub = pricing_task(state)
        elif task["type"] == "image":
            sub = image_generator_node(state)
        elif task["type"] == "video":
            sub = video_generator_node(state)
        else:
            continue

        if sub.get("status") == "failed":
            has_error = True
            result["error"] = sub.get("error", "子任务失败")

        result.update(sub)
        # 将子 Agent 产出回写到 state，供后续依赖节点（图文/视频）读取上游结果
        state.update(sub)

        if sub.get("copy_result") is not None:
            result["results"]["copywriter"] = sub["copy_result"]
        if sub.get("price_result") is not None:
            result["results"]["pricing"] = sub["price_result"]
        if sub.get("image_result") is not None:
            result["results"]["image"] = sub["image_result"]
        if sub.get("video_result") is not None:
            result["results"]["video"] = sub["video_result"]

    # 审批熔断 + 状态保留
    if result.get("approval_required") or result.get("forbidden_hit"):
        result["status"] = "waiting_approval"
        result["approval_status"] = "pending"
        result["metadata"] = result.get("metadata", {})
        result["metadata"]["pending_results"] = {
            "copy_result": result.get("copy_result"),
            "price_result": result.get("price_result"),
        }
        logger.info("任务 %s 触发审批，等待人工处理", state["task_id"])
    else:
        result["status"] = "failed" if has_error else "done"
        logger.info("任务 %s 执行结束，状态：%s", state["task_id"], result["status"])

    return result


# ============================================
# 四、审批恢复函数
# ============================================
def approve_task(state: SupervisorState, decision: str, comment: str = "") -> dict:
    """
    前端审批后调用，恢复被熔断的任务。

    Args:
        state: 处于 waiting_approval 状态的 SupervisorState
        decision: "approved" 或 "rejected"
        comment: 审批意见

    Returns:
        更新后的 state，包含最终状态
    """
    if state.get("status") != "waiting_approval":
        return {
            **state,
            "error": f"当前状态 {state.get('status')} 不是 waiting_approval，无法审批",
        }

    state["approval_status"] = decision
    state["metadata"] = state.get("metadata", {})
    state["metadata"]["approval_comment"] = comment

    if decision == "approved":
        state["status"] = "done"
        state["approval_reason"] = f"人工批准：{comment}" if comment else "人工批准"
        logger.info("任务 %s 审批通过", state.get("task_id"))
    elif decision == "rejected":
        state["status"] = "failed"
        state["error"] = f"人工拒绝：{comment}" if comment else "人工拒绝"
        logger.info("任务 %s 审批拒绝", state.get("task_id"))
    else:
        state["status"] = "failed"
        state["error"] = f"无效的审批决定：{decision}"

    return state


# ============================================
# 五、独立运行测试
# ============================================
if __name__ == "__main__":
    test_state = {
        "goal": "给新品保温杯做一波小红书推广",
        "product_name": "316不锈钢保温杯",
        "product_sell_points": ["24小时保温", "轻便", "密封性好"],
        "product_category": "水杯",
        "platform": "xiaohongshu",
    }
    output = supervisor_node(test_state)
    print("调度结果：")
    for k, v in output.items():
        print(f"  {k}: {v}")

    # 模拟审批恢复
    if output.get("status") == "waiting_approval":
        print("\n--- 模拟审批通过 ---")
        approved = approve_task(output, "approved", "同意发布")
        print(f"最终状态：{approved['status']}")