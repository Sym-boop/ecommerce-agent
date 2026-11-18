"""操作审计日志（追加写 JSONL，不可变）。

**为什么需要**

广告是要花钱且带合规风险的业务，出了事必须能回答三个问题：
**谁、在什么时候、对哪条素材做了什么、改前改后是什么**。
原先 `ads.json` 里只留了最终的 `handler` / `handled_at` / `comment`，
中间过程（谁放行了违禁词、谁改了预算、改成了多少）没有留痕，审计时对不上。

**设计**

- 追加写到 `<仓库根>/data/audit.jsonl`，每行一条 JSON。
- **追加写不重写整个文件**，所以不存在"写一半把历史写坏"的问题（也不需要临时文件替换）。
- 记录 actor（当前登录用户）、action、target、before/after 摘要。
- 审计写入失败**不阻断业务**（风控和主流程优先），但会打 ERROR 日志。
"""
from __future__ import annotations

import contextvars
import json
import logging
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("uvicorn.error")

_ROOT = Path(__file__).resolve().parents[3]
_AUDIT_REL = Path("data") / "audit.jsonl"

_LOCK = threading.Lock()

# 当前操作人：**必须用 ContextVar 而不是 threading.local**。
#
# 原因：FastAPI 会把同步（def）端点丢到 anyio 工作线程池执行，而线程是复用的。
# 用 threading.local 时，上一个请求设置的 actor 会残留在同一线程上，
# 导致 A 用户的操作被记成 B 用户 —— 实测复现过（运营发起的生成被记成审查员）。
# ContextVar 由 anyio 在每次请求任务开始时复制上下文，天然按请求隔离。
_actor_var: contextvars.ContextVar[dict[str, str]] = contextvars.ContextVar(
    "dsh_audit_actor", default={}
)


def set_actor(username: Optional[str], display_name: Optional[str] = None,
              role: Optional[str] = None) -> None:
    """设置当前请求的操作人（由鉴权中间件调用）。"""
    _actor_var.set({
        "username": username or "anonymous",
        "display_name": display_name or username or "匿名",
        "role": role or "",
    })


def current_actor() -> dict[str, str]:
    """取当前请求的操作人（未设置时返回匿名）。"""
    return _actor_var.get() or {"username": "anonymous", "display_name": "匿名", "role": ""}


# 敏感字段脱敏（避免审计日志里出现密钥）
_REDACT_KEYS = {"password", "token", "api_key", "apikey", "secret", "access_token", "refresh_token"}


def _path() -> Path:
    if _AUDIT_REL.is_absolute():
        return _AUDIT_REL
    return _AUDIT_REL if _AUDIT_REL.parent.exists() else _ROOT / _AUDIT_REL


def _redact(payload: Any, depth: int = 0) -> Any:
    """对敏感键脱敏，并限制嵌套深度避免日志爆炸。"""
    if depth > 4:
        return "…"
    if isinstance(payload, dict):
        out = {}
        for k, v in payload.items():
            if str(k).lower() in _REDACT_KEYS:
                out[k] = "***"
            else:
                out[k] = _redact(v, depth + 1)
        return out
    if isinstance(payload, list):
        return [_redact(v, depth + 1) for v in payload[:20]]
    if isinstance(payload, str) and len(payload) > 500:
        return payload[:500] + "…"
    return payload


def _diff(before: Optional[dict], after: Optional[dict]) -> dict[str, Any]:
    """计算变更字段（只记录真正变化的键）。"""
    if not isinstance(before, dict) or not isinstance(after, dict):
        return {}
    changed: dict[str, Any] = {}
    for key in set(before) | set(after):
        if key in {"fingerprint", "_deduped"}:
            continue
        b, a = before.get(key), after.get(key)
        if b != a:
            changed[key] = {"before": _redact(b), "after": _redact(a)}
    return changed


def record(action: str, *, target: str = "", target_type: str = "ad",
           before: Optional[dict] = None, after: Optional[dict] = None,
           detail: Optional[dict] = None, result: str = "ok") -> dict[str, Any]:
    """写一条审计记录，返回记录本身。

    action 取值约定（便于检索）：
        ad.register / ad.save / ad.pause / ad.discard
        review.copy_clear / review.budget_clear / review.approve / review.reject
        delivery.submit / batch.create / batch.save / batch.discard
        auth.login / auth.register / auth.logout
        rbac.denied / config.reload
    """
    actor = current_actor()
    entry: dict[str, Any] = {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "action": action,
        "result": result,
        "actor": actor.get("username"),
        "actor_display": actor.get("display_name"),
        "actor_role": actor.get("role"),
        "target": target,
        "target_type": target_type,
    }
    if detail:
        entry["detail"] = _redact(detail)
    changed = _diff(before, after)
    if changed:
        entry["changes"] = changed

    line = json.dumps(entry, ensure_ascii=False, default=str)
    try:
        path = _path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with _LOCK, path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError as exc:
        # 审计失败不阻断业务，但必须可见
        logger.error("审计日志写入失败（%s）: %s", exc, line[:200])
    return entry


def read(limit: int = 200, action: str = "", target: str = "",
         actor: str = "") -> list[dict[str, Any]]:
    """读取审计日志（倒序，最新的在前），支持按动作/对象/操作人过滤。"""
    path = _path()
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    try:
        with _LOCK, path.open("r", encoding="utf-8") as f:
            for raw in f:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    item = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if action and item.get("action") != action:
                    continue
                if target and item.get("target") != target:
                    continue
                if actor and item.get("actor") != actor:
                    continue
                rows.append(item)
    except OSError as exc:
        logger.error("审计日志读取失败: %s", exc)
        return []
    rows.reverse()
    return rows[:limit]


def stats() -> dict[str, Any]:
    """审计概览：总条数 + 按动作分组计数。"""
    rows = read(limit=100000)
    by_action: dict[str, int] = {}
    for r in rows:
        by_action[r.get("action", "?")] = by_action.get(r.get("action", "?"), 0) + 1
    return {
        "total": len(rows),
        "by_action": dict(sorted(by_action.items(), key=lambda kv: -kv[1])),
        "path": str(_path()),
        "latest": rows[0]["ts"] if rows else None,
    }
