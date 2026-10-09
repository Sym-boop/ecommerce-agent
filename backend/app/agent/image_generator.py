"""图文生成 Agent：把文案转成小红书风格图片。"""
import uuid
from pathlib import Path
from playwright.sync_api import sync_playwright

OUTPUT_DIR = Path("data/media/images")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


HTML_TEMPLATE = """
<html><body style="
    width: 1080px; height: 1440px; margin: 0;
    background: linear-gradient(135deg, #ffecd2 0%, #fcb69f 100%);
    padding: 80px 60px; font-family: 'Microsoft YaHei', sans-serif;
    display: flex; flex-direction: column; box-sizing: border-box;
">
    <div style="font-size: 72px; color: #ff6b6b; margin-bottom: 20px;">✨</div>
    <h1 style="font-size: 64px; font-weight: bold; color: #333;
        line-height: 1.3; margin-bottom: 40px;">
        {title}
    </h1>
    <p style="font-size: 36px; line-height: 1.8; color: #555;
        flex: 1; white-space: pre-wrap;">
        {content}
    </p>
    <div style="font-size: 30px; color: #ff6b6b; margin-top: 40px;">
        {tags}
    </div>
</body></html>
"""


def generate_image(title: str, content: str, tags: list) -> str:
    """生成单张图文，返回文件路径。"""
    html = HTML_TEMPLATE.format(
        title=title,
        content=content,
        tags=" ".join(tags) if isinstance(tags, list) else str(tags),
    )
    filename = OUTPUT_DIR / f"{uuid.uuid4().hex[:8]}.png"

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1080, "height": 1440})
        page.set_content(html)
        page.screenshot(path=str(filename))
        browser.close()

    return str(filename)


def image_generator_node(state: dict) -> dict:
    """Supervisor 调度入口。"""
    try:
        copy_result = state.get("copy_result") or {}
        if not copy_result.get("content"):
            return {"status": "failed", "error": "没有文案，无法生成图文"}

        image_path = generate_image(
            title=copy_result.get("title", ""),
            content=copy_result.get("content", ""),
            tags=copy_result.get("tags", []),
        )
        return {
            "image_result": {"image_path": image_path},
            "status": "done",
        }
    except Exception as e:
        return {"status": "failed", "error": f"图文生成失败：{e}"}