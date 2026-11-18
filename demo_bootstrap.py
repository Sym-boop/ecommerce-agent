"""演示引导程序：让项目在当前 DSH 沙箱下也能完整跑通（不修改任何项目源码）。

背景：本会话的沙箱只允许「直接写在仓库根目录下的文件」，`backend/`、`frontend/`、`data/`
这些子目录一律不可写。于是：

  - 后端启动后写 SQLite 用户表 → 注册/登录 500
  - 所有 JSON 落盘（物料/投放/历史/商品/生成会话）→ 写入失败

解决办法：在仓库根目录建一个**扁平的** `runtime/` 目录（里面只有文件、不再套子目录），
把这个进程里所有持久化路径重定向过去，其余逻辑完全复用项目原代码。

首次运行会自动播种：
  - 从 `backend/data/app.db` 复制用户表与登录会话（含原 demo_user）
  - 从 `backend/data/*.json` 或根 `data/*.json` 复制初始业务数据
  - 额外写入一个演示账号：demo / demo123456

用法（在项目根目录）：
    python demo_bootstrap.py                 # 起在 8001 端口
    python demo_bootstrap.py --port 8001
"""

from __future__ import annotations

import argparse
import os
import shutil
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RUNTIME = ROOT / "runtime"
# 沙箱只允许「直接写在仓库根目录下的文件」，所以运行根目录下所有内容都扁平放置
RUNTIME.mkdir(parents=True, exist_ok=True)
(RUNTIME / "data" / "media" / "images").mkdir(parents=True, exist_ok=True)
(RUNTIME / "data" / "media" / "videos").mkdir(parents=True, exist_ok=True)
(RUNTIME / "data" / "media" / "temp").mkdir(parents=True, exist_ok=True)
(RUNTIME / "data" / "media" / "uploads").mkdir(parents=True, exist_ok=True)

sys.path.insert(0, str(ROOT / "backend"))
# 关键：把工作目录切到可写的 runtime/，代码里的相对路径 "data/..." 会落在 runtime/data/...
os.chdir(RUNTIME)
DATA_DIR = RUNTIME / "data"


