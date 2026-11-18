"""违禁词检测（单一事实来源，fail-closed 语义）。

**为什么要独立成模块**

原先的检测有两个实测缺陷：

1. **子串误报**：`word in text` 无词边界，"最近入手" 会因为词表里的单字「最」被判命中；
   且 `最` / `最好` / `最佳` 并存时同一条文案会重复报多条冗余结果。
2. **fail-open（故障放行）**：词表文件读不到时返回空列表，调用方再套一层
   `except: hits = []`，结果是**所有文案都被判定「审查通过」**。
   风控依赖失效时放行违规内容，代价不可逆，属于方向性错误。

**本模块的策略**

- 检测：jieba 分词 + 词边界规则（单字词只在完整 token 上匹配，避免「最近」「最后」误伤）；
  多字词做子串匹配但按「最长优先 + 命中区间去重」消除冗余。
- 词表格式：`词` 或 `词|分类|建议替换`（向后兼容原有的纯词行）。
- 替换建议：词表行内给不出时，回落到 `data/raw/forbidden_replacements.json` 的映射表。
- 可用性：提供 `available()` / `status()`；**词表加载失败时由调用方 fail-closed**，
  不再静默返回空列表。
"""
from __future__ import annotations

import json
import logging
import re
import sys
import threading
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("uvicorn.error")

# backend/app/common/banned_words.py → parents[3] = 仓库根目录
_ROOT = Path(__file__).resolve().parents[3]

# 词表与替换建议的默认路径（相对当前工作目录，与项目其他模块一致）
_WORDS_REL = Path("data") / "raw" / "forbidden_words.txt"
_REPLACEMENTS_REL = Path("data") / "raw" / "forbidden_replacements.json"

_LOCK = threading.RLock()


class BannedWordsUnavailable(RuntimeError):
    """词表不可用（文件缺失 / 解析失败）。

    调用方应把它当作 **fail-closed 信号**：拒绝生成或阻止推进，而不是当作「无违禁词」。
    """


# ---------------------------------------------------------------------------
# 词条与状态
# ---------------------------------------------------------------------------

class _Entry:
    """一条违禁词规则。"""

    __slots__ = ("word", "category", "replacement", "basis", "single_char")

    def __init__(self, word: str, category: str, replacement: str,
                 basis: str = "") -> None:
        self.word = word
        self.category = category
        self.replacement = replacement
        # 依据：这一条为什么禁（对应哪条法规/平台规范）。
        # 词表最容易缺的就是这个字段 —— 没有它，被问「这个词凭什么违禁」就答不出。
        self.basis = basis
        # 单字词（如「最」）只在完整分词 token 上命中，避免「最近 / 最后」误伤
        self.single_char = len(word) == 1


_ENTRIES: list[_Entry] = []
_REPLACEMENTS: dict[str, str] = {}
_LOADED = False
_LOAD_ERROR: Optional[str] = None
_SOURCE: Optional[str] = None


def _resolve(path: Path) -> Path:
    """相对路径按当前工作目录解析；再回落到仓库根目录（兼容两种启动方式）。"""
    if path.is_absolute():
        return path
    if path.exists():
        return path
    return _ROOT / path


def _has_cjk(text: str) -> bool:
    return any("\u4e00" <= ch <= "\u9fff" for ch in text)


