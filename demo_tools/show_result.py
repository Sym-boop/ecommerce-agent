"""查询一次广告生成会话并把结果写成 UTF-8 JSON 文件（临时工具）。

用法: python show_result.py <session_id>
"""

import json
import sys
import urllib.request
from pathlib import Path

sid = sys.argv[1]
with urllib.request.urlopen(f"http://127.0.0.1:8001/api/v1/adgen/session/{sid}", timeout=30) as r:
    data = json.loads(r.read().decode("utf-8"))["data"]

out = Path("runtime") / "last_result.json"
out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
print("written:", out)
print("status:", data.get("status"))
print("review:", json.dumps(data.get("review"), ensure_ascii=False))
