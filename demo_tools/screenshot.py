"""用 Playwright 登录并截图各个页面（现在 Chromium 可用了）。

用法：python demo_tools/screenshot.py [页面...]
默认截：登录页 + 登录后的全部主页面。
"""

import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:5173"
OUT = Path("runtime/shots")
OUT.mkdir(parents=True, exist_ok=True)

PAGES = [
    ("home", "/", "首页"),
    ("adgen", "/adgen", "广告生成"),
    ("batch", "/batch", "批量生成"),
    ("delivery", "/delivery", "广告投放"),
    ("history", "/history", "历史广告发售"),
    ("approvals", "/approvals", "审查中心"),
    ("recharge", "/recharge", "充值中心"),
    ("help", "/help", "帮助中心"),
]

errors: list[str] = []


def main() -> None:
    only = set(sys.argv[1:])

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1600, "height": 1000}, device_scale_factor=1)
        page.on("console", lambda m: errors.append(f"[console.{m.type}] {m.text}") if m.type == "error" else None)
        page.on("pageerror", lambda e: errors.append(f"[pageerror] {e}"))

        # 1) 登录页
        page.goto(f"{BASE}/login", wait_until="networkidle", timeout=60000)
        time.sleep(1.5)
        page.screenshot(path=str(OUT / "00-login.png"), full_page=True)
        print("截图 00-login.png")

        # 2) 登录：直接调接口拿 token 注入 localStorage，避免依赖表单选择器
        token = page.evaluate(
            """async () => {
                const r = await fetch('/api/v1/auth/login', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({username: 'demo', password: 'demo123456'})
                });
                const j = await r.json();
                if (j.code !== 0) return null;
                localStorage.setItem('dashboard_token', j.data.token);
                localStorage.setItem('dashboard_user', JSON.stringify(j.data.user));
                return j.data.token;
            }"""
        )
        if not token:
            print("!! 登录失败，无法继续")
            browser.close()
            return
        print("登录成功，token 已注入")

        # 3) 逐页截图
        for key, path, label in PAGES:
            if only and key not in only:
                continue
            page.goto(f"{BASE}{path}", wait_until="networkidle", timeout=60000)
            time.sleep(3)
            page.screenshot(path=str(OUT / f"{key}.png"), full_page=True)
            print(f"截图 {key}.png  ({label})")

        browser.close()

    if errors:
        print("\n=== 浏览器控制台报错 ===")
        for e in dict.fromkeys(errors):
            print(" ", e[:300])
    else:
        print("\n无控制台报错")


if __name__ == "__main__":
    main()
