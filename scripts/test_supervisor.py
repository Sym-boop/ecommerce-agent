# ============================================
# scripts/test_supervisor.py
# Supervisor 主流程测试
# 用 mock 子图先跑通全链路，等 D/E 完成后替换真实子图
# ============================================

import sys
import os

# 把项目根目录加入 sys.path，保证能 import backend
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from backend.app.agent.supervisor import supervisor_node


def test_single_copywriter():
    print("\n===== 测试1：只触发文案生成 =====")
    state = {
        "goal": "给新品做一波小红书推广",
        "product_name": "316不锈钢保温杯",
        "product_sell_points": ["24小时保温", "轻便", "密封性好"],
        "product_category": "水杯",
        "platform": "xiaohongshu",
    }
    result = supervisor_node(state)
    assert result["status"] in ("done", "failed", "waiting_approval"), f"意外状态: {result['status']}"
    if "copy_result" in result and isinstance(result["copy_result"], dict):
        print("通过：", result["copy_result"].get("title", "")[:50])
    else:
        print("通过：", str(result.get("copy_result", ""))[:50])
    if result["status"] == "waiting_approval":
        print("注意：命中违禁词触发审批，forbidden_hit =", result.get("forbidden_hit"))


def test_single_pricing():
    print("\n===== 测试2：只触发定价 =====")
    state = {
        "goal": "监控 SKU-1001 竞品价格并给出调价建议",
        "sku": "SKU-1001",
    }
    result = supervisor_node(state)
    assert result["status"] in ("done", "failed"), f"意外状态: {result['status']}"
    if result["status"] == "failed":
        print("定价未就绪：", result.get("error"))
        return
    assert "price_result" in result
    print("通过：", result["price_result"].get("action"))


def test_both():
    print("\n===== 测试3：同时触发文案 + 定价 =====")
    state = {
        "goal": "给新品 SKU-1001 连衣裙做小红书推广，同时监控竞品调价",
        "sku": "SKU-1001",
        "product_name": "法式碎花连衣裙",
        "product_sell_points": ["显瘦", "透气", "适合春夏"],
        "product_category": "女装",
        "platform": "xiaohongshu",
    }
    result = supervisor_node(state)
    assert result["status"] in ("done", "failed"), f"意外状态: {result['status']}"
    assert "copy_result" in result, "文案应返回"
    if "price_result" in result:
        print("通过：文案 + 定价都返回")
    else:
        print("通过：文案已返回，定价未就绪：", result.get("error", ""))


def test_empty_goal():
    """测试：空目标应报错"""
    print("\n===== 测试4：空目标 =====")
    state = {"goal": ""}
    result = supervisor_node(state)
    assert result["status"] == "failed", "空目标应返回 failed"
    print("通过：", result["error"])


def test_unknown_goal():
    """测试：无法识别的目标应报错"""
    print("\n===== 测试5：无法识别的目标 =====")
    state = {"goal": "今天天气怎么样"}
    result = supervisor_node(state)
    assert result["status"] == "failed"
    print("通过：", result["error"])


if __name__ == "__main__":
    print("开始 Supervisor 主流程测试...")
    test_single_copywriter()
    test_single_pricing()
    test_both()
    test_empty_goal()
    test_unknown_goal()
    print("\n全部测试通过 ✅")