"""用户鉴权 API（真实用户表，SQLite 落盘）。

    POST /api/v1/auth/register  JSON: { username, password, display_name? } → { token, user }
    POST /api/v1/auth/login     JSON: { username, password }               → { token, user }
    GET  /api/v1/auth/me        Header: Authorization: Bearer <token>      → user

用户表落盘 backend/data/app.db（stdlib sqlite3，无需额外依赖）；
密码使用 PBKDF2-HMAC-SHA256 加盐哈希存储，登录签发随机 token 存入 sessions 表，
前端保存到 localStorage('dashboard_token') 并随请求头携带（与 api/request.js 对齐）。

登录时若用户名不存在，返回 404 且提示「账号不存在，请先注册」，引导用户注册。
"""
from __future__ import annotations

import hashlib
import secrets
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Header
from fastapi.responses import JSONResponse

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])

# backend/app/api/auth_router.py → parents[2] = backend
_DB_PATH = Path(__file__).resolve().parents[2] / "data" / "app.db"
_LOCK = threading.Lock()
_PBKDF2_ROUNDS = 120_000


def ok(data=None) -> dict:
    return {"code": 0, "msg": "ok", "data": data}


def fail(status: int, msg: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"code": status, "msg": msg})


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _connect() -> sqlite3.Connection:
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _init_db() -> None:
    with _LOCK, _connect() as conn:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                salt TEXT NOT NULL,
                display_name TEXT DEFAULT '',
                role TEXT DEFAULT '运营',
                created_at TEXT NOT NULL
            )"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS sessions (
                token TEXT PRIMARY KEY,
                username TEXT NOT NULL,
                created_at TEXT NOT NULL
            )"""
        )
        conn.commit()


def _hash_password(password: str, salt: str) -> str:
    dk = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), bytes.fromhex(salt), _PBKDF2_ROUNDS,
    )
    return dk.hex()


def _public_user(row: sqlite3.Row) -> dict:
    return {
        "username": row["username"],
        "display_name": row["display_name"] or row["username"],
        "role": row["role"],
        "created_at": row["created_at"],
    }


def _issue_token(username: str) -> str:
    token = secrets.token_urlsafe(32)
    with _LOCK, _connect() as conn:
        conn.execute(
            "INSERT INTO sessions(token, username, created_at) VALUES (?,?,?)",
            (token, username, _now_iso()),
        )
        conn.commit()
    return token


def _find_user(username: str) -> sqlite3.Row | None:
    with _LOCK, _connect() as conn:
        return conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()


def current_user(authorization: str | None) -> dict | None:
    """从 Authorization 头解析 Bearer token 并返回用户信息（无则 None）。"""
    token = ""
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
    if not token:
        return None
    with _LOCK, _connect() as conn:
        row = conn.execute("SELECT username FROM sessions WHERE token=?", (token,)).fetchone()
        if row is None:
            return None
        user = conn.execute("SELECT * FROM users WHERE username=?", (row["username"],)).fetchone()
    return _public_user(user) if user else None


@router.post("/register")
async def register(payload: dict | None = None):
    body = payload or {}
    username = str(body.get("username") or "").strip()
    password = str(body.get("password") or "")
    display_name = str(body.get("display_name") or "").strip()

    if len(username) < 3:
        return fail(400, "用户名至少 3 个字符")
    if len(password) < 6:
        return fail(400, "密码至少 6 位")

    if _find_user(username) is not None:
        return fail(409, "该用户名已被注册，请直接登录")

    salt = secrets.token_hex(16)
    password_hash = _hash_password(password, salt)
    with _LOCK, _connect() as conn:
        conn.execute(
            "INSERT INTO users(username, password_hash, salt, display_name, role, created_at)"
            " VALUES (?,?,?,?,?,?)",
            (username, password_hash, salt, display_name, "运营", _now_iso()),
        )
        conn.commit()

    user = _find_user(username)
    token = _issue_token(username)
    return ok({"token": token, "user": _public_user(user)})


@router.post("/login")
async def login(payload: dict | None = None):
    body = payload or {}
    username = str(body.get("username") or "").strip()
    password = str(body.get("password") or "")

    if not username or not password:
        return fail(400, "请输入用户名和密码")

    user = _find_user(username)
    if user is None:
        # 明确提示用户不存在，引导前往注册
        return fail(404, "账号不存在，请先注册")

    if _hash_password(password, user["salt"]) != user["password_hash"]:
        return fail(401, "密码错误，请重试")

    token = _issue_token(username)
    return ok({"token": token, "user": _public_user(user)})


@router.get("/me")
async def me(authorization: str | None = Header(default=None)):
    user = current_user(authorization)
    if user is None:
        return fail(401, "登录态无效或已过期，请重新登录")
    return ok(user)


@router.post("/logout")
async def logout(authorization: str | None = Header(default=None)):
    token = ""
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
    if token:
        with _LOCK, _connect() as conn:
            conn.execute("DELETE FROM sessions WHERE token=?", (token,))
            conn.commit()
    return ok({"ok": True})


_init_db()