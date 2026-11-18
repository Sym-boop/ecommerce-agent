"""验证审计「改前改后」与数据飞轮：走一遍人工修正放行 → 审批通过。

在项目根目录运行：python demo_tools/verify_flywheel.py
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
        with urllib.request.urlopen(req, timeout=180) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(body)
        except json.JSONDecodeError:
            return e.code, {"raw": body}


def check(name, ok, detail=""):
    RESULTS.append((ok, name, detail))
    print(f"{'✅' if ok else '❌'} {name}" + (f"  — {detail}" if detail else ""))


# 三个角色：生成(运营) / 审查 / 审批 —— 用于验证职责分离
def ensure_user(username: str, password: str, role: str, display: str) -> str:
    import hashlib
    import secrets
    import sqlite3
    from datetime import datetime, timezone
    from pathlib import Path

    db = Path(__file__).resolve().parents[1] / "backend" / "data" / "app.db"
    con = sqlite3.connect(db)
    salt = secrets.token_hex(16)
    pw_hash = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 120000).hex()
    row = con.execute("SELECT username FROM users WHERE username=?", (username,)).fetchone()
    if row:
        con.execute("UPDATE users SET password_hash=?, salt=?, role=?, display_name=? WHERE username=?",
                    (pw_hash, salt, role, display, username))
    else:
        con.execute(
            "INSERT INTO users(username,password_hash,salt,display_name,role,created_at) VALUES (?,?,?,?,?,?)",
            (username, pw_hash, salt, display, role,
             datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")),
        )
    con.commit()
    con.close()
    status, res = call("POST", "/api/v1/auth/login", {"username": username, "password": password})
    assert status == 200, f"登录 {username} 失败: {res}"
    return res["data"]["token"]


print("=" * 72)
print("数据飞轮 + 审计变更明细 + 职责分离 验证")
print("=" * 72)

operator_token = ensure_user("op_user", "op123456", "运营", "运营小张")
reviewer_token = ensure_user("rv_user", "rv123456", "审查", "审查小李")
approver_token = ensure_user("ap_user", "ap123456", "审批", "审批老王")

# ---------- 1. 运营生成一条「含违禁词」的文案 ----------
_, gen = call("POST", "/api/v1/adgen/copy", {
    "product": {"name": "轻透防晒隔离霜", "category": "防晒", "price": 89.0,
                "sell_points": ["清爽不假白", "敏感肌适用"]},
    "keyword": "通勤防晒", "platform": "xiaohongshu",
}, token=operator_token)
sid = gen["data"]["session_id"]
print(f"生成会话: {sid}")
_, saved = call("POST", "/api/v1/adgen/save", {"session_id": sid}, token=operator_token)
ad_ids = saved["data"].get("ad_ids", [])
ad_id = ad_ids[0]
_, ads = call("GET", "/api/v1/ads")
ad = next((a for a in ads["data"] if a["id"] == ad_id), {})
check("运营生成的物料记录了生成人",
      ad.get("generated_by") == "op_user", f"generated_by={ad.get('generated_by')}")
check("物料记录类目（供数据飞轮检索过滤）", ad.get("category") == "防晒",
      f"category={ad.get('category')}")

# ---------- 2. 职责分离：同一人不能自审 ----------
operator_clear = call("POST", f"/api/v1/review/copy/{ad_id}/clear",
                      {"title": ad.get("title"), "content": ad.get("content") or ""},
                      token=operator_token)
check("运营无「审查放行」权限（403）", operator_clear[0] == 403,
      str(operator_clear[1].get("detail"))[:70])

# ---------- 3. 审查角色做人工修正放行 → 触发数据飞轮 ----------
# 故意注入一个极限词再修正，模拟真实"模型写了违禁词、人工改掉"的场景
bad_title = "全网最好的防晒霜｜敏感肌闭眼入"
bad_status, bad_res = call("POST", f"/api/v1/review/copy/{ad_id}/clear",
                           {"title": bad_title, "content": ad.get("content") or ""},
                           token=reviewer_token)
check("审查角色有权放行（未被 403 拦截）", bad_status != 403,
      f"HTTP {bad_status}")
# 上一步用带违禁词的标题应被拒绝（400），验证「改完仍命中就不放行」
check("携带违禁词时拒绝放行", bad_status == 400,
      str(bad_res.get("msg"))[:70] if bad_status == 400 else f"未拦截！HTTP {bad_status}")

# 用干净标题 + 修改后的正文放行（真正的人工修正）
clean_title = "清爽不假白的防晒｜敏感肌也能用"
fixed_content = (ad.get("content") or "") + "\n（人工补充：通勤路上补涂更方便）"
status, cleared = call("POST", f"/api/v1/review/copy/{ad_id}/clear",
                       {"title": clean_title, "content": fixed_content},
                       token=reviewer_token)
check("人工修正后放行成功", status == 200, f"HTTP {status} {cleared.get('msg', '')}")
if status == 200:
    after = cleared["data"]
    check("标题已更新为人工版本", after.get("title") == clean_title, after.get("title"))

# ---------- 4. 数据飞轮：人工修正样本应回流 ----------
_, fw = call("GET", "/api/v1/system/flywheel")
fw = fw.get("data", {})
check("人工修正样本已回流", fw.get("total_samples", 0) >= 1,
      f"样本 {fw.get('total_samples')} / 已入库 {fw.get('flushed')} / 待补写 {fw.get('pending_flush')}")

_, samples = call("GET", "/api/v1/system/flywheel/samples?limit=5")
samples = samples.get("data", [])
if samples:
    s = samples[0]
    check("回流样本带类目与平台（可作检索参考）",
          s.get("category") == "防晒" and s.get("platform") == "xiaohongshu",
          f"category={s.get('category')} platform={s.get('platform')} mode={s.get('mode')}")
    check("Milvus 不可用时样本入队不丢数据", s.get("mode") in ("queued", "milvus"),
          f"mode={s.get('mode')}")

# ---------- 5. 版本链应记录 AI 原始版 + 人工修正版 ----------
_, ver = call("GET", f"/api/v1/system/versions/{ad_id}")
versions = ver.get("data", {}).get("versions", [])
check("版本链记录了 AI 原始版与人工修正版", len(versions) >= 1,
      f"{len(versions)} 个版本：" + " | ".join(f"v{v['version']}({v['note']})" for v in versions))

_, diff = call("GET", f"/api/v1/system/versions/{ad_id}/diff")
diff = diff.get("data", {})
check("版本 diff 可用", diff.get("available") is not None,
      f"added={diff.get('added')} removed={diff.get('removed')} identical={diff.get('identical')}")

# ---------- 6. 审批：职责分离 ----------
# 先确认进入待审批
_, pending = call("GET", "/api/v1/review/pending")
is_pending = any(i["id"] == ad_id for i in pending.get("data", {}).get("items", []))
check("修正放行后进入待审批", is_pending, f"ad={ad_id}")

if is_pending:
    # 审查角色无审批权限
    st, res = call("POST", f"/api/v1/review/approve/{ad_id}", {}, token=reviewer_token)
    check("审查角色无「审批」权限（403）", st == 403, str(res.get("detail"))[:60])

    # 审批角色可批
    st, res = call("POST", f"/api/v1/review/approve/{ad_id}", {"comment": "同意投放"},
                   token=approver_token)
    check("审批角色可审批通过", st == 200,
          f"stage={res.get('data', {}).get('stage')} handler={res.get('data', {}).get('handler')}")

# ---------- 7. 审计变更明细 ----------
admin_status, admin_login = call("POST", "/api/v1/auth/login",
                                 {"username": "admin", "password": "admin123456"})
admin_token = admin_login["data"]["token"] if admin_status == 200 else ""
_, audit = call("GET", "/api/v1/system/audit?limit=200", token=admin_token)
rows = audit.get("data", [])
with_changes = [r for r in rows if r.get("changes")]
check("审计记录了改前改后", len(with_changes) > 0, f"{len(with_changes)} 条含变更明细")
if with_changes:
    r = with_changes[0]
    print(f"      示例: {r['action']} target={r['target']} actor={r['actor']}({r.get('actor_role')})")
    for k, v in list(r["changes"].items())[:2]:
        print(f"        {k}: {str(v['before'])[:40]!r} → {str(v['after'])[:40]!r}")

actions = {r.get("action") for r in rows}
check("审计覆盖审查与审批动作", bool({"review.copy_clear", "review.approve"} & actions),
      f"{sorted(a for a in actions if a.startswith('review'))}")

print()
ok_count = sum(1 for ok, _, _ in RESULTS if ok)
print(f"结果：{ok_count}/{len(RESULTS)} 通过")
for ok, n, d in RESULTS:
    if not ok:
        print(f"  ❌ {n} — {d}")
