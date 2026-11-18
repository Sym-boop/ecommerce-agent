"""全链路联调脚本：登录 → 生成文案 → 存到投放 → 校验物料/历史（临时工具）。

在项目根目录运行：python probe_flow.py
"""

import json
import urllib.request
from collections import Counter
from pathlib import Path

BASE = "http://127.0.0.1:5173"


def call(path: str, payload: dict | None = None, token: str | None = None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(BASE + path, data=data, headers=headers,
                                 method="POST" if payload is not None else "GET")
    with urllib.request.urlopen(req, timeout=200) as r:
        return json.loads(r.read().decode("utf-8"))


out: dict = {}

# 1. 登录
login = call("/api/v1/auth/login", {"username": "demo", "password": "demo123456"})
token = login["data"]["token"]
out["login"] = {"user": login["data"]["user"]["username"], "ok": True}
print("1) 登录 OK:", login["data"]["user"]["display_name"])

# 2. 生成文案
gen = call("/api/v1/adgen/copy", {
    "product": {"name": "法式碎花连衣裙", "category": "连衣裙", "price": 159.0,
                "sell_points": ["垂感好", "显瘦", "度假风"]},
    "keyword": "约会穿搭", "platform": "xiaohongshu",
}, token)
sid = gen["data"]["session_id"]
copy = gen["data"].get("copy") or {}
out["copy"] = {"session_id": sid, "title": copy.get("title"), "tags": copy.get("tags")}
print("2) 文案 OK:", copy.get("title"))
print("   标签:", " ".join(copy.get("tags") or []))

# 3. 存到投放
try:
    saved = call("/api/v1/adgen/save", {"session_id": sid}, token)
    out["save"] = {"ok": True, "detail": str(saved.get("data"))[:200]}
    print("3) 存到投放 OK")
except Exception as exc:  # noqa: BLE001
    body = ""
    if getattr(exc, "read", None):
        body = exc.read().decode("utf-8", "replace")[:300]
    out["save"] = {"ok": False, "error": f"{exc} {body}"}
    print("3) 存到投放失败:", exc, body)

# 4. 校验物料与历史
ads = call("/api/v1/ads")["data"]
hist = call("/api/v1/history")["data"]
out["ads"] = {"count": len(ads), "stages": dict(Counter(a.get("stage") for a in ads))}
out["history"] = {"count": len(hist), "stages": dict(Counter(h.get("stage") for h in hist))}
print("4) 物料:", out["ads"])
print("   历史:", out["history"])

# 5. 审查统计
out["review_stats"] = call("/api/v1/review/stats")["data"]
print("5) 审查统计:", out["review_stats"])

# 6. 会话快照
snap = call(f"/api/v1/adgen/session/{sid}")["data"]
out["session_status"] = snap.get("status")
out["session_image_error"] = (snap.get("image") or {}).get("error")
print("6) 会话状态:", snap.get("status"))

Path("runtime").mkdir(exist_ok=True)
Path("runtime/flow_result.json").write_text(
    json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
print("\n结果已写入 runtime/flow_result.json")
