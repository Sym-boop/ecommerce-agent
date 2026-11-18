"""验证违禁词检测在运行目录下真的生效（临时工具）。

背景：check_banned_words() 读 <cwd>/data/raw/forbidden_words.txt，找不到文件会返回空列表，
表现为「所有文案都通过审查」——风控静默失效。本脚本验证该文件到位后检测能命中。

用法：python demo_tools/probe_banned.py
"""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "runtime"
sys.path.insert(0, str(ROOT / "backend"))
os.chdir(RUNTIME)  # 与后端启动方式一致

from app.common import pipeline  # noqa: E402
from app.common.milvus_cli import check_banned_words  # noqa: E402
from app.agent import copywriter  # noqa: E402

print("工作目录:", os.getcwd())
words_file = Path("data/raw/forbidden_words.txt")
print("违禁词表:", words_file, "存在" if words_file.exists() else "缺失")
print("词表行数:", len(copywriter._FORBIDDEN_WORDS))
print("-" * 60)

cases = [
    ("这件T恤是全网最好的选择，第一品牌", True),
    ("夏天穿这件很透气，日常通勤都合适", False),
    ("绝对正品，100%有效，国家级认证", True),
]
ok = True
for text, should_hit in cases:
    hits = check_banned_words(text)
    wrapped = pipeline.check_copy(text)
    hit = len(hits) > 0
    mark = "✅" if hit == should_hit else "❌"
    if hit != should_hit:
        ok = False
    print(f"{mark} 「{text[:22]}…」 命中={hit} 期望={should_hit}")
    if hits:
        print("     命中词:", [h['word'] for h in hits])
        print("     建议替换:", [h.get('replacement') or '(空)' for h in hits])
    print("     pipeline.check_copy ->", wrapped)

print("-" * 60)
print("结论:", "违禁词检测生效 ✅" if ok else "检测未按预期工作 ❌")
