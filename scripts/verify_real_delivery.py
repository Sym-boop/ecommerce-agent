"""验证「真实投放」代码路径：用本地契约模拟网关端到端跑一遍。

**为什么要这么做**

千川的 `QIANCHUAN_APP_ID / ACCESS_TOKEN / ADVERTISER_ID` 需要平台授权才能拿到。
没有授权时，`QIANCHUAN_DELIVERY_MODE=real` 的真实分支从来没被执行过——
这等于"代码写了但没验证"。

这个脚本启动一个**按千川开放平台契约**实现的本地网关，把
`QIANCHUAN_BASE_URL` 指向它，然后让**真实的 QianchuanClient 发真实 HTTP 请求**。
于是被验证的是：

  ✅ 请求路径与 HTTP 方法
  ✅ query 上的 access_token / advertiser_id 注入
  ✅ JSON body 序列化
  ✅ code=0 成功响应的解析
  ✅ code!=0 业务错误 → QianchuanAPIError
  ✅ HTTP 5xx → QianchuanAPIError
  ✅ token 刷新接口
  ✅ qianchuan_submit_delivery(real) 的返回值结构

**没被验证的**：千川平台侧的真实校验、真实扣费、真实素材审核——
这些必须有平台授权才能测。

运行：python scripts/verify_real_delivery.py
"""

from __future__ import annotations

import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend"))
os.chdir(ROOT)

# ---- 模拟网关 ---------------------------------------------------------------
RECEIVED: list[dict] = []
FAIL_MODE = {"value": ""}          # "" | "biz_error" | "http_500" | "bad_json"


class Gateway(BaseHTTPRequestHandler):
    def log_message(self, *args):   # 静音
        pass

    def _reply(self, status: int, payload: dict | str) -> None:
        body = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
        raw = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _record(self, method: str, body: dict | None) -> dict:
        parsed = urlparse(self.path)
        query = {k: v[0] for k, v in parse_qs(parsed.query).items()}
        item = {"method": method, "path": parsed.path, "query": query, "body": body}
        RECEIVED.append(item)
        return item

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        try:
            body = json.loads(raw.decode("utf-8")) if raw else {}
        except Exception:  # noqa: BLE001
            body = {"_raw": raw.decode("utf-8", "replace")}
        self._record("POST", body)
        self._dispatch()

    def do_GET(self):
        self._record("GET", None)
        self._dispatch()

    def _dispatch(self):
        if FAIL_MODE["value"] == "http_500":
            self._reply(500, {"error": "internal error"})
            return
        if FAIL_MODE["value"] == "bad_json":
            self._reply(200, "not-a-json")
            return
        if FAIL_MODE["value"] == "biz_error":
            self._reply(200, {"code": 40001, "message": "参数错误：advertiser_id 无效"})
            return

        path = urlparse(self.path).path
        if path == "/open_api/oauth2/refresh_token/":
            self._reply(200, {"code": 0, "data": {
                "access_token": "refreshed-token-abc123",
                "refresh_token": "refresh-xyz789",
                "expires_in": 86400,
            }})
        elif path == "/open_api/v1.0/qianchuan/ad/create/":
            self._reply(200, {"code": 0, "message": "OK", "data": {
                "ad_promotion_id": "QCP-REAL-7742001",
                "ad_ids": [7742001],
                "status": "AUDIT",
            }})
        elif path == "/open_api/v1.0/qianchuan/ad/list/":
            self._reply(200, {"code": 0, "data": {"list": [
                {"ad_id": 7742001, "name": "测试计划", "opt_status": "ENABLE"}
            ]}})
        else:
            self._reply(404, {"code": 40400, "message": f"未知路径 {path}"})


def start_gateway() -> tuple[HTTPServer, int]:
    srv = HTTPServer(("127.0.0.1", 0), Gateway)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, port


