"""角色权限与职责分离（RBAC + Separation of Duties）。

**为什么需要**

系统原先任何登录用户都能审批，且**可以审批自己生成的广告**。
合规上这是硬伤：**自己生成、自己审批**等于没有审批，责任无法划分。
审计场景要求「生成、审查、审批」至少由不同角色承担。

**角色与权限**

| 角色 | 权限 |
|---|---|
| 运营 operator | 生成广告、存到投放、登记物料、发起投放 |
| 审查 reviewer | 违禁词 / 预算审查放行 |
| 审批 approver | 最终审批通过 / 驳回、投放提交 |
| 管理员 admin | 全部权限 + 查看审计与配置 |

**职责分离规则**

1. **禁止自审**：审批人不能审批「自己生成的」物料（`generated_by` 相同）。
2. **禁止自审（审查环节）**：审查放行同理。
3. admin 用于演示与运维，**不受自审限制**（否则单人环境无法演示），但会记录审计。

**兼容性**

用户表里 `role` 默认是「运营」。为了让演示/既有账号不被卡死，
`operator` 在缺少审批人时**看不到审批按钮但不会 500**；
权限不足统一返回 403，并写入审计（`rbac.denied`）。
"""
from __future__ import annotations

from typing import Iterable, Optional

# 权限点
PERM_GENERATE = "ad.generate"        # 生成 / 登记广告
PERM_REVIEW = "ad.review"            # 违禁词 / 预算审查放行
PERM_APPROVE = "ad.approve"          # 最终审批
PERM_DELIVER = "ad.deliver"          # 提交投放
PERM_ADMIN = "system.admin"          # 审计 / 配置 / 指标

ROLE_PERMISSIONS: dict[str, set[str]] = {
    "运营": {PERM_GENERATE, PERM_DELIVER},
    "审查": {PERM_REVIEW, PERM_GENERATE},
    "审批": {PERM_APPROVE, PERM_DELIVER},
    "管理员": {PERM_GENERATE, PERM_REVIEW, PERM_APPROVE, PERM_DELIVER, PERM_ADMIN},
    # 兼容英文角色名
    "operator": {PERM_GENERATE, PERM_DELIVER},
    "reviewer": {PERM_REVIEW, PERM_GENERATE},
    "approver": {PERM_APPROVE, PERM_DELIVER},
    "admin": {PERM_GENERATE, PERM_REVIEW, PERM_APPROVE, PERM_DELIVER, PERM_ADMIN},
}

# 不受「自审」限制的角色（演示/运维需要）
SANCTIONED_ROLES = {"管理员", "admin"}

ROLE_LABELS = {
    "运营": "运营（生成）",
    "审查": "审查（合规）",
    "审批": "审批（决策）",
    "管理员": "管理员",
}


def permissions_for(role: Optional[str]) -> set[str]:
    return ROLE_PERMISSIONS.get((role or "").strip(), set())


def has_permission(role: Optional[str], perm: str) -> bool:
    return perm in permissions_for(role)


def is_sanctioned(role: Optional[str]) -> bool:
    """是否为不受自审限制的角色。"""
    return (role or "").strip() in SANCTIONED_ROLES


class PermissionDenied(Exception):
    """权限不足。"""

    def __init__(self, message: str, *, code: int = 403) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def require(role: Optional[str], perm: str) -> None:
    """校验权限，不足则抛 `PermissionDenied`。"""
    if not has_permission(role, perm):
        raise PermissionDenied(
            f"当前角色「{role or '未设置'}」没有执行该操作的权限（需要 {perm}）"
        )


def require_not_self_review(role: Optional[str], actor: Optional[str],
                            generated_by: Optional[str], what: str = "审批") -> None:
    """职责分离：禁止对自己生成的物料做审查/审批。

    admin 角色豁免（单人演示环境），但仍会写审计。
    """
    if is_sanctioned(role):
        return
    if actor and generated_by and actor == generated_by:
        raise PermissionDenied(
            f"职责分离限制：不能{what}自己生成的广告（操作人 {actor} 同时是生成人）。"
            f"请由其他角色处理。"
        )


def describe() -> dict:
    """返回角色-权限矩阵（供前端渲染与文档说明）。"""
    return {
        "roles": [
            {
                "role": role,
                "label": ROLE_LABELS.get(role, role),
                "permissions": sorted(perms),
                "self_review_exempt": is_sanctioned(role),
            }
            for role, perms in ROLE_PERMISSIONS.items()
            if role in {"运营", "审查", "审批", "管理员"}
        ],
        "permissions": [
            {"key": PERM_GENERATE, "label": "生成 / 登记广告"},
            {"key": PERM_REVIEW, "label": "审查放行"},
            {"key": PERM_APPROVE, "label": "最终审批"},
            {"key": PERM_DELIVER, "label": "提交投放"},
            {"key": PERM_ADMIN, "label": "系统管理（审计 / 配置 / 指标）"},
        ],
        "rules": [
            "禁止审批自己生成的广告（职责分离）",
            "管理员角色豁免自审限制（便于单人演示），但仍写审计",
        ],
    }
