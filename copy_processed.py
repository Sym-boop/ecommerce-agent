import csv
import json
from pathlib import Path

RAW_DIR = Path("data/raw")
PROC_DIR = Path("data/processed")
PROC_DIR.mkdir(exist_ok=True)

out_list = []

# 读取csv
with open(RAW_DIR / "copy_sample.csv", "r", encoding="utf‑8") as f:
    reader = csv.DictReader(f)
    for row in reader:
        title = row["title"].strip()
        content = row["content"].strip()
        platform = row["platform"].strip()
        category = row["product_category"].strip()
        tags = row["tags"].split(" ") if row["tags"] else []

        if not title or not content:
            continue

        # 【重点】这一段是拿去做向量embedding的文本
        vector_text = f"类目:{category} 平台:{platform} 标题:{title} 正文:{content}"

        item = {
            "text": vector_text,
            "platform": platform,
            "category": category,
            "original_title": title,
            "original_content": content,
            "tags": tags
        }
        out_list.append(item)

# 输出json
out_path = PROC_DIR / "copy_processed.json"
with open(out_path, "w", encoding="utf‑8") as fw:
    json.dump(out_list, fw, ensure_ascii=False, indent=2)

print(f"清洗完成，输出文件：{out_path}，共{len(out_list)}条")
