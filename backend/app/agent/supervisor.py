# ============================================
# backend/app/agent/supervisor.py
# 运营主控协调 Agent
# ============================================

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from typing import TypedDict, Optional, Any
from backend.app.agent.pricing import pricing_node
from backend.app.agent.copywriter import copywriter_node


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
def decompose_goal(goal: str) -> list:
    """把运营目标拆解成子任务列表"""
    sub_tasks = []
    if any(kw in goal for kw in ["文案", "推广", "小红书", "TikTok", "亚马逊"]):
        sub_tasks.append({"type": "copywriter", "payload": {"goal": goal}})
    if any(kw in goal for kw in ["调价", "定价", "竞品", "价格"]):
        sub_tasks.append({"type": "pricing", "payload": {"goal": goal}})
    return sub_tasks


def supervisor_node(state: SupervisorState) -> dict:
    """
    主控节点：接收运营目标 → 拆解 → 调度子 Agent → 汇总结果
    """
    goal = state.get("goal", "")
    if not goal:
        return {"status": "failed", "error": "goal 不能为空"}

    sub_tasks = decompose_goal(goal)
    if not sub_tasks:
        return {"status": "failed", "error": f"无法识别目标类型：{goal}"}

    result = {"sub_tasks": sub_tasks, "status": "running"}
    has_error = False

    for task in sub_tasks:
        if task["type"] == "copywriter":
            sub = copywriter_task(state)
        elif task["type"] == "pricing":
            sub = pricing_task(state)
        else:
            continue

        if sub.get("status") == "failed":
            has_error = True
            result["error"] = sub.get("error", "子任务失败")

        result.update(sub)

    result["status"] = "failed" if has_error else "done"
    return result


# ============================================
# 四、独立运行测试
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