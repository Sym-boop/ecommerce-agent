"""鉴权与权限依赖（FastAPI Depends）。

职责：
1. 从 `Authorization: Bearer <token>` 解析当前用户（复用 auth_router 的会话表）；
2. 把操作人写入 `audit` 的线程本地存储，供业务层记录「谁做的」；
3. 提供基于角色的权限校验依赖（RBAC），并在越权时写审计。

**为什么把 actor 放在这里**

业务层（pipeline / adgen_router）需要知道"谁在操作"才能做职责分离与审计，
但它们不应该关心 HTTP 头解析。放在依赖里注入，业务层只管调用
`audit.current_actor()` 或接收 `actor` 参数。

**宽松与严格的取舍**

当前系统是内部运营工具，历史账号的 `role` 多为「运营」。
为了让既有账号不被一次性卡死：
- `current_user_optional`：拿不到用户也不报错（用于不强制登录的读接口）
- `require_perm(perm)`：**严格**校验，越权返回 403 并写审计
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import HTTPException

from app.common import audit, metrics, rbac

logger = logging.getLogger("uvicorn.error")


class Actor:
    """当前请求的操作人。"""

    __slots__ = ("username", "display_name", "role")

    def __init__(self, username: str = "", display_name: str = "", role: str = "") -> None:
        self.username = username
        self.display_name = display_name or username
        self.role = role

    @property
    def is_anonymous(self) -> bool:
        return not self.username

    def to_dict(self) -> dict:
        return {"username": self.username, "display_name": self.display_name, "role": self.role}

    def has(self, perm: str) -> bool:
        return rbac.has_permission(self.role, perm)


def resolve_actor(authorization: Optional[str]) -> Actor:
    """解析 Bearer token → Actor；解析失败返回匿名 Actor（不抛错）。

    由**中间件**在每个请求开始时调用，把结果写入 audit 的 ContextVar。
    """
    username = display_name = role = ""
    try:
        from app.api.auth_router import current_user

        user = current_user(authorization)
        if user:
            username = str(user.get("username") or "")
            display_name = str(user.get("display_name") or username)
            role = str(user.get("role") or "")
    except Exception as exc:  # noqa: BLE001
        logger.debug("解析登录态失败（按匿名处理）: %s", exc)

    actor = Actor(username, display_name, role)
    audit.set_actor(actor.username or None, actor.display_name, actor.role)
    return actor


def actor_from_context() -> Actor:
    """从 ContextVar 读取当前操作人（中间件已写入，不做鉴权副作用）。

    **注意**：不要在依赖里重新解析 token —— FastAPI 的同步依赖会跑在工作线程，
    而中间件跑在请求任务上下文，重复解析会把 ContextVar 覆盖成同一份数据，
    真正的问题是用 threading.local 时串号（已改 ContextVar）。
    """
    data = audit.current_actor()
    return Actor(
        username=data.get("username") if data.get("username") != "anonymous" else "",
        display_name=data.get("display_name") or "",
        role=data.get("role") or "",
    )


async def current_actor() -> Actor:
    """FastAPI 依赖：当前操作人（匿名也不报错）。"""
    return actor_from_context()


async def current_user_required() -> Actor:
    """FastAPI 依赖：要求已登录，否则 401。"""
    actor = actor_from_context()
    if actor.is_anonymous:
        raise HTTPException(status_code=401, detail="登录态无效或已过期，请重新登录")
    return actor


def require_perm(perm: str):
    """生成一个权限校验依赖。

    用法::

        @router.post("/approve")
        async def approve(actor: Actor = Depends(require_perm(rbac.PERM_APPROVE))):
            ...
    """

    async def _dep() -> Actor:
        actor = actor_from_context()
        if not actor.has(perm):
            metrics.incr("rbac_denied")
            audit.record("rbac.denied", target=perm, target_type="permission",
                         detail={"role": actor.role, "required": perm,
                                 "actor": actor.username or "anonymous"},
                         result="denied")
            raise HTTPException(
                status_code=403,
                detail=f"当前角色「{actor.role or '未设置'}」无权执行该操作（需要 {perm}）",
            )
        return actor

    return _dep


def enforce_separation_of_duties(actor: Actor, generated_by: Optional[str], what: str = "审批") -> None:
    """职责分离校验：禁止审批自己生成的物料（管理员豁免但写审计）。"""
    try:
        rbac.require_not_self_review(actor.role, actor.username, generated_by, what)
    except rbac.PermissionDenied as exc:
        metrics.incr("sod_denied")
        audit.record("rbac.self_review_denied", target=generated_by or "",
                     target_type="user",
                     detail={"actor": actor.username, "role": actor.role, "what": what},
                     result="denied")
        raise HTTPException(status_code=403, detail=exc.message) from exc
