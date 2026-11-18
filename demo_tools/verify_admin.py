"""验证管理员专属能力：审计日志 / 指标 / 质量 / 版本 / 飞轮 / 词表热加载 / 角色矩阵。

在项目根目录运行：python demo_tools/verify_admin.py
"""

import json
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8000"
RESULTS: list[tuple[bool, str, str]] = []


def call(method: str, path: str, payload=None, token: str = ""):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(body)
        except json.JSONDecodeError:
            return e.code, {"raw": body}


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((ok, name, detail))
    print(f"{'✅' if ok else '❌'} {name}" + (f"  — {detail}" if detail else ""))


print("=" * 72)
print("管理员能力验证")
print("=" * 72)

status, login = call("POST", "/api/v1/auth/login", {"username": "admin", "password": "admin123456"})
if status != 200:
    print("管理员登录失败:", login)
    raise SystemExit(1)
token = login["data"]["token"]
role = login["data"]["user"]["role"]
check("管理员登录", role == "管理员", f"角色={role}")

# 1) 角色权限矩阵
_, rbac = call("GET", "/api/v1/system/rbac", token=token)
rbac = rbac.get("data", {})
check("管理员拥有全部权限", len(rbac.get("current_permissions", [])) == 5,
      f"{rbac.get('current_permissions')}")

# 2) 审计日志
status, audit = call("GET", "/api/v1/system/audit?limit=100", token=token)
rows = audit.get("data", []) if status == 200 else []
check("审计日志可读取（管理员）", status == 200 and len(rows) > 0, f"{len(rows)} 条")
if rows:
    actions = sorted({r.get("action") for r in rows})
    check("审计覆盖多类动作", len(actions) >= 2, f"{actions[:8]}")
    with_changes = [r for r in rows if r.get("changes")]
    check("审计记录了改前改后", len(with_changes) > 0,
          f"{len(with_changes)} 条含变更明细")
    actors = {r.get("actor") for r in rows}
    check("审计记录了操作人", bool(actors), f"{sorted(a for a in actors if a)[:5]}")

_, astats = call("GET", "/api/v1/system/audit/stats", token=token)
astats = astats.get("data", {})
check("审计概览可用", astats.get("total", 0) > 0, f"总 {astats.get('total')} 条，按动作 {list(astats.get('by_action', {}))[:5]}")

# 3) 指标
_, m = call("GET", "/api/v1/system/metrics")
m = m.get("data", {})
caps = m.get("capabilities", {})
check("指标含能力维度", len(caps) >= 2, f"{list(caps.keys())}")
for name in ("llm_call", "rag_retrieval"):
    if name in caps:
        c = caps[name]
        check(f"  {name} 指标完整",
              all(k in c for k in ("calls", "failed", "degraded", "avg_ms", "p95_ms")),
              f"calls={c['calls']} failed={c['failed']} degraded={c['degraded']} avg={c['avg_ms']}ms")
check("RAG 降级被记录（Milvus 不可用）", m.get("counters", {}).get("rag_empty_fallback", 0) > 0,
      f"rag_empty_fallback={m.get('counters', {}).get('rag_empty_fallback')}")
check("成本估算基于真实 token", m.get("llm_tokens", {}).get("total", 0) > 0,
      f"tokens={m.get('llm_tokens')} 成本=¥{m.get('cost_estimate')}")

# 4) 质量
_, q = call("GET", "/api/v1/system/quality")
q = q.get("data", {})
check("质量看板有记录", q.get("total_records", 0) > 0,
      f"{q.get('total_records')} 条, 平均 {q.get('summary', {}).get('avg_score')}")
check("质量指标通过率完整", len(q.get("metric_rates", {})) == 4,
      json.dumps(q.get("metric_rates", {}), ensure_ascii=False))
check("分数分布可用", sum(q.get("score_distribution", {}).values()) == q.get("total_records", 0),
      json.dumps(q.get("score_distribution", {}), ensure_ascii=False))

# 5) 词表热加载
status, reload_res = call("POST", "/api/v1/review/banned-words/reload", token=token)
rd = reload_res.get("data", {}) if status == 200 else {}
check("管理员可热加载词表", status == 200 and rd.get("available"),
      f"{rd.get('word_count')} 条 / 建议 {rd.get('replacement_count')} 条")

# 6) 飞轮
_, fw = call("GET", "/api/v1/system/flywheel")
fw = fw.get("data", {})
check("飞轮概览可用", "total_samples" in fw,
      f"样本 {fw.get('total_samples')} / 待补写 {fw.get('pending_flush')}")

# 7) 版本
_, ads = call("GET", "/api/v1/ads")
ads = ads.get("data", [])
if ads:
    ad_id = ads[0]["id"]
    _, ver = call("GET", f"/api/v1/system/versions/{ad_id}")
    vd = ver.get("data", {})
    check("版本链接口可用", "versions" in vd, f"{ad_id} 有 {len(vd.get('versions', []))} 个版本")

print()
ok_count = sum(1 for ok, _, _ in RESULTS if ok)
print(f"结果：{ok_count}/{len(RESULTS)} 通过")
for ok, n, d in RESULTS:
    if not ok:
        print(f"  ❌ {n} — {d}")
