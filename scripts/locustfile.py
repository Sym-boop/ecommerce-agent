"""Locust 压测脚本：多 Agent 电商广告平台的四个核心接口。

**为什么四个接口要分开测**

它们的延迟特征完全不同，混在一起算平均值没有意义：

| 接口 | 类型 | 特征 |
|---|---|---|
| `GET /health` | 探测 | 含依赖探活，正常情况下毫秒级 |
| `GET /api/v1/ads` | 读 | 存储层全量读 + 序列化 |
| `GET /api/v1/review/stats` | 读 | 状态聚合 |
| `POST /api/v1/review/check` | 写（CPU） | jieba 分词 + 词边界匹配，CPU 密集 |

**权重按"真实访问比例"设**：读接口远多于写接口，所以 health/ads/stats 权重高，
`review/check` 权重低——如果等权，等于人为把 CPU 密集接口放大，测出的吞吐会失真。

**用法**

    # 独立 venv（未污染项目环境）
    .venv-locust/Scripts/locust.exe -f scripts/locustfile.py --host http://127.0.0.1:8001

    # 无头模式（适合留档）
    .venv-locust/Scripts/locust.exe -f scripts/locustfile.py \
        --host http://127.0.0.1:8001 --headless -u 20 -r 5 -t 30s \
        --csv data/reports/locust --html data/reports/locust.html

    # 带 Web UI（看实时曲线，默认 http://localhost:8089）
    .venv-locust/Scripts/locust.exe -f scripts/locustfile.py --host http://127.0.0.1:8001
"""
from __future__ import annotations

import random

from locust import HttpUser, between, events, task

# 违禁词检测的测试语料：一半命中一半干净，避免只走单一分支
_DIRTY_TEXTS = [
    ("全网最好的水杯", "100% 正品，绝对安全"),
    ("销量第一的耳机，顶级音质", "史上最低价"),
    ("国家级认证产品", "专供渠道，零风险"),
]
_CLEAN_TEXTS = [
    ("办公室必备！同事都来问链接的杯子", "最近入手了一款水杯，保温 12 小时，容量 500ml。"),
    ("通勤党狂喜！这款降噪耳机真的救我狗命", "地铁上开着降噪，整个世界都安静了。"),
    ("黄皮天菜！这支口红涂上立马白两个度", "清爽不黏腻，涂完直接出门。"),
]


class PlatformUser(HttpUser):
    """模拟运营人员：读多写少。"""

    wait_time = between(0.1, 0.5)

    @task(10)
    def health(self) -> None:
        """健康检查。含 Milvus/Redis 探活，是依赖不可用时的"背锅接口"。"""
        with self.client.get("/health", name="GET /health", catch_response=True) as resp:
            if resp.status_code != 200:
                resp.failure(f"health 返回 {resp.status_code}")
            elif '"status"' not in resp.text:
                resp.failure("health 响应结构异常")

    @task(12)
    def list_ads(self) -> None:
        """物料列表：存储层全量读 + 序列化。"""
        with self.client.get("/api/v1/ads", name="GET /ads", catch_response=True) as resp:
            if resp.status_code != 200:
                resp.failure(f"ads 返回 {resp.status_code}")

    @task(8)
    def review_stats(self) -> None:
        """审查状态聚合：五个阶段计数。"""
        with self.client.get("/api/v1/review/stats", name="GET /review/stats",
                             catch_response=True) as resp:
            if resp.status_code != 200:
                resp.failure(f"stats 返回 {resp.status_code}")

    @task(3)
    def banned_check(self) -> None:
        """违禁词检测：CPU 密集（jieba 分词 + 词边界匹配）。

        命中与干净各半，确保检出的两个分支都被压到。
        """
        if random.random() < 0.5:
            title, content = random.choice(_DIRTY_TEXTS)
        else:
            title, content = random.choice(_CLEAN_TEXTS)
        with self.client.post(
            "/api/v1/review/check",
            json={"title": title, "content": content},
            name="POST /review/check",
            catch_response=True,
        ) as resp:
            if resp.status_code != 200:
                resp.failure(f"check 返回 {resp.status_code}")

    # 权重合计 33：health 30% / ads 36% / stats 24% / check 9%（接近真实读多写少）


@events.test_start.add_listener
def _on_start(environment, **_kwargs) -> None:
    print(f"[locust] 目标: {environment.host}")


@events.test_stop.add_listener
def _on_stop(environment, **_kwargs) -> None:
    stats = environment.stats.total
    print("\n[locust] 汇总")
    print(f"  请求总数: {stats.num_requests}")
    print(f"  失败数  : {stats.num_failures}")
    print(f"  平均 RPS: {stats.total_rps:.1f}")
    print(f"  P50/P95/P99: {stats.get_response_time_percentile(0.50):.0f} / "
          f"{stats.get_response_time_percentile(0.95):.0f} / "
          f"{stats.get_response_time_percentile(0.99):.0f} ms")
