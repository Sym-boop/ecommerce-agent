"""充值中心 API（最小实现，仅提供「可看」的账单 / 充值接口）。

用途：为图文生成 Agent 的独立 API 提供账户余额与点数充值展示。
后端仅做 Mock 记账（落盘到 <仓库根>/data/billing.json），不接入真实支付渠道。

端点：
    GET  /api/v1/billing/account    账户概览（余额 / 点数 / 图文 API 配额）
    GET  /api/v1/billing/packages   充值套餐列表
    GET  /api/v1/billing/invoices   充值/消费流水（时间倒序）
    POST /api/v1/billing/recharge   充值（Mock，返回新余额）
"""
from __future__ import annotations

import threading
import uuid
from datetime import datetime

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.common import json_store

router = APIRouter(prefix="/api/v1/billing", tags=["billing"])

_LOCK = threading.Lock()
_BILLING_FILE = "billing.json"

# 充值套餐（元 → 点数 + 赠送比例）
PACKAGES = [
    {"id": "pkg-100", "label": "体验包", "amount": 100, "credits": 10000, "bonus_rate": 0, "tag": ""},
    {"id": "pkg-500", "label": "标准包", "amount": 500, "credits": 50000, "bonus_rate": 10, "tag": "热销"},
    {"id": "pkg-1000", "label": "专业包", "amount": 1000, "credits": 100000, "bonus_rate": 20, "tag": "推荐"},
    {"id": "pkg-5000", "label": "旗舰包", "amount": 5000, "credits": 500000, "bonus_rate": 30, "tag": "最划算"},
]

# 图文生成 Agent 的独立 API 计费口径（每次生成消耗点数）
UNIT_COST = {"image": 20, "video": 80, "copy": 5}


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


_DEFAULT_ACCOUNT = {
    "account_id": "ACC-1000001",
    "balance": 500.0,          # 账户余额（元）
    "credits": 20000,          # 可用点数
    "total_recharged": 500.0,  # 累计充值（元）
    "updated_at": _now_iso(),
}

_DEFAULT_INVOICES: list[dict] = [
    {"id": "INV-0000002", "type": "consume", "title": "图文生成消耗", "amount": 0, "credits": -120,
     "created_at": _now_iso(), "status": "已扣减"},
    {"id": "INV-0000001", "type": "recharge", "title": "新用户赠送", "amount": 0, "credits": 20000,
     "created_at": _now_iso(), "status": "已完成"},
]

# 账户与流水落盘到 <仓库根>/data/billing.json，进程重启后不丢
_billing = json_store.read_dict(_BILLING_FILE, {})
_ACCOUNT: dict = _billing.get("account") or dict(_DEFAULT_ACCOUNT)
_INVOICES: list[dict] = _billing.get("invoices") or list(_DEFAULT_INVOICES)


def _persist() -> None:
    """把账户与流水原子写盘（调用方需持有 _LOCK）。"""
    json_store.write_json(_BILLING_FILE, {"account": _ACCOUNT, "invoices": _INVOICES})


def ok(data=None) -> dict:
    return {"code": 0, "msg": "ok", "data": data}


def fail(status: int, msg: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"code": status, "msg": msg})


@router.get("/account")
async def billing_account():
    """账户概览：余额、点数、计费口径（供充值页展示）。"""
    with _LOCK:
        snapshot = dict(_ACCOUNT)
    return ok({**snapshot, "unit_cost": UNIT_COST})


@router.get("/packages")
async def billing_packages():
    """充值套餐列表。"""
    return ok(PACKAGES)


@router.get("/invoices")
async def billing_invoices():
    """充值 / 消费流水（时间倒序）。"""
    with _LOCK:
        return ok(sorted(_INVOICES, key=lambda r: r["created_at"], reverse=True))


@router.post("/recharge")
async def billing_recharge(payload: dict | None = None):
    """充值（Mock）：按套餐或自定义金额增加余额与点数。"""
    body = payload or {}
    package_id = str(body.get("package_id") or "").strip()
    pkg = next((p for p in PACKAGES if p["id"] == package_id), None)
    if pkg is None:
        return fail(400, "请选择有效的充值套餐")

    credits = int(pkg["credits"] * (1 + pkg["bonus_rate"] / 100))
    invoice = {
        "id": f"INV-{uuid.uuid4().hex[:7].upper()}",
        "type": "recharge",
        "title": f"充值 {pkg['label']}",
        "amount": float(pkg["amount"]),
        "credits": credits,
        "created_at": _now_iso(),
        "status": "已完成",
    }
    with _LOCK:
        _ACCOUNT["balance"] = round(_ACCOUNT["balance"] + pkg["amount"], 2)
        _ACCOUNT["credits"] += credits
        _ACCOUNT["total_recharged"] = round(_ACCOUNT["total_recharged"] + pkg["amount"], 2)
        _ACCOUNT["updated_at"] = _now_iso()
        _INVOICES.insert(0, invoice)
        _persist()
        snapshot = dict(_ACCOUNT)

    return ok({"account": snapshot, "invoice": invoice,
               "message": f"充值成功，到账 {credits:,} 点数"})