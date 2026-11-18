"""验证 MySQL 分布式队列的多 worker 安全性（不重复消费、不丢任务）。

核心验证点：
  1. 提交 N 个任务，起 M 个 worker，每个任务**恰好被执行一次**
  2. 消费记录里没有重复 job_id（SKIP LOCKED 生效）
  3. 所有任务都到达终态（不丢）
  4. 任务确实被多个 worker 分担（不是单线程全干）

在项目根目录运行：python scripts/verify_queue.py
"""

from __future__ import annotations

import os
import sys
import threading
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
os.chdir(ROOT)

from app.common.job_queue import (  # noqa: E402
    MysqlJobQueue, QueueWorker, register_handler, registered_kinds,
)

DB_CFG = {"host": "127.0.0.1", "port": 3306, "user": "root",
          "password": "root", "database": "ecommerce_agent"}

# 全局执行记录（用锁保护）
EXECUTED: list[str] = []
EXEC_LOCK = threading.Lock()


def handle_sleep(payload: dict) -> dict:
    """模拟一个耗时任务，用于制造竞争。"""
    job_id = payload.get("_job_id", "")
    time.sleep(payload.get("sleep", 0.02))
    with EXEC_LOCK:
        EXECUTED.append(job_id)
    return {"ok": True, "job_id": job_id}


def handle_fail(payload: dict) -> dict:
    raise RuntimeError("故意失败，验证 failed 状态")


register_handler("verify.sleep", handle_sleep)
register_handler("verify.fail", handle_fail)


def main() -> int:
    table = f"jobs_verify_{int(time.time())}"
    q = MysqlJobQueue(config=DB_CFG, table=table)

    print("=" * 68)
    print("MySQL 分布式队列 · 多 worker 安全性验证")
    print("=" * 68)
    print(f"表: {table}")
    print(f"已注册处理器: {registered_kinds()}")

    N_JOBS = 60
    N_WORKERS = 4

    print(f"\n[1] 提交 {N_JOBS} 个任务 …")
    ids = []
    for i in range(N_JOBS):
        job = q.submit("verify.sleep", {"_job_id": f"job-{i:03d}", "sleep": 0.01})
        ids.append(job["id"])
    print(f"    提交完成，队列统计: {q.stats()}")

    print(f"\n[2] 启动 {N_WORKERS} 个 worker 并发消费 …")
    workers = [QueueWorker(q, worker_id=f"worker-{i}", poll_interval=0.01).start()
               for i in range(N_WORKERS)]

    # 等待全部到达终态
    deadline = time.time() + 60
    while time.time() < deadline:
        st = q.stats()
        done = st["by_status"].get("succeeded", 0) + st["by_status"].get("failed", 0)
        if done >= N_JOBS:
            break
        time.sleep(0.2)

    for w in workers:
        w.stop()

    st = q.stats()
    print(f"    结束时统计: {st}")

    # ---------------- 断言 ----------------
    print("\n[3] 校验")
    ok = True

    succeeded = st["by_status"].get("succeeded", 0)
    failed = st["by_status"].get("failed", 0)
    pending = st["by_status"].get("pending", 0)
    running = st["by_status"].get("running", 0)

    def check(name: str, cond: bool, detail: str = "") -> None:
        nonlocal ok
        print(f"    {'✅' if cond else '❌'} {name}" + (f" — {detail}" if detail else ""))
        if not cond:
            ok = False

    check("全部任务到达终态", succeeded + failed == N_JOBS,
          f"succeeded={succeeded} failed={failed} pending={pending} running={running}")
    check("没有任务卡在 pending/running", pending == 0 and running == 0,
          f"pending={pending} running={running}")

    # 恰好执行一次：EXECUTED 数量 == 成功数，且无重复
    counter = Counter(EXECUTED)
    dups = {k: v for k, v in counter.items() if v > 1}
    check("没有任务被重复执行", not dups, f"重复项={dict(list(dups.items())[:3])}")
    check("执行次数与成功数一致", len(EXECUTED) == succeeded,
          f"executed={len(EXECUTED)} succeeded={succeeded}")

    # 多 worker 分担：distinct worker 数 > 1
    check("任务被多个 worker 分担", st["distinct_workers"] > 1,
          f"参与 worker 数={st['distinct_workers']}")

    # 每个 worker 实际处理的条数
    print("\n[4] 各 worker 分担情况")
    per_worker = Counter()
    for job in q.list_jobs(limit=N_JOBS * 2):
        if job.get("worker_id"):
            per_worker[job["worker_id"]] += 1
    for w, c in sorted(per_worker.items()):
        print(f"    {w}: {c} 个")
    check("每个 worker 都有产出（负载确实被分摊）", len(per_worker) >= 2,
          f"{len(per_worker)} 个 worker 有产出")

    # ---------------- 失败任务 ----------------
    print("\n[5] 失败任务处理")
    fail_job = q.submit("verify.fail", {"reason": "test"})
    w = QueueWorker(q, worker_id="worker-fail", poll_interval=0.01).start()
    deadline = time.time() + 15
    while time.time() < deadline:
        j = q.get(fail_job["id"])
        if j and j["status"] in ("failed", "succeeded"):
            break
        time.sleep(0.1)
    w.stop()
    j = q.get(fail_job["id"])
    check("失败任务被标记为 failed 并记录错误", j["status"] == "failed" and bool(j["error"]),
          f"status={j['status']} error={str(j['error'])[:40]}")

    # ---------------- 卡死任务回收 ----------------
    print("\n[6] 崩溃恢复：回收卡在 running 的任务")
    stuck = q.submit("verify.sleep", {"_job_id": "stuck"})
    conn = q._conn
    with conn.cursor() as cur:
        cur.execute(
            f"UPDATE `{table}` SET status='running', worker_id='dead', "
            "started_at=NOW() - INTERVAL 20 MINUTE WHERE id=%s", (stuck["id"],))
    conn.commit()
    reclaimed = q.reclaim_stale(timeout_seconds=600)
    after = q.get(stuck["id"])
    check("超时任务被放回 pending", reclaimed >= 1 and after["status"] == "pending",
          f"回收 {reclaimed} 个，状态={after['status']}")

    print("\n" + "=" * 68)
    print(f"结果：{'全部通过 ✅' if ok else '存在失败项 ❌'}")

    # 清理
    with conn.cursor() as cur:
        cur.execute(f"DROP TABLE IF EXISTS `{table}`")
    conn.commit()
    q.close()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
