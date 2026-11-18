"""联调探针：走通「生成图文 → 存到投放 → 历史记录」全链路（临时工具）。

在项目根目录运行：python probe_asset.py <session_id>
"""

import json
import sys
import time
import urllib.request
from pathlib import Path

BASE = "http://127.0.0.1:8001"
sid = sys.argv[1] if len(sys.argv) > 1 else (Path("runtime") / "_last_sid.txt").read_text().strip()


def post_json(path: str, payload: dict) -> dict:
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=180) as r:
        return json.loads(r.read().decode("utf-8"))


def get(path: str) -> dict:
    with urllib.request.urlopen(BASE + path, timeout=60) as r:
        return json.loads(r.read().decode("utf-8"))


def post_multipart(path: str, fields: dict[str, str]) -> dict:
    boundary = "----dshprobe"
    parts = []
    for k, v in fields.items():
        parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n")
    body = ("".join(parts) + f"--{boundary}--\r\n").encode("utf-8")
    req = urllib.request.Request(
        BASE + path, data=body, method="POST",
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    with urllib.request.urlopen(req, timeout=180) as r:
        return json.loads(r.read().decode("utf-8"))


print(f"session_id = {sid}")
print("=" * 60)

# ---- 1. 生成图文 ----
print("[1] 提交图文生成任务 ...")
try:
    res = post_multipart("/api/v1/adgen/asset", {"session_id": sid, "type": "image", "keyword": "夏季穿搭"})
    print("    ", json.dumps(res, ensure_ascii=False)[:300])
    task_id = (res.get("data") or {}).get("task_id") or (res.get("data") or {}).get("id")
except Exception as exc:  # noqa: BLE001
    print("    失败:", exc)
    task_id = None

# ---- 2. 轮询状态 ----
if task_id:
    print(f"[2] 轮询任务 {task_id} ...")
    for i in range(36):
        time.sleep(5)
        try:
            st = get(f"/api/v1/adgen/asset/status/{task_id}")
        except Exception as exc:  # noqa: BLE001
            print("    查询失败:", exc)
            break
        d = st.get("data") or {}
        print(f"    第{i + 1}次: status={d.get('status')} url={d.get('url') or d.get('image_url') or '-'}")
        if d.get("status") in ("done", "success", "failed", "error"):
            break

# ---- 3. 会话快照 ----
print("[3] 会话快照 ...")
snap = get(f"/api/v1/adgen/session/{sid}")["data"]
print("    status =", snap.get("status"))
print("    image  =", json.dumps(snap.get("image"), ensure_ascii=False)[:200])

# ---- 4. 存到投放 ----
print("[4] 存到投放界面 ...")
try:
    saved = post_json("/api/v1/adgen/save", {"session_id": sid})
    print("    ", json.dumps(saved, ensure_ascii=False)[:300])
except Exception as exc:  # noqa: BLE001
    print("    失败:", exc)

# ---- 5. 验证投放候选与历史 ----
print("[5] 校验广告物料 / 历史记录 ...")
ads = get("/api/v1/ads")["data"]
hist = get("/api/v1/history")["data"]
print(f"    广告物料 {len(ads)} 条, 历史 {len(hist)} 条")
stage_count: dict[str, int] = {}
for a in ads:
    stage_count[a.get("stage")] = stage_count.get(a.get("stage"), 0) + 1
print("    物料阶段分布:", stage_count)
if hist:
    newest = hist[0]
    print("    最新历史:", newest.get("id"), "|", (newest.get("product") or {}).get("name"), "|", newest.get("stage"))