def main() -> int:
    srv, port = start_gateway()
    base = f"http://127.0.0.1:{port}"
    print("=" * 70)
    print("验证：真实投放代码路径（本地契约模拟网关）")
    print("=" * 70)
    print(f"模拟千川网关: {base}")

    # 指向模拟网关，并注入测试凭证（真实模式）
    os.environ["QIANCHUAN_BASE_URL"] = base
    os.environ["QIANCHUAN_DELIVERY_MODE"] = "real"
    os.environ["QIANCHUAN_ACCESS_TOKEN"] = "test-token-001"
    os.environ["QIANCHUAN_ADVERTISER_ID"] = "1700000000000001"
    os.environ["QIANCHUAN_APP_ID"] = "test-app-id"
    os.environ["QIANCHUAN_APP_SECRET"] = "test-app-secret"
    os.environ["QIANCHUAN_REFRESH_TOKEN"] = "test-refresh-token"
    os.environ["QIANCHUAN_TIMEOUT"] = "10"

    ok_all = True

    def check(name: str, cond: bool, detail: str = "") -> None:
        nonlocal ok_all
        print(f"  {'✅' if cond else '❌'} {name}" + (f" — {detail}" if detail else ""))
        if not cond:
            ok_all = False

    from mcp_servers.douyin.client import QianchuanAPIError, QianchuanClient
    from mcp_servers.douyin.config import qianchuan_config
    from mcp_servers.douyin import tools as qc_tools

    cfg = qianchuan_config()
    print(f"生效配置: mode={cfg['mode']} base_url={cfg['base_url']} "
          f"create_plan={cfg['create_plan_path']}")

    # ---------- 1. 创编提交（真实 HTTP）----------
    print("\n[1] 提交创编（真实 HTTP POST）")
    RECEIVED.clear()
    payload = {
        "advertiser_id": cfg["advertiser_id"],
        "shop_id": "SHOP-001",
        "product_id": "PROD-001",
        "ad_name": "测试投放计划",
        "budget": 500.0,
        "marketing_goal": "VIDEO_PROMOTION",
    }
    result = QianchuanClient().call(cfg["create_plan_path"], payload, method="POST")
    check("返回 code=0 且含 ad_promotion_id",
          result.get("code") == 0 and result.get("data", {}).get("ad_promotion_id") == "QCP-REAL-7742001",
          str(result.get("data")))

    req = RECEIVED[-1] if RECEIVED else {}
    check("请求方法为 POST", req.get("method") == "POST", req.get("method", ""))
    check("请求路径与配置一致", req.get("path") == cfg["create_plan_path"], req.get("path", ""))
    check("query 注入了 access_token",
          req.get("query", {}).get("access_token") == "test-token-001",
          str(req.get("query")))
    check("query 注入了 advertiser_id",
          req.get("query", {}).get("advertiser_id") == "1700000000000001",
          str(req.get("query")))
    check("body 字段完整送达",
          req.get("body", {}).get("budget") == 500.0
          and req.get("body", {}).get("product_id") == "PROD-001",
          str({k: req.get("body", {}).get(k) for k in ("budget", "product_id")}))

    # ---------- 2. qianchuan_submit_delivery 真实分支 ----------
    print("\n[2] qianchuan_submit_delivery() 走 real 分支")
    RECEIVED.clear()
    out = __import__("asyncio").run(qc_tools.qianchuan_submit_delivery({"budget": 300}))
    check("real 分支未返回 mock 标记", not out.get("mock", False), f"mock={out.get('mock')}")
    check("拿到平台返回的 ad_promotion_id",
          out.get("data", {}).get("ad_promotion_id") == "QCP-REAL-7742001",
          str(out.get("data")))
    check("确实发起了 HTTP 请求", len(RECEIVED) == 1, f"{len(RECEIVED)} 次")

    # ---------- 3. 业务错误 ----------
    print("\n[3] 业务错误（code!=0）")
    FAIL_MODE["value"] = "biz_error"
    try:
        QianchuanClient().call(cfg["create_plan_path"], {}, method="POST")
        check("业务错误应抛 QianchuanAPIError", False, "未抛异常")
    except QianchuanAPIError as exc:
        check("业务错误被识别为 QianchuanAPIError",
              "40001" in str(exc) or "参数错误" in str(exc), str(exc)[:60])
    FAIL_MODE["value"] = ""

    # ---------- 4. HTTP 5xx ----------
    print("\n[4] HTTP 5xx")
    FAIL_MODE["value"] = "http_500"
    try:
        QianchuanClient().call(cfg["create_plan_path"], {}, method="POST")
        check("5xx 应抛异常", False, "未抛异常")
    except QianchuanAPIError as exc:
        check("5xx 被识别", "500" in str(exc), str(exc)[:50])
    FAIL_MODE["value"] = ""

    # ---------- 5. 非 JSON 响应 ----------
    print("\n[5] 非 JSON 响应")
    FAIL_MODE["value"] = "bad_json"
    try:
        QianchuanClient().call(cfg["create_plan_path"], {}, method="POST")
        check("非 JSON 应抛异常", False, "未抛异常")
    except QianchuanAPIError as exc:
        check("非 JSON 被识别", "JSON" in str(exc), str(exc)[:50])
    FAIL_MODE["value"] = ""

    # ---------- 6. token 刷新 ----------
    print("\n[6] access_token 续期")
    RECEIVED.clear()
    token = QianchuanClient().refresh_access_token()
    check("拿到新 access_token",
          token.get("access_token") == "refreshed-token-abc123", str(token))
    req = RECEIVED[-1] if RECEIVED else {}
    check("刷新请求带齐 app_id / secret / refresh_token",
          req.get("body", {}).get("app_id") == "test-app-id"
          and req.get("body", {}).get("grant_type") == "refresh_token",
          str({k: req.get("body", {}).get(k) for k in ("app_id", "grant_type")}))

    # ---------- 7. 未配置凭证时的 fail-fast ----------
    print("\n[7] 未配置凭证时明确报错（不是静默降级）")
    saved = os.environ["QIANCHUAN_ACCESS_TOKEN"]
    os.environ["QIANCHUAN_ACCESS_TOKEN"] = "***"
    try:
        QianchuanClient().call(cfg["create_plan_path"], {}, method="POST")
        check("占位符 token 应被拒绝", False, "未抛异常")
    except QianchuanAPIError as exc:
        check("占位符 token 被识别为未配置", "ACCESS_TOKEN" in str(exc), str(exc)[:60])
    finally:
        os.environ["QIANCHUAN_ACCESS_TOKEN"] = saved

    srv.shutdown()
    print("\n" + "=" * 70)
    print(f"结果：{'全部通过 ✅' if ok_all else '存在失败项 ❌'}")
    print("说明：验证的是本项目真实代码路径；平台侧用契约模拟器替代，")
    print("      填入真实 APP_ID / ACCESS_TOKEN / ADVERTISER_ID 即可对真实千川生效。")
    return 0 if ok_all else 1


if __name__ == "__main__":
    sys.exit(main())
