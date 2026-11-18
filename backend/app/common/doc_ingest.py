"""文档解析与切块 —— RAG 建库的前置环节。

**为什么需要这个模块**

之前的语料是运营整理好的结构化 CSV（每条 130 字），整条就能当一个检索单元，
所以没有文档解析和切块。一旦要把「平台政策」「产品手册」「运营规范」这类
**非结构化长文档**也纳入知识库，就必须先解析再切块——一个 50 页的 PDF
不可能整篇塞进 Prompt。

**解析器是可插拔的**

| 优先级 | 解析器 | 适用 | 依赖 |
|---|---|---|---|
| 1 | **MinerU** | 复杂版式 PDF（多栏、表格、公式、扫描件） | `pip install mineru`（重） |
| 2 | **pypdf** | 常规文本层 PDF | 已装 |
| 3 | **python-docx** | Word（.docx） | 已装 |
| 4 | 内置 | txt / md / csv | 无 |

MinerU 未安装时自动回退到 pypdf——**不是不支持，是可插拔降级**。
返回结果里的 `parser` 字段会写明实际用了哪个，避免"以为用了 MinerU"。

**切块按 token 而不是字符**

中文里 1 个 token ≈ 1–1.5 个汉字，按字符切会让不同内容的块大小差异很大。
这里用 tiktoken 计数，默认 `chunk_size=512 / overlap=50`（约 10% 重叠），
两者都能用环境变量覆盖。

overlap 的作用：**避免语义在块的边界被切断**。上一块的尾部 50 token 会
重复出现在下一块开头，这样"半句话"至少在其中一块里是完整的。

**为什么不用固定字符切**

固定字符切会把句子拦腰截断。用 `RecursiveCharacterTextSplitter` 会优先按
段落 → 换行 → 句号 → 逗号 逐级尝试，**尽量在自然边界断开**。
"""
from __future__ import annotations

import csv
import io
import logging
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("uvicorn.error")

# 仓库根目录（doc_ingest.py 在 <root>/backend/app/common/ 下）
_REPO_ROOT = Path(__file__).resolve().parents[3]

# 默认切块参数（可用环境变量覆盖）
DEFAULT_CHUNK_SIZE = int(os.getenv("RAG_CHUNK_SIZE", "512"))
DEFAULT_CHUNK_OVERLAP = int(os.getenv("RAG_CHUNK_OVERLAP", "50"))

# 支持的扩展名
SUPPORTED_EXT = {".pdf", ".docx", ".txt", ".md", ".markdown", ".csv"}

# 文档切块单独用一个集合，避免和「爆款文案风格样本」混在一起污染召回
DOC_COLLECTION = os.getenv("RAG_DOC_COLLECTION", "doc_knowledge")


class DocParseError(RuntimeError):
    """文档解析失败。"""


@dataclass
class ParsedDoc:
    """解析结果。"""
    text: str
    parser: str                       # mineru | pypdf | python-docx | text
    source: str = ""                  # 文件名
    pages: int = 0                    # PDF 页数（其它类型为 0）
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def chars(self) -> int:
        return len(self.text)


@dataclass
class Chunk:
    """一个切块。"""
    text: str
    index: int                        # 第几块（从 0 开始）
    tokens: int                       # tiktoken 计数
    meta: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# 解析
# ---------------------------------------------------------------------------

def _token_count(text: str) -> int:
    """用 tiktoken 计 token；不可用时按字符数粗估（中文约 1.4 字/token）。"""
    try:
        import tiktoken

        enc = tiktoken.get_encoding("cl100k_base")
        return len(enc.encode(text))
    except Exception:  # noqa: BLE001
        return max(1, int(len(text) / 1.4))


