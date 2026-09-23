from pydantic import BaseModel
from pathlib import Path
from typing import List

# ========== 和 requirements.md 保持完全一致 ==========
class CopyWriterInput(BaseModel):
    product_name: str
    product_sell_points: List[str]
    product_category: str
    platform: str
    language: str = "zh‑CN"
    reference_count: int = 3

class CopyWriterResult(BaseModel):
    title: str
    content: str
    tags: List[str]
    platform: str
    language: str
    forbidden_hit: List[str]
    reference_snippets: List[str]

# ========= 工具1：违禁词过滤 =========
_FORBIDDEN_WORDS: List[str] = []

def load_forbidden_words():
    global _FORBIDDEN_WORDS
    fp = Path("data/raw/forbidden_words.txt")
    _FORBIDDEN_WORDS.clear()
    if fp.exists():
        with open(fp, "r", encoding="utf‑8") as f:
            for line in f:
                w = line.strip()
                if w:
                    _FORBIDDEN_WORDS.append(w)

load_forbidden_words()

def filter_forbidden_words(text: str) -> List[str]:
    hit = []
    for w in _FORBIDDEN_WORDS:
        if w in text:
            hit.append(w)
    return hit

# ========= 工具2：向量检索（现在mock，等B的milvus_cli就绪再打开真实逻辑） =========
# from backend.app.common.milvus_cli import search

def retrieve_similar_copy(category: str, top_k: int):
    # ---------------- mock 模拟返回参考文案 ----------------
    mock_docs = [
        "颜值超高的保温杯，出门携带轻便，保温效果不错",
        "水杯密封性很好，夏天喝水不漏水，很适合日常通勤"
    ]
    return mock_docs

    # ========= 等B准备好milvus_cli之后，注释上面mock，启用下面真实代码 =========
    # res = search(query_text=category, top_k=top_k)
    # return [item["text"] for item in res]

# ========= 工具3：构建Prompt（MVP只做xiaohongshu） =========
def build_prompt(input_data: CopyWriterInput, references: List[str]) -> str:
    ref_text = "\n".join([f"-{r}" for r in references])
    prompt = f"""
你是小红书种草文案博主。
商品名称：{input_data.product_name}
商品卖点：{"；".join(input_data.product_sell_points)}
商品类目：{input_data.product_category}

参考爆款文案：
{ref_text}

要求：写小红书风格种草文案，带emoji，结尾输出话题标签。严禁使用广告违禁词。
输出格式：
标题：xxx
正文：xxx
标签：#xxx #xxx
"""
    return prompt

# ========= 主入口函数，给Supervisor调用 =========
def generate_copy(input_data: CopyWriterInput) -> CopyWriterResult:
    # 1、召回参考文案
    reference_snippets = retrieve_similar_copy(
        category=input_data.product_category,
        top_k=input_data.reference_count
    )

    # 2、组装prompt
    prompt = build_prompt(input_data, reference_snippets)

    # 3、大模型【mock】，后面替换成真实LLM调用
    mock_output_title = f"{input_data.product_name}｜真的太好用啦✨"
    mock_output_content = f"实测这款{input_data.product_name}！\n{'；'.join(input_data.product_sell_points)}\n日常使用很合适～"
    mock_tags = ["#好物分享", f"#{input_data.product_category}"]

    full_text = mock_output_title + mock_output_content

    # 4、违禁词检测
    forbidden_hit = filter_forbidden_words(full_text)

    result = CopyWriterResult(
        title=mock_output_title,
        content=mock_output_content,
        tags=mock_tags,
        platform=input_data.platform,
        language=input_data.language,
        forbidden_hit=forbidden_hit,
        reference_snippets=reference_snippets
    )
    return result

# ========= 本地自测入口 =========
if __name__ == "__main__":
    test_input = CopyWriterInput(
        product_name="316不锈钢保温杯",
        product_sell_points=["24小时保温", "轻便", "密封性好"],
        product_category="水杯",
        platform="xiaohongshu"
    )
    out = generate_copy(test_input)
    print(out.model_dump_json(indent=2))
