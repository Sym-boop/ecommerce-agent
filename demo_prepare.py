#!/usr/bin/env python
"""演示前一键预热：检查环境 → 补建演示账号 → 预热模型 → 自检关键接口。

**为什么需要它**

实测三个坑，都会在面试现场当场翻车：

1. **首次文案生成要 49 秒**（其中 Cross-Encoder 模型加载约 25 秒）。
   预热一次之后，热态只要 11 秒。不预热，面试官会盯着转圈等 50 秒。
2. **演示账号存在 SQLite 里**（`backend/data/app.db`）。如果这个文件被回滚/
   重建，`demo/demo123456` 就不存在，登录直接 404「账号不存在」。
3. 后端启动时 Milvus 探测要 3 秒、首次 RAG 检索要建 TF-IDF 索引。
   不预热，第一条检索会慢。

用法（后端已启动的情况下）：

    python demo_prepare.py                 # 默认 http://127.0.0.1:8000
    python demo_prepare.py --base http://127.0.0.1:8000 --skip-llm
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request

DEMO_USERS = [
    ("demo", "demo123456", "演示运营"),
    ("admin", "admin123456", "管理员"),
]


def call(base: str, method: str, path: str, body=None, token: str | None = None,
         timeout: float = 200.0):
    data = json.dumps(body, ensure_ascii=False).encode() if body is not None else None
    req = urllib.request.Request(f"{base}{path}", data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read().decode() or "{}")
        except Exception:  # noqa: BLE001
            return exc.code, {}
    except Exception as exc:  # noqa: BLE001
        return 0, {"msg": f"{type(exc).__name__}: {exc}"}


def step(n: int, total: int, text: str) -> None:
    print(f"[{n}/{total}] {text}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8000", help="后端地址")
    ap.add_argument("--skip-llm", action="store_true", help="跳过 LLM 预热（省 token）")
    ap.add_argument("--seed", action="store_true",
                    help="播种审查中心素材（写文件，需重启后端才可见）")
    ap.add_argument("--approval", action="store_true",
                    help="生成一张实时审批单（需后端已启动，重启后会变 orphaned）")
    ap.add_argument("--asset", action="store_true",
                    help="真跑一次图文生成（本地 Playwright 渲染海报，约 10 秒）")
    ap.add_argument("--deliver", action="store_true",
                    help="演示投放：登记店铺 → 预检 → mock 提交")
    args = ap.parse_args()
    base = args.base.rstrip("/") + "/api/v1"
    total = 6
    problems: list[str] = []

    # ---------- 1. 后端存活 ----------
    # 注意 /health 挂在根路径，不在 /api/v1 下
    root = args.base.rstrip("/")
    step(1, total, "后端存活")
    ok = False
    for _ in range(30):
        try:
            with urllib.request.urlopen(f"{root}/health", timeout=5) as resp:
                if resp.status == 200:
                    ok = True
                    break
        except Exception:  # noqa: BLE001
            pass
        time.sleep(2)
    if not ok:
        print("  ❌ 后端未响应。先启动：")
        print("     cd backend && python -m uvicorn app.main:app --host 127.0.0.1 --port 8000")
        return 1
    print("  ✅ 后端在线")

    # ---------- 2. 演示账号 ----------
    step(2, total, "演示账号")
    for username, password, display in DEMO_USERS:
        st, d = call(base, "POST", "/auth/register",
                     {"username": username, "password": password,
                      "display_name": display})
        msg = str(d.get("msg") or "")
        # 409「已被注册」是正常情况（账号之前建过），不是问题
        if st == 200:
            print(f"  ✅ 注册 {username}")
        elif st == 409 or "已注册" in msg or "已存在" in msg or "exist" in msg.lower():
            print(f"  · {username} 已存在（跳过）")
        else:
            problems.append(f"{username} 注册失败: HTTP {st} {msg[:50]}")
            print(f"  ❌ {username} 注册失败 HTTP {st}")

    # ---------- 3. 登录拿 token ----------
    step(3, total, "登录")
    st, d = call(base, "POST", "/auth/login",
                 {"username": "demo", "password": "demo123456"})
    if st != 200:
        print(f"  ❌ demo 登录失败 HTTP {st}: {d.get('msg')}")
        return 1
    token = d["data"]["token"]
    print(f"  ✅ demo 登录成功")

    # ---------- 4. 只读接口自检 ----------
    step(4, total, "只读接口")
    for path, label in [("/ads", "物料列表"), ("/review/stats", "审查统计"),
                        ("/review/pending", "待审批"), ("/diagnose/tools", "诊断工具"),
                        ("/billing/account", "账户余额")]:
        st, _ = call(base, "GET", path, token=token)
        print(f"  {'✅' if st == 200 else '❌'} {label} (HTTP {st})")
        if st != 200:
            problems.append(f"{label} 返回 {st}")

    # ---------- 5. 预热检索（TF-IDF + 精排模型）----------
    step(5, total, "预热检索链路（首次会加载精排模型，约 25 秒）")
    t0 = time.time()
    st, d = call(base, "POST", "/copy/retrieval-preview",
                 {"category": "水杯", "platform": "xiaohongshu", "top_k": 3},
                 token=token, timeout=180)
    dt = time.time() - t0
    if st == 200:
        n = len(d["data"].get("references") or [])
        print(f"  ✅ 检索就绪：{n} 条参考，耗时 {dt:.1f}s")
    else:
        print(f"  ❌ 检索预热失败 HTTP {st}: {d.get('msg')}")
        problems.append("检索预热失败")

    # ---------- 6. 预热文案生成 ----------
    if args.skip_llm:
        step(6, total, "跳过 LLM 预热（--skip-llm）")
    else:
        step(6, total, "预热文案生成（首次约 50 秒，之后约 11 秒）")
        t0 = time.time()
        st, d = call(base, "POST", "/adgen/copy", {
            "product": {"name": "便携保温杯", "category": "水杯",
                        "sell_points": ["12 小时保温", "304 不锈钢", "500ml"]},
            "platform": "xiaohongshu",
        }, token=token, timeout=300)
        dt = time.time() - t0
        if st == 200:
            q = (d["data"].get("quality") or {}).get("score")
            print(f"  ✅ 生成就绪：耗时 {dt:.1f}s，质量分 {q}")
        else:
            print(f"  ❌ 生成预热失败 HTTP {st}: {d.get('msg')}")
            problems.append("文案生成预热失败（检查 LLM_API_KEY）")

    # ---------- 播种演示数据（可选）----------
    if args.seed:
        print("\n[seed] 播种审查中心素材")
        seed_ads()
        print("  ⚠️ ad_store 在启动时载入内存，**需要重启后端**才能看到这两条素材")
    if args.approval:
        print("\n[approval] 生成实时审批单")
        seed_approval(base, token)
    if args.asset:
        print("\n[asset] 真跑一次图文生成")
        warm_asset(base, token)
    if args.deliver:
        print("\n[deliver] 演示投放（登记店铺 → 预检 → mock 提交）")
        warm_deliver(base, token)

    # ---------- 结论 ----------
    print("\n" + "=" * 56)
    if problems:
        print("⚠️ 有 %d 个问题：" % len(problems))
        for p in problems:
            print("   -", p)
        return 1
    print("✅ 演示环境就绪。打开前端：http://127.0.0.1:5173")
    print("   账号：demo / demo123456   （管理员 admin / admin123456）")
    print("=" * 56)
    return 0




# ---------------------------------------------------------------------------
# 演示数据播种
# ---------------------------------------------------------------------------

SEED_ADS = [
    {   # 违禁词审查页要有内容
        "ad_type": "copy",
        "title": "【演示】全网最好的保温杯，100% 正品",
        "content": "全网最好的保温杯，100% 正品保证，绝对保温 12 小时，顶级材质。",
        "platform": "xiaohongshu",
        "category": "水杯",
        "stage": "review",
        "copy_ok": False,
        "violations": [
            {"word": "最好", "category": "极限词", "basis": "广告法第九条(三)",
             "replacement": "不错", "start": 5, "end": 7, "context": "全网最好的保温杯"},
            {"word": "100%", "category": "虚假夸大", "basis": "广告法第二十八条",
             "replacement": "高比例", "start": 10, "end": 14, "context": "100% 正品保证"},
            {"word": "绝对", "category": "极限词", "basis": "广告法第九条(三)",
             "replacement": "非常", "start": 17, "end": 19, "context": "绝对保温 12 小时"},
            {"word": "顶级", "category": "极限词", "basis": "广告法第九条(三)",
             "replacement": "高品质", "start": 22, "end": 24, "context": "顶级材质"},
        ],
        "budget_verdict": "normal",
    },
    {   # 预算审查页要有内容
        "ad_type": "copy",
        "title": "【演示】通勤降噪耳机（预算超限）",
        "content": "地铁上开降噪，整个世界都安静了，续航 30 小时。",
        "platform": "xiaohongshu",
        "category": "蓝牙耳机",
        "stage": "review",
        "budget": 260000,
        "budget_ok": False,
        "budget_verdict": "over",
        "budget_msg": "预算 260000 元超出上限 100000 元，需人工调低后再投",
    },
]


def seed_ads() -> None:
    """写入待审素材（写 data/ads.json）。

    **为什么必须重启后端**：`ad_store` 在模块导入时把 ads.json 读进内存，
    从外部进程改文件，运行中的服务看不到——实测踩过，审查页一直空白。
    """
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent / "backend"))
    try:
        from app.common import ad_store
    except Exception as exc:  # noqa: BLE001
        print(f"  ⚠️ 无法导入 ad_store（{exc}），跳过")
        return

    existing = {str(a.get("title") or "") for a in ad_store.list_ads()}
    for spec in SEED_ADS:
        if spec["title"] in existing:
            print(f"  · 已存在：{spec['title'][:24]}")
            continue
        try:
            ad = ad_store.add_ad(**spec)
            print(f"  ✅ 写入 {ad.get('id')}  {spec['title'][:22]}…")
        except Exception as exc:  # noqa: BLE001
            print(f"  ❌ 写入失败：{type(exc).__name__}: {exc}")


def seed_approval(base: str, token: str) -> None:
    """下发一个超预算目标，让 task_engine 生成一张「活的」审批单。

    **必须在后端启动之后调用**：审批单落在进程内存 + 运行时文件里，
    服务重启会把 pending 标成 orphaned（设计如此，避免点不动的死单）。
    """
    st, d = call(base, "POST", "/goals", {
        "goal": "把这批素材在大促期间全量投放",
        "budget": 60000,
        "product_name": "便携保温杯",
        "product_category": "水杯",
        "product_sell_points": ["12 小时保温"],
        "platform": "xiaohongshu",
    }, token=token)
    if st != 200:
        print(f"  ❌ 下发目标失败 HTTP {st}: {d.get('msg')}")
        return
    print(f"  ✅ 已下发目标 {d['data'].get('task_id')}")
    for _ in range(6):
        time.sleep(3)
        st2, d2 = call(base, "GET", "/approvals?status=pending", token=token)
        data = d2.get("data")
        items = data if isinstance(data, list) else (data or {}).get("items") or []
        if items:
            print(f"  ✅ 审批中心有 {len(items)} 条待审批")
            for a in items[:3]:
                print(f"      {a.get('id')}  {str(a.get('reason') or '')[:34]}")
            return
    print("  ⚠️ 15 秒内没等到审批单，检查 /goals 的任务是否报错")


def warm_asset(base: str, token: str) -> None:
    """真跑一次图文生成，拿到海报 URL。

    `IMAGE_PROVIDER=local` 时不需要任何第三方 Key：Playwright 无头浏览器把 HTML
    模板渲染成 1080x1440 海报，实测约 10 秒。这是演示里最容易出效果的一幕：
    **真·本地渲染，不依赖外部服务**。
    """
    import io
    import urllib.request as _urllib
    import uuid

    st, d = call(base, "POST", "/adgen/copy", {
        "product": {"name": "便携保温杯", "category": "水杯",
                    "sell_points": ["12 小时保温", "304 不锈钢", "500ml"]},
        "platform": "xiaohongshu"}, token=token)
    if st != 200:
        print(f"  ⚠️ 文案生成失败 HTTP {st}，跳过图文")
        return
    sid = d["data"]["session_id"]

    boundary = "----dsh" + uuid.uuid4().hex
    buf = io.BytesIO()
    for k, v in {"session_id": sid, "type": "image", "keyword": "通勤 保温"}.items():
        buf.write(f"--{boundary}\r\n".encode())
        buf.write(f'Content-Disposition: form-data; name="{k}"\r\n\r\n'.encode())
        buf.write(str(v).encode() + b"\r\n")
    buf.write(f"--{boundary}--\r\n".encode())

    req = _urllib.Request(f"{base}/adgen/asset", data=buf.getvalue(), method="POST")
    req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    req.add_header("Authorization", f"Bearer {token}")
    try:
        with _urllib.urlopen(req, timeout=120) as resp:
            task_id = json.loads(resp.read().decode())["data"]["task_id"]
    except Exception as exc:  # noqa: BLE001
        print(f"  ⚠️ 提交图文任务失败：{exc}")
        return

    for _ in range(20):
        time.sleep(3)
        st, d = call(base, "GET", f"/adgen/asset/status/{task_id}", token=token)
        data = d.get("data") or {}
        if data.get("status") in ("succeeded", "failed"):
            if data.get("status") == "succeeded":
                print(f"  ✅ 海报已生成：{data.get('url')}")
                print(f"     session={sid}（生成页可直接看到）")
            else:
                print(f"  ❌ 图文生成失败：{data.get('error') or data}")
            return
    print("  ⚠️ 图文任务超时")


def warm_deliver(base: str, token: str) -> None:
    """登记演示店铺 → 预检 → mock 提交。

    两个坑（都实测过）：
    1. 字段名是 `shop_id`，不是 `store_id`；
    2. 平台键必须是 **qianchuan**——MCP 侧预检读 `data/stores/qianchuan.json`，
       登记到 `douyin.json` 会一直报「店铺未登记」。
    """
    st, _ = call(base, "POST", "/stores/qianchuan", {
        "shop_id": "44000001",
        "name": "演示店铺 · 保温杯专营",
        "aweme_id": "AW-9001",
        "douyin_account": "演示抖音号",
        "advertiser_id": "1800000001",
        "products": [{"product_id": "P1001", "name": "便携保温杯", "price": 89.0}],
    }, token=token)
    print(f"  {'✅' if st == 200 else '⚠️'} 登记店铺 HTTP {st}")

    payload = {
        "advertiser_id": "1800000001", "shop_id": "44000001", "product_id": "P1001",
        "aweme_id": "AW-9001", "image_id": "demo-poster",
        "name": "保温杯-通勤-图文计划", "marketing_goal": "短视频带货", "budget": 5000,
    }
    st, d = call(base, "POST", "/qianchuan/preflight", payload, token=token)
    data = d.get("data") or {}
    print(f"  预检 ready={data.get('ready')} mode={data.get('mode')}")
    for c in data.get("checks") or []:
        print(f"    {'✅' if c['ok'] else '❌'} {c['name']}")

    st, d = call(base, "POST", "/qianchuan/deliver", payload, token=token)
    data = d.get("data") or {}
    result = data.get("result") or {}
    print(f"  提交 submitted={data.get('submitted')} mode={data.get('mode')}")
    print(f"    计划 ID={result.get('ad_promotion_id')} 状态={result.get('status')}")


if __name__ == "__main__":
    sys.exit(main())