def _tail_tokens(text: str, n: int) -> str:
    """取文本最后 n 个 token 对应的原文片段（用于块间 overlap）。

    **为什么不能直接 decode**

    token 边界可能切在多字节汉字中间，`enc.decode()` 会产出 `�`（替换字符）。
    实测块首出现过 `�单价，文案要匹配…` 这种脏字符——直接入库会污染语料。
    这里 decode 时忽略非法字节，再逐个丢首字符，直到结果是原文的真子串，
    保证返回的片段**一定能在原文里找到**。
    """
    if n <= 0 or not text:
        return ""
    try:
        import tiktoken

        enc = tiktoken.get_encoding("cl100k_base")
        ids = enc.encode(text)
        if len(ids) <= n:
            return ""
        tail = enc.decode(ids[-n:], errors="ignore").lstrip("\ufffd")
        # 收紧到「确实是原文子串」为止
        guard = 0
        while tail and tail not in text and guard < len(tail):
            tail = tail[1:]
            guard += 1
        return tail
    except Exception:  # noqa: BLE001
        return text[-max(1, int(n * 1.4)):]


def _mineru_bin() -> str:
    """定位 mineru 可执行文件。

    MinerU 依赖重（gradio / onnxruntime / modelscope 等），**不建议装进项目环境**，
    而是装在独立 venv 里，通过 `MINERU_BIN` 指向它的可执行文件：

        MINERU_BIN=<repo>/.venv-mineru/Scripts/mineru.exe
    """
    return os.getenv("MINERU_BIN", "mineru")