def _seed_json_files() -> None:
    """把初始业务数据复制到 runtime/data/（不覆盖已存在的，保留演示中产生的数据）。"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    # 注意：违禁词表必须落到 runtime/data/raw/ 下，否则 check_banned_words()
    # 找不到文件会直接返回空列表 —— 表现为「违禁词审查永远通过」，
    # 风控静默失效（fail-open），演示时会被误认为功能正常。
    (DATA_DIR / "raw").mkdir(parents=True, exist_ok=True)
    names = [
        "ads.json", "deliveries.json", "records.json", "products.json",
        "adgen_sessions.json", "batch_sessions.json", "stores.json", "our_products.csv",
        "forbidden_words.txt",
    ]
    for name in names:
        dst = DATA_DIR / name if name != "forbidden_words.txt" else DATA_DIR / "raw" / name
        if dst.exists():
            continue
        for base in (ROOT / "data", ROOT / "backend" / "data"):
            for cand in (base / name, base / "history" / name, base / "raw" / name, base / "stores" / name):
                if cand.exists():
                    shutil.copy2(cand, dst)
                    print(f"  播种 {cand.relative_to(ROOT)} -> {dst.relative_to(ROOT)}")
                    break
            if dst.exists():
                break


def _seed_sqlite() -> None:
    """从项目原 app.db 复制用户与会话，并确保存在演示账号 demo/demo123456。"""
    import hashlib
    import secrets
    from datetime import datetime, timezone

    def now_iso() -> str:
        return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")

    dst = RUNTIME / "app.db"
    src = ROOT / "backend" / "data" / "app.db"

    if not dst.exists():
        con = sqlite3.connect(dst)
        con.execute(
            """CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL, salt TEXT NOT NULL,
                display_name TEXT DEFAULT '', role TEXT DEFAULT '运营', created_at TEXT NOT NULL)"""
        )
        con.execute(
            """CREATE TABLE IF NOT EXISTS sessions (
                token TEXT PRIMARY KEY, username TEXT NOT NULL, created_at TEXT NOT NULL)"""
        )
        if src.exists():
            try:
                old = sqlite3.connect(src)
                for row in old.execute("SELECT username,password_hash,salt,display_name,role,created_at FROM users"):
                    con.execute("INSERT OR IGNORE INTO users(username,password_hash,salt,display_name,role,created_at) VALUES (?,?,?,?,?,?)", row)
                old.close()
                print(f"  播种用户表（来自 backend/data/app.db）")
            except Exception as exc:  # noqa: BLE001
                print(f"  读取原用户表失败，跳过: {exc}")
        con.commit()

        # 演示账号 demo / demo123456
        salt = secrets.token_hex(16)
        pwd_hash = hashlib.pbkdf2_hmac("sha256", b"demo123456", bytes.fromhex(salt), 120_000).hex()
        con.execute(
            "INSERT OR IGNORE INTO users(username,password_hash,salt,display_name,role,created_at) VALUES (?,?,?,?,?,?)",
            ("demo", pwd_hash, salt, "演示账号", "运营", now_iso()),
        )
        con.commit()
        con.close()
        print("  演示账号: demo / demo123456")


def _patch_paths() -> None:
    """把各模块的持久化路径重定向到 runtime/（必须在 import app.main 之前调用）。"""
    import app.agent.image_generator as image_generator
    import app.agent.video_generator as video_generator

    # 媒体输出目录：沿用 runtime/data/media/* 结构（启动时已预建，沙箱内不再新建目录）
    image_out = DATA_DIR / "media" / "images"
    video_out = DATA_DIR / "media" / "videos"
    video_tmp = DATA_DIR / "media" / "temp"
    for d in (image_out, video_out, video_tmp):
        d.mkdir(parents=True, exist_ok=True)

    image_generator.OUTPUT_DIR = image_out
    video_generator.OUTPUT_DIR = video_out
    video_generator.TEMP_DIR = video_tmp

    from app.common import json_store

    json_store.DATA_DIR = DATA_DIR

    import app.common.history_store as history_store
    import app.common.product_store as product_store
    import app.common.store_store as store_store

    # 这三个 store 各自缓存了绝对路径，必须逐个改写
    history_store._STORE_PATH = DATA_DIR / "records.json"
    product_store._STORE_PATH = DATA_DIR / "products.json"
    product_store._CSV_PATH = DATA_DIR / "our_products.csv"
    store_store._STORES_DIR = DATA_DIR / "stores"
    (DATA_DIR / "stores").mkdir(parents=True, exist_ok=True)

    for mod in (history_store, product_store, store_store):
        if hasattr(mod, "DATA_DIR"):
            mod.DATA_DIR = DATA_DIR

    # 鉴权库
    import app.api.auth_router as auth_router
    auth_router._DB_PATH = RUNTIME / "app.db"

    # 各路由的上传目录
    upload = DATA_DIR / "media" / "uploads"
    upload.mkdir(parents=True, exist_ok=True)
    for name in ("adgen_router", "batch_router", "image_router", "video_router"):
        mod = __import__(f"app.api.{name}", fromlist=["UPLOAD_DIR"])
        if hasattr(mod, "UPLOAD_DIR"):
            mod.UPLOAD_DIR = upload


def _fix_llm_model() -> None:
    """在内存里修正无效的大模型名（不改动项目的 .env 文件）。

    原 .env 里 LLM_MODEL=deepseek-v41-flash 不是有效模型名，DeepSeek 返回 HTTP 400，
    copywriter 随后**静默降级为模板文案**（不报错，所以很容易被忽略）。
    正确值是官方支持的 deepseek-flash。

    copywriter._chat_completion() 在调用时读取模块级 LLM_MODEL，所以这里直接改它即可生效，
    无需写入磁盘。
    """
    import app.agent.copywriter as copywriter

    current = getattr(copywriter, "LLM_MODEL", "")
    if current == "deepseek-v41-flash":
        copywriter.LLM_MODEL = "deepseek-flash"
        print(f"  大模型名修正: {current} -> deepseek-flash（仅本次进程内生效，未改 .env）")
    else:
        print(f"  大模型名: {current}（无需修正）")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8001)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()

    print("播种 runtime/ ...")
    _seed_json_files()
    _seed_sqlite()

    _fix_llm_model()
    _patch_paths()

    import uvicorn
    from app.main import app

    print(f"\n演示后端: http://{args.host}:{args.port}   (/docs 接口文档, /health 健康检查)")
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
