# ============================================
# backend/app/agent/supervisor.py
# 运营主控协调 Agent
# 负责：接收运营目标 → 拆解子任务 → 调度 Copywriter / Pricing → 汇总结果
# ============================================

from typing import TypedDict, Optional


# ============================================
# 一、接口定义：SupervisorState
# ============================================
class SupervisorState(TypedDict, total=False):
    """Supervisor 全局状态，所有子图共享"""
    goal: str                    # 运营目标，如"给新品做一波小红书推广"
    sub_tasks: list              # 拆解后的子任务列表
    copy_result: str             # 文案生成结果
    price_result: dict           # 调价建议结果
    status: str                  # 执行状态：pending / running / done / failed
    error: Optional[str]         # 错误信息


# ============================================
# 二、Mock 实现（等 D/E 完成后替换）
# ============================================
def mock_copywriter(state: SupervisorState) -> dict:
    """模拟文案生成，等 D 的 copywriter.py 完成后替换"""
    return {
        "copy_result": "【模拟文案】春日新品上市，法式碎花连衣裙，限时 8 折，点击链接抢购！",
        "status": "done",
    }


def mock_pricing(state: SupervisorState) -> dict:
    """模拟定价建议，等 E 的 pricing.py 完成后替换"""
    return {
        "price_result": {
            "action": "降价",
            "confidence": 0.85,
            "reason": "竞品近 3 天降价 5%，建议跟随降价 3% 保持竞争力",
        },
        "status": "done",
    }


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

    for task in sub_tasks:
        if task["type"] == "copywriter":
            # 等 D 完成后替换为：from backend.app.agent.copywriter import copywriter_node
            result.update(mock_copywriter(state))
        elif task["type"] == "pricing":
            # 等 E 完成后替换为：from backend.app.agent.pricing import pricing_node
            result.update(mock_pricing(state))

    result["status"] = "done"
    return result


# ============================================
# 四、独立运行测试（可选）
# ============================================
if __name__ == "__main__":
    test_state = {"goal": "给新品连衣裙做一波小红书推广并调价"}
    output = supervisor_node(test_state)
    print("调度结果：")
    for k, v in output.items():
        print(f"  {k}: {v}")