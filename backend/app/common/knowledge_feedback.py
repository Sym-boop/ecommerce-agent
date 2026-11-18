"""数据飞轮：把「人工修正并通过」的文案回写为检索样本。

**为什么这是最有价值的一项改进**

审查中心里运营手动改完文案并放行，这份**「人类认为更好的版本」是最宝贵的标注数据**——
它直接说明了"模型哪里不对、应该改成什么"。
原实现只把结果存进 `ads.json`，**没有任何回流**，这份数据白白浪费。

**闭环**

```
AI 生成 → 人工修正 → 通过审查与审批 → 回写向量库 → 下次同类目生成时被召回为参考
```

**降级策略**

Milvus 不可达时（本地开发常见）**不能丢数据**：
仍然把样本追加到 `data/knowledge_feedback.jsonl` 作为待入库队列与审计凭据，
并返回 `mode="queued"`；Milvus 恢复后可用 `flush_queue()` 补写。
"""
from __future__ import annotations

import json
import logging
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("uvicorn.error")

_ROOT = Path(__file__).resolve().parents[3]
_QUEUE_REL = Path("data") / "knowledge_feedback.jsonl"

_LOCK = threading.RLock()


def _resolve(path: Path) -> Path:
    if path.is_absolute():
        return path
    return path if path.parent.exists() else _ROOT / path


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _queue(entry: dict[str, Any]) -> None:
    """追加到待入库队列（JSONL 追加写，不重写文件）。"""
    try:
        path = _resolve(_QUEUE_REL)
        path.parent.mkdir(parents=True, exist_ok=True)
        with _LOCK, path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError as exc:
        logger.error("知识回流队列写入失败: %s", exc)


def _to_milvus(entry: dict[str, Any]) -> dict[str, Any]:
    """把样本写入 Milvus（失败则抛错，由调用方降级）。"""
    from app.common.milvus_cli import insert_data

    doc = {
        "text": entry["text"],
        "platform": entry.get("platform", ""),
        "category": entry.get("category", ""),
        "original_title": entry.get("title", ""),
        "source": "human_approved",
        "ad_id": entry.get("ad_id", ""),
        "approved_by": entry.get("actor", ""),
        "approved_at": entry.get("at", ""),
    }
    return insert_data([doc])


def contribute(*, ad_id: str, text: str, title: str = "", platform: str = "",
               category: str = "", actor: str = "", note: str = "") -> dict[str, Any]:
    """登记一条人工认可样本。

    返回: {"mode": "milvus"|"queued", "ad_id", "at", ...}
    """
    text = (text or "").strip()
    if not text:
        return {"mode": "skipped", "reason": "内容为空"}

    entry = {
        "at": _now(),
        "ad_id": ad_id,
        "text": text[:2000],
        "title": title or "",
        "platform": platform or "",
        "category": category or "",
        "actor": actor or "",
        "note": note or "",
    }

    # 无论 Milvus 是否可用都先落队列（审计凭据 + 待补写）
    try:
        result = _to_milvus(entry)
        entry["mode"] = "milvus"
        entry["insert"] = result
        logger.info("人工认可样本已回写向量库 ad=%s", ad_id)
    except Exception as exc:  # noqa: BLE001
        entry["mode"] = "queued"
        entry["error"] = str(exc)[:200]
        logger.warning("向量库写入失败，样本已入队待补写 ad=%s: %s", ad_id, exc)

    # 落队列放在最后：此时 entry 已带上真实的 mode，
    # 否则重启后从队列读出来的记录会缺 mode（观测不到"到底入没入库"）
    _queue(entry)
    return entry


def queue_size() -> int:
    """待补写队列长度。"""
    path = _resolve(_QUEUE_REL)
    if not path.exists():
        return 0
    try:
        with path.open("r", encoding="utf-8") as f:
            return sum(1 for line in f if line.strip())
    except OSError:
        return 0


def read_queue(limit: int = 200) -> list[dict[str, Any]]:
    """读取队列（倒序）。"""
    path = _resolve(_QUEUE_REL)
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8") as f:
            for raw in f:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    rows.append(json.loads(raw))
                except json.JSONDecodeError:
                    continue
    except OSError:
        return []
    rows.reverse()
    return rows[:limit]


def flush_queue() -> dict[str, Any]:
    """把队列里的样本补写进向量库（Milvus 恢复后调用）。

    成功写入的样本不会从队列删除（队列同时是审计凭据），
    但会标记 `flushed_at`，避免重复补写。
    """
    rows = read_queue(limit=100000)
    pending = [r for r in rows if not r.get("flushed_at")]
    if not pending:
        return {"attempted": 0, "succeeded": 0, "failed": 0, "mode": "empty"}

    succeeded = failed = 0
    last_error = ""
    for entry in pending:
        try:
            _to_milvus(entry)
            succeeded += 1
        except Exception as exc:  # noqa: BLE001
            failed += 1
            last_error = str(exc)[:200]

    mode = "milvus" if succeeded else "queued"
    if succeeded:
        try:
            path = _resolve(_QUEUE_REL)
            marked: list[str] = []
            flushed_at = _now()
            for entry in rows:
                if not entry.get("flushed_at"):
                    entry["flushed_at"] = flushed_at
                marked.append(json.dumps(entry, ensure_ascii=False))
            path.write_text("\n".join(marked) + "\n", encoding="utf-8")
        except OSError as exc:
            logger.warning("队列补写标记失败: %s", exc)

    return {
        "attempted": len(pending),
        "succeeded": succeeded,
        "failed": failed,
        "mode": mode,
        "error": last_error,
    }


def stats() -> dict[str, Any]:
    """数据飞轮概览。"""
    rows = read_queue(limit=100000)
    flushed = sum(1 for r in rows if r.get("flushed_at"))
    return {
        "total_samples": len(rows),
        "pending_flush": len(rows) - flushed,
        "flushed": flushed,
        "queue_path": str(_resolve(_QUEUE_REL)),
        "latest": rows[0]["at"] if rows else None,
    }