def _mineru_home() -> Path:
    """MinerU 的 HOME 目录。

    它要在 `~/.mineru` 下建 doclib 与模型缓存。若进程 HOME 不可写（沙箱/受控目录），
    会直接 `WinError 5 拒绝访问`。所以这里给一个明确可写的位置。
    """
    home = os.getenv("MINERU_HOME")
    if home:
        p = Path(home)
    else:
        # 默认放仓库内：系统临时目录在沙箱/受控环境下子进程可能无写权限
        p = _REPO_ROOT / ".mineru-home"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _mineru_run(args: list[str], *, timeout: float, env_home: Path):
    env = dict(os.environ)
    # 子进程的 HOME / USERPROFILE 都指向可写目录，否则 .mineru 建不出来
    env["USERPROFILE"] = str(env_home)
    env["HOME"] = str(env_home)
    return subprocess.run(
        [_mineru_bin(), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=timeout, env=env,
    )


def mineru_available() -> bool:
    """探测 MinerU 是否可用（二进制存在且能打印版本）。"""
    try:
        r = _mineru_run(["--version"], timeout=60, env_home=_mineru_home())
        return r.returncode == 0
    except Exception:  # noqa: BLE001
        return False


def _parse_mineru(path: Path) -> ParsedDoc:
    """用 MinerU 解析（4.x：本地服务 + CLI）。

    为什么值得优先用它：**表格和版式能保住**。实测同一份带表格的 PDF——
    pypdf 抽出来是「平台 / 建议时长 / 预算下限 …」一列裸文本，行列关系全丢；
    MinerU 输出的是 Markdown 表格，可以直接切块进检索。

    流程：
        1. `mineru parse <file> --format markdown -o <out.md>`
        2. 若报 server_not_running，`mineru server start` 后重试一次
        3. 读回 markdown
    """
    tier = os.getenv("MINERU_TIER", "flash")
    timeout = float(os.getenv("MINERU_TIMEOUT", "600"))
    home = _mineru_home()

    # 输出目录必须在**子进程可写**的位置。系统临时目录在沙箱下会 Permission denied，
    # 所以默认放仓库内（可用 MINERU_OUT_DIR 覆盖）。
    out_dir = Path(os.getenv("MINERU_OUT_DIR") or (_REPO_ROOT / ".mineru-out"))
    out_dir.mkdir(parents=True, exist_ok=True)

    out_md = out_dir / f"{path.stem}.md"
    if out_md.exists():
        try:
            out_md.unlink()
        except OSError:
            pass
    args = ["parse", str(path), "--tier", tier, "--format", "markdown",
            "-o", str(out_md), "--wait", str(int(timeout))]

    if True:

        try:
            r = _mineru_run(args, timeout=timeout + 60, env_home=home)
        except FileNotFoundError as exc:
            raise ImportError(
                f"找不到 mineru 可执行文件（MINERU_BIN={_mineru_bin()}）"
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise DocParseError(f"MinerU 解析超时（>{timeout}s）") from exc

        combined = f"{r.stdout}\n{r.stderr}"
        if "server_not_running" in combined or "本地 mineru 服务未运行" in combined:
            logger.info("MinerU 服务未运行，尝试启动后重试")
            _mineru_run(["server", "start"], timeout=180, env_home=home)
            r = _mineru_run(args, timeout=timeout + 60, env_home=home)
            combined = f"{r.stdout}\n{r.stderr}"

        if r.returncode != 0 and not out_md.exists():
            raise DocParseError(f"MinerU 解析失败（code={r.returncode}）: {combined[-400:]}")
        if not out_md.exists():
            raise DocParseError(f"MinerU 未产出 markdown: {combined[-400:]}")

        text = out_md.read_text(encoding="utf-8", errors="replace").strip()
        if not text:
            raise DocParseError("MinerU 输出为空")

        pages = 0
        import re as _re
        m = _re.search(r"page 1 of (\d+)", text)
        if m:
            pages = int(m.group(1))
        return ParsedDoc(text=text, parser=f"mineru({tier})", source=path.name, pages=pages)


def _parse_pdf(path: Path) -> ParsedDoc:
    """pypdf 解析：抽取文本层。扫描件（无文本层）会抽出空串，需上层判断。"""
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise DocParseError("未安装 pypdf（pip install pypdf）") from exc

    try:
        reader = PdfReader(str(path))
        parts: list[str] = []
        for page in reader.pages:
            try:
                parts.append(page.extract_text() or "")
            except Exception:  # noqa: BLE001
                parts.append("")
        text = "\n".join(parts).strip()
        if not text:
            raise DocParseError(
                "PDF 未抽取到文本层（可能是扫描件）。"
                "扫描件需要 MinerU 这类带 OCR 的解析器。"
            )
        return ParsedDoc(text=text, parser="pypdf", source=path.name, pages=len(reader.pages))
    except DocParseError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise DocParseError(f"PDF 解析失败: {exc}") from exc


def _parse_docx(path: Path) -> ParsedDoc:
    """python-docx 解析：段落 + 表格。"""
    try:
        import docx  # type: ignore
    except ImportError as exc:
        raise DocParseError("未安装 python-docx（pip install python-docx）") from exc

    try:
        d = docx.Document(str(path))
        lines = [p.text for p in d.paragraphs if p.text and p.text.strip()]
        # 表格行也拼进来，否则表格里的规范条目会丢
        for table in d.tables:
            for row in table.rows:
                cells = [c.text.strip() for c in row.cells if c.text and c.text.strip()]
                if cells:
                    lines.append(" | ".join(cells))
        text = "\n".join(lines).strip()
        if not text:
            raise DocParseError("docx 未抽取到文本")
        return ParsedDoc(text=text, parser="python-docx", source=path.name)
    except DocParseError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise DocParseError(f"docx 解析失败: {exc}") from exc


def _parse_text(path: Path) -> ParsedDoc:
    """纯文本类：txt / md / csv。"""
    raw = path.read_bytes()
    # utf-8-sig 放第一位：带 BOM 的文件（Windows 记事本/Out-File 常见）会
    # 在首行留下 \ufeff，导致标题识别和关键词匹配全部失效。
    for enc in ("utf-8-sig", "utf-8", "gbk"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise DocParseError("无法识别文本编码（试过 utf-8-sig / utf-8 / gbk）")

    text = text.lstrip("\ufeff")

    if path.suffix.lower() == ".csv":
        # CSV 逐行拼成「列名: 值」的可读文本，保留表头语义
        try:
            reader = csv.DictReader(io.StringIO(text))
            lines = []
            for row in reader:
                pairs = [f"{k}:{v}" for k, v in row.items() if k and v]
                if pairs:
                    lines.append(" ".join(pairs))
            text = "\n".join(lines)
        except Exception as exc:  # noqa: BLE001
            raise DocParseError(f"CSV 解析失败: {exc}") from exc

    return ParsedDoc(text=text.strip(), parser="text", source=path.name)


def parse_document(path: str | Path, *, prefer_mineru: bool = True) -> ParsedDoc:
    """解析任意支持的文档，返回纯文本 + 用了哪个解析器。

    `prefer_mineru=True` 时会先试 MinerU；未安装则**静默回退**并在
    `parser` 字段标注实际解析器。
    """
    p = Path(path)
    if not p.exists():
        raise DocParseError(f"文件不存在: {p}")
    ext = p.suffix.lower()
    if ext not in SUPPORTED_EXT:
        raise DocParseError(f"不支持的格式 {ext}，支持: {sorted(SUPPORTED_EXT)}")

    if ext == ".pdf":
        if prefer_mineru:
            try:
                return _parse_mineru(p)
            except ImportError:
                logger.info("MinerU 未安装，回退 pypdf 解析 %s", p.name)
            except DocParseError as exc:
                logger.warning("MinerU 解析失败（%s），回退 pypdf", exc)
        return _parse_pdf(p)
    if ext == ".docx":
        return _parse_docx(p)
    return _parse_text(p)


# ---------------------------------------------------------------------------
# 切块
# ---------------------------------------------------------------------------

_HEADING_PREFIXES = ("#", "##", "###", "第四章", "第三章", "第二章", "第一章", "第 4", "第 3", "第 2", "第 1")


def _detect_heading(chunk: str) -> str:
    """粗略抽一个标题：取首行，若是 markdown 标题或章节开头就当标题。"""
    first = (chunk.splitlines() or [""])[0].strip()
    if not first:
        return ""
    if first.startswith("#"):
        return first.lstrip("#").strip()[:60]
    if first.startswith(_HEADING_PREFIXES):
        return first[:60]
    return ""


def _split_units(text: str) -> list[str]:
    """把文本切成最小语义单元：段落 / 句子。

    保留分隔符（`(?<=X)` 后顾断言），这样拼回去还是原文。
    切分粒度从粗到细：段落 → 句号 → 感叹/问号 → 分号 → 换行。
    """
    import re

    # 先按段落切（双换行）
    paragraphs = re.split(r"\n\s*\n", text)
    units: list[str] = []
    for para in paragraphs:
        para = para.strip()
        if not para:
            continue
        # 段落内再按句末标点切，保留标点
        sentences = re.split(r"(?<=[。！？；!?;])\s*|\n", para)
        for s in sentences:
            s = s.strip()
            if s:
                units.append(s)
    return units


def chunk_text(
    text: str,
    *,
    chunk_size: int | None = None,
    overlap: int | None = None,
    meta: Optional[dict[str, Any]] = None,
) -> list[Chunk]:
    """按 **token** 切块，在自然边界断开，块间带 overlap。

    - `chunk_size` 默认取 `RAG_CHUNK_SIZE`（512 token）
    - `overlap` 默认取 `RAG_CHUNK_OVERLAP`（50 token，约 10%）
    - 原文短于 chunk_size 时**不切**，直接返回单块

    **为什么不直接用 langchain 的 `from_tiktoken_encoder`**

    实测该版本里它并没有按 token 计长，而是按字符——设 `chunk_size=512`
    出来的块只有 232 字符（约 258 token），块远小于预期。这里改为自己
    按 token 贪心累加，并对 overlap 做显式控制，行为可预测、可验证。
    """
    chunk_size = chunk_size or DEFAULT_CHUNK_SIZE
    overlap = overlap if overlap is not None else DEFAULT_CHUNK_OVERLAP
    if overlap >= chunk_size:
        raise ValueError(f"overlap({overlap}) 必须小于 chunk_size({chunk_size})")

    body = (text or "").strip()
    if not body:
        return []

    base_meta = dict(meta or {})
    total_tokens = _token_count(body)
    # 短文档不切：一条 130 字的文案本来就该整条用
    if total_tokens <= chunk_size:
        return [Chunk(text=body, index=0, tokens=total_tokens,
                      meta={**base_meta, "chunk_total": 1, "heading": _detect_heading(body)})]

    units = _split_units(body)
    if not units:
        return []

    pieces: list[str] = []
    cur: list[str] = []
    cur_tokens = 0

    for unit in units:
        t = _token_count(unit)
        # 单个单元就超过 chunk_size：先结算当前块，再把超长单元硬切片
        if t > chunk_size:
            if cur:
                pieces.append("\n".join(cur))
                cur, cur_tokens = [], 0
            step_chars = max(1, int(len(unit) * chunk_size / t))
            for i in range(0, len(unit), step_chars):
                pieces.append(unit[i:i + step_chars])
            continue

        if cur_tokens + t > chunk_size and cur:
            pieces.append("\n".join(cur))
            # 用**上一块的尾部 overlap 个 token** 作为下一块的开头。
            # 为什么不用「整句回退」：中文一句话常常就超过 50 token，
            # 整句回退会导致 overlap 时有时无（实测块0→块1 就没有）。
            # 直接按 token 截尾能保证每一块都有稳定重叠。
            seed = _tail_tokens("\n".join(cur), overlap)
            cur = [seed] if seed else []
            cur_tokens = _token_count(seed) if seed else 0

        cur.append(unit)
        cur_tokens += t + 1   # +1 给连接用的换行

    if cur:
        pieces.append("\n".join(cur))

    # 去空 + 去重（overlap 可能产生与上一块完全相同的块）
    chunks: list[Chunk] = []
    seen: set[str] = set()
    for piece in pieces:
        piece = piece.strip()
        if not piece or piece in seen:
            continue
        seen.add(piece)
        chunks.append(Chunk(
            text=piece,
            index=len(chunks),
            tokens=_token_count(piece),
            meta={**base_meta, "chunk_total": 0, "heading": _detect_heading(piece)},
        ))
    for c in chunks:
        c.meta["chunk_total"] = len(chunks)
    return chunks


# ---------------------------------------------------------------------------
# 端到端：解析 → 切块 → 向量化 → 入库
# ---------------------------------------------------------------------------

def ingest_file(
    path: str | Path,
    *,
    platform: str = "",
    category: str = "",
    chunk_size: int | None = None,
    overlap: int | None = None,
    collection: str = DOC_COLLECTION,
    prefer_mineru: bool = True,
    dry_run: bool = False,
) -> dict[str, Any]:
    """把一份文档灌进向量库。

    流程：解析 → 切块 → embedding → 写入 Milvus。

    `dry_run=True` 只做解析和切块、不写库——用于检查切块质量。
    """
    p = Path(path)
    doc = parse_document(p, prefer_mineru=prefer_mineru)

    chunks = chunk_text(
        doc.text,
        chunk_size=chunk_size,
        overlap=overlap,
        meta={
            "source": doc.source,
            "parser": doc.parser,
            "platform": platform,
            "category": category,
        },
    )
    if not chunks:
        raise DocParseError(f"切块结果为空: {p.name}")

    summary: dict[str, Any] = {
        "file": doc.source,
        "parser": doc.parser,
        "chars": doc.chars,
        "pages": doc.pages,
        "chunks": len(chunks),
        "chunk_size": chunk_size or DEFAULT_CHUNK_SIZE,
        "overlap": overlap if overlap is not None else DEFAULT_CHUNK_OVERLAP,
        "max_chunk_tokens": max(c.tokens for c in chunks),
        "min_chunk_tokens": min(c.tokens for c in chunks),
        "avg_chunk_tokens": round(sum(c.tokens for c in chunks) / len(chunks), 1),
        "dry_run": dry_run,
    }
    if dry_run:
        summary["preview"] = [
            {"index": c.index, "tokens": c.tokens, "heading": c.meta.get("heading", ""),
             "text": c.text[:120]}
            for c in chunks[:5]
        ]
        return summary

    from app.common import milvus_cli

    # Milvus 的 text 字段存切块正文；original_title 存标题或文件名
    payload = [
        {
            "text": c.text,
            "platform": platform,
            "category": category,
            "original_title": (c.meta.get("heading") or f"{doc.source}#{c.index}")[:200],
            "_doc": {
                "source": doc.source,
                "parser": doc.parser,
                "chunk_index": c.index,
                "chunk_total": len(chunks),
                "tokens": c.tokens,
            },
        }
        for c in chunks
    ]
    res = milvus_cli.insert_data(payload, collection_name=collection)
    summary["insert"] = res
    summary["mode"] = res.get("mode")
    return summary