def _load_replacements() -> dict[str, str]:
    """加载「违禁词 → 建议替换」映射表（可缺省）。"""
    fp = _resolve(_REPLACEMENTS_REL)
    if not fp.exists():
        return {}
    try:
        raw = json.loads(fp.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("违禁词替换建议表解析失败（忽略）: %s", exc)
        return {}
    if not isinstance(raw, dict):
        return {}
    return {str(k).strip(): str(v) for k, v in raw.items() if str(k).strip()}


def _parse_line(line: str) -> Optional[_Entry]:
    """解析一行词表：`词` 或 `词|分类|建议替换`。"""
    line = line.strip()
    if not line or line.startswith("#"):
        return None
    if "|" in line:
        parts = [p.strip() for p in line.split("|")]
        word = parts[0]
        if not word:
            return None
        category = parts[1] if len(parts) > 1 and parts[1] else "违禁词"
        replacement = parts[2] if len(parts) > 2 else ""
        basis = parts[3] if len(parts) > 3 else ""
        return _Entry(word, category, replacement, basis)
    return _Entry(line, "违禁词", "")


def _load(force: bool = False) -> None:
    """加载词表；失败时记录原因（由调用方决定 fail-closed）。"""
    global _ENTRIES, _REPLACEMENTS, _LOADED, _LOAD_ERROR, _SOURCE
    with _LOCK:
        if _LOADED and not force:
            return

        _ENTRIES = []
        _LOAD_ERROR = None
        _SOURCE = None

        fp = _resolve(_WORDS_REL)
        if not fp.exists():
            _LOAD_ERROR = f"违禁词表不存在: {fp}"
            _LOADED = True
            logger.error("违禁词表缺失，风控将 fail-closed: %s", fp)
            return

        try:
            entries: list[_Entry] = []
            with fp.open("r", encoding="utf-8") as f:
                for line in f:
                    entry = _parse_line(line)
                    if entry is not None:
                        entries.append(entry)
        except OSError as exc:
            _LOAD_ERROR = f"违禁词表读取失败: {exc}"
            _LOADED = True
            logger.error("违禁词表读取失败，风控将 fail-closed: %s", exc)
            return

        replacements = _load_replacements()
        for entry in entries:
            # 词表行内未给建议时，回落到映射表
            if not entry.replacement:
                entry.replacement = replacements.get(entry.word, "")

        _ENTRIES = entries
        _REPLACEMENTS = replacements
        _SOURCE = str(fp)
        _LOADED = True
        # 与 status()['replacement_count'] 口径一致（都是"有建议的词条数"）
        with_suggestion = sum(1 for e in entries if e.replacement)
        logger.info("违禁词表已加载: %d 条（替换建议 %d 条）", len(_ENTRIES), with_suggestion)


# ---------------------------------------------------------------------------
# 对外状态接口
# ---------------------------------------------------------------------------

def available() -> bool:
    """词表是否可用（不可用时调用方必须 fail-closed）。"""
    _load()
    return _LOAD_ERROR is None


def status() -> dict[str, Any]:
    """词表状态，供 /health 与审查中心配置展示。"""
    _load()
    return {
        "available": _LOAD_ERROR is None,
        "source": _SOURCE,
        "word_count": len(_ENTRIES),
        "replacement_count": sum(1 for e in _ENTRIES if e.replacement),
        # 依据与分类的覆盖度：用于确认词表是否"可审计"
        "categorized_count": sum(1 for e in _ENTRIES if e.category != "违禁词"),
        "with_basis_count": sum(1 for e in _ENTRIES if e.basis),
        "error": _LOAD_ERROR,
    }


def reload() -> dict[str, Any]:
    """热加载词表（改完词表文件无需重启服务）。"""
    _load(force=True)
    return status()


def words() -> list[str]:
    """返回全部违禁词（供兼容旧调用）。"""
    _load()
    return [e.word for e in _ENTRIES]


# ---------------------------------------------------------------------------
# 检测
# ---------------------------------------------------------------------------

_TOKENIZER: Any = None
_TOKENIZER_TRIED = False


def _tokenize(text: str) -> set[str]:
    """jieba 分词结果集合；jieba 不可用时回退为空集（此时单字词不参与匹配）。"""
    global _TOKENIZER, _TOKENIZER_TRIED
    if not _TOKENIZER_TRIED:
        _TOKENIZER_TRIED = True
        try:
            import jieba  # type: ignore

            jieba.setLogLevel(logging.WARNING)
            _TOKENIZER = jieba
        except Exception as exc:  # noqa: BLE001
            logger.warning("jieba 不可用，单字违禁词将退化为不匹配: %s", exc)
            _TOKENIZER = None
    if _TOKENIZER is None or not _has_cjk(text):
        return set()
    try:
        return set(_TOKENIZER.lcut(text))
    except Exception:  # noqa: BLE001
        return set()


def check(text: str, *, strict: bool = True) -> list[dict[str, Any]]:
    """扫描文本，返回命中明细。

    参数:
        strict: True 时词表不可用会抛 `BannedWordsUnavailable`（fail-closed）；
                False 时返回空列表（仅供不做风控的展示场景）。

    返回: [{"word", "category", "replacement", "start", "end", "context"}]
    """
    if not text:
        return []

    _load()
    if _LOAD_ERROR is not None:
        if strict:
            raise BannedWordsUnavailable(_LOAD_ERROR)
        return []

    if not _ENTRIES:
        return []

    tokens = _tokenize(text)
    hits: list[dict[str, Any]] = []

    # 1) 单字词：只在完整分词 token 上命中（避免「最近」「最后」误伤）
    for entry in _ENTRIES:
        if not entry.single_char or entry.word not in tokens:
            continue
        start = text.find(entry.word)
        hits.append(_hit(entry, text, start))

    # 2) 多字词：最长优先 + 命中区间去重（消除「最」⊂「最好」这类冗余）
    spans = _tokenize_spans(text)
    multi = sorted((e for e in _ENTRIES if not e.single_char), key=lambda e: -len(e.word))
    covered: list[tuple[int, int]] = []
    for entry in multi:
        for m in re.finditer(re.escape(entry.word), text):
            span = (m.start(), m.end())
            if any(span[0] < c_end and c_start < span[1] for c_start, c_end in covered):
                continue  # 已被更长的词覆盖
            # 命中若**严格落在单个分词 token 内部**则拒绝。
            # 实测误报：「第一」在「第一次」「第一眼」里被当子串命中（jieba 把
            # 「第一次」切成一个 token）。而「销量第一的洗发水」里「第一」自成
            # token，属于真命中 —— 用 token 边界区分两者。
            if _inside_single_token(span, spans):
                continue
            covered.append(span)
            hits.append(_hit(entry, text, span[0]))

    # 按出现位置排序，便于前端逐处处置
    hits.sort(key=lambda h: (h["start"], -len(h["word"])))
    return hits


def _tokenize_spans(text: str) -> list[tuple[int, int]]:
    """jieba 分词并返回每个 token 的 (start, end) 偏移。

    `_tokenize` 只返回词集合，做边界判断需要偏移量。
    jieba 不可用时返回空列表（此时不做边界过滤，退化为纯子串匹配）。
    """
    global _TOKENIZER, _TOKENIZER_TRIED
    _tokenize(text)   # 复用初始化逻辑
    if _TOKENIZER is None or not _has_cjk(text):
        return []
    try:
        return [(int(s), int(e)) for _w, s, e in _TOKENIZER.tokenize(text) if _w.strip()]
    except Exception:  # noqa: BLE001
        return []


def _inside_single_token(span: tuple[int, int], tokens: list[tuple[int, int]]) -> bool:
    """判断命中区间是否严格落在某个 token 内部（不等于该 token）。"""
    start, end = span
    for tok_start, tok_end in tokens:
        if tok_start <= start and end <= tok_end and (tok_start < start or end < tok_end):
            return True
    return False


def _hit(entry: _Entry, text: str, start: int) -> dict[str, Any]:
    end = start + len(entry.word) if start >= 0 else -1
    context = ""
    if start >= 0:
        lo = max(0, start - 8)
        hi = min(len(text), end + 8)
        context = text[lo:hi]
    return {
        "word": entry.word,
        "category": entry.category,
        "replacement": entry.replacement or "",
        "basis": entry.basis or "",
        "start": start,
        "end": end,
        "context": context,
    }


def filter_words(text: str) -> list[str]:
    """只返回命中的词（供 copywriter 的轻量调用）。"""
    return [h["word"] for h in check(text, strict=False)]


# 模块导入时预热（不抛异常，失败状态记录在 status() 里）
try:
    _load()
except Exception as exc:  # noqa: BLE001
    print(f"[banned_words] 词表预热失败: {exc}", file=sys.stderr)
