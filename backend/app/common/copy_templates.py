"""爆款文案模版：把「平台风格」拆成「平台 × 结构」两个维度。

**为什么要有这个模块**

原来平台风格写死在 `copywriter.PLATFORM_PROMPTS` 里，是**一个平台一个大 prompt**。
问题不在于 prompt 写得好不好，而在于：

1. **结构不可控**——同一个商品生成三次，开头、段数、收尾每次都不一样。
   运营没法做 A/B，因为"变好"和"换了个随机结构"分不清。
2. **不能按目标选**——新品冷启动要「痛点开场」，清库存要「价格锚定」，
   但模型只会按它理解的平台调性自由发挥。
3. **改一处影响全部**——想给某次投放换个结构，只能改 prompt，改了所有商品一起变。

所以拆成两层：
- **平台**管"语气"（小红书要口语真实感、抖音要快节奏、Amazon 要 SEO）——留在 PLATFORM_PROMPTS
- **模版**管"结构"（开头怎么写、几段、有没有 CTA）——放这里

调用方传 `template_id`，我们只把**结构约束**追加到 system prompt 末尾，
语气部分仍由平台决定。这样两个维度可以正交组合。

**为什么模版写在代码里而不是数据库**

模版是 prompt 工程的一部分，需要跟代码一起 review、一起回滚；
放数据库会让"某次生成用的哪版模版"变得不可追溯。等模版数量超过几十个、
需要运营自助编辑时再迁到配置表，那时应该同时加版本号与生效时间。
"""
from __future__ import annotations

from typing import Any

# 每个模版：id / platform / name / goal（适用目标）/ structure（结构约束，注入 system prompt）
TEMPLATES: list[dict[str, Any]] = [
    # ---------------- 小红书 ----------------
    {
        "id": "xhs_pain",
        "platform": "xiaohongshu",
        "name": "痛点开场",
        "goal": "新品冷启动：先用共鸣把人留住",
        "structure": (
            "结构要求（严格按此）：\n"
            "1) 标题：把用户的具体困扰写出来，让人一眼觉得「说的是我」，不用感叹号堆砌\n"
            "2) 正文第 1 段：描述一个具体的、有画面的痛点场景（时间/地点/动作）\n"
            "3) 第 2 段：转折——我怎么发现这个商品的\n"
            "4) 第 3 段：用了之后的真实变化（要具体，不要形容词堆叠）\n"
            "5) 结尾：一句轻量的建议，不要硬推销"
        ),
    },
    {
        "id": "xhs_compare",
        "platform": "xiaohongshu",
        "name": "测评对比",
        "goal": "用户在做选择：用对比建立信任",
        "structure": (
            "结构要求（严格按此）：\n"
            "1) 标题：体现「对比」而不是「推荐」，例如换过几个之后我留下了这个\n"
            "2) 第 1 段：说明我为什么纠结、对比了哪几类（不要点名具体品牌）\n"
            "3) 第 2 段：分维度列对比（价格/材质/耐用度/使用手感），每维度 1-2 句\n"
            "4) 第 3 段：我最终选它的理由，要承认它也有不足\n"
            "5) 结尾：适合什么人不适合什么人——这句话能显著提升可信度"
        ),
    },
    {
        "id": "xhs_scene",
        "platform": "xiaohongshu",
        "name": "场景代入",
        "goal": "生活方式内容：弱化卖货感",
        "structure": (
            "结构要求（严格按此）：\n"
            "1) 标题：写一个具体场景，不写商品名\n"
            "2) 正文：围绕一个完整的日常片段展开（通勤/办公室/居家/出门），"
            "商品作为场景里的一个道具自然出现\n"
            "3) 至少写一个感官细节（温度、声音、触感、气味）\n"
            "4) 不出现「推荐」「必买」「安利」这类直接推销词\n"
            "5) 结尾落在情绪或状态上，不落在商品上"
        ),
    },
    {
        "id": "xhs_share",
        "platform": "xiaohongshu",
        "name": "自用分享",
        "goal": "已有信任：朋友式分享",
        "structure": (
            "结构要求（严格按此）：\n"
            "1) 标题：口语化，像跟朋友说话（可以用「我」「终于」「居然」）\n"
            "2) 第 1 段：交代用了多久，建立时间可信度\n"
            "3) 第 2 段：讲一个具体的使用细节（别人不会注意但很关键的那种）\n"
            "4) 第 3 段：提一个真实的小缺点\n"
            "5) 结尾：一句话总结值不值，不用力过猛"
        ),
    },
    {
        "id": "xhs_list",
        "platform": "xiaohongshu",
        "name": "清单体",
        "goal": "信息密度优先：快速扫读",
        "structure": (
            "结构要求（严格按此）：\n"
            "1) 标题：带数字，说明这是几条信息（如 5 个细节）\n"
            "2) 正文用编号列表，每条 1-2 句，每条讲一个独立的事实或用法\n"
            "3) 至少有一条是「怎么用更好」的实用技巧\n"
            "4) 不要写成长段落，允许短句和分行\n"
            "5) 结尾一句话收束即可"
        ),
    },
    # ---------------- 抖音 ----------------
    {
        "id": "dy_hook3s",
        "platform": "tiktok",
        "name": "3 秒钩子",
        "goal": "信息流抢注意力",
        "structure": (
            "结构要求（严格按此）：\n"
            "Hook（0-3 秒）：一句话制造信息缺口或反常识，禁止铺垫\n"
            "Problem（3-10 秒）：一个具体到能看见的痛点场景\n"
            "Solution（10-25 秒）：商品怎么解决，只讲一个核心机制\n"
            "CTA（最后 5 秒）：明确动作指令（点击/看评论区/关注）"
        ),
    },
    {
        "id": "dy_reverse",
        "platform": "tiktok",
        "name": "反转剧情",
        "goal": "完播率优先",
        "structure": (
            "结构要求（严格按此）：\n"
            "Hook：先给一个「看起来要失败/要踩坑」的开场\n"
            "Script：中间设计一次认知反转（原来问题不在 A 而在 B）\n"
            "转折后落到商品上，且要解释为什么它能解决 B\n"
            "CTA：用提问引导互动，不要直接喊购买"
        ),
    },
    {
        "id": "dy_beforeafter",
        "platform": "tiktok",
        "name": "前后对比",
        "goal": "效果可视化",
        "structure": (
            "结构要求（严格按此）：\n"
            "Hook：先说「之前是什么样」，用一个可量化的状态\n"
            "Script：中间用对比结构讲清变化（之前 vs 之后，分两到三点）\n"
            "强调变化的原因必须能归到商品的具体特性上\n"
            "CTA：邀请用户说自己的情况"
        ),
    },
    {
        "id": "dy_oral",
        "platform": "tiktok",
        "name": "口播带货",
        "goal": "直播间/口播切片",
        "structure": (
            "结构要求（严格按此）：\n"
            "Hook：直接对目标人群喊话（谁适合看这条）\n"
            "Script：口语短句，每句不超过 15 字，适合念出来\n"
            "中间必须有价格锚点（不用具体数字，用「比一杯奶茶」「不到两杯咖啡」这类参照）\n"
            "CTA：明确说出下一步动作和时间紧迫感（不带违禁词）"
        ),
    },
    # ---------------- Amazon ----------------
    {
        "id": "amz_benefit",
        "platform": "amazon",
        "name": "Benefit-led",
        "goal": "利益前置，适合功能型产品",
        "structure": (
            "Structure requirement:\n"
            "Title: brand + core keyword + top benefit (<=150 chars)\n"
            "Bullet Points: lead each with a capitalized BENEFIT, not a feature; "
            "explain the feature inside the sentence\n"
            "Product Description: open with the user's situation, then the mechanism, "
            "then who it is for"
        ),
    },
    {
        "id": "amz_problem",
        "platform": "amazon",
        "name": "Problem-Solution",
        "goal": "解决明确痛点",
        "structure": (
            "Structure requirement:\n"
            "Title: name the problem being solved, then the keyword\n"
            "Bullet Points: 1-2 points address the problem directly, remaining points cover specs\n"
            "Product Description: problem -> why common fixes fail -> how this differs"
        ),
    },
    {
        "id": "amz_compare",
        "platform": "amazon",
        "name": "Comparison",
        "goal": "替换升级场景",
        "structure": (
            "Structure requirement:\n"
            "Title: emphasize upgrade/replacement positioning (no competitor names)\n"
            "Bullet Points: each point contrasts 'old way' vs 'this way' without naming brands\n"
            "Product Description: explain what was wrong with typical options and what changed here"
        ),
    },
]

_BY_ID = {t["id"]: t for t in TEMPLATES}
_BY_PLATFORM: dict[str, list[dict]] = {}
for _t in TEMPLATES:
    _BY_PLATFORM.setdefault(_t["platform"], []).append(_t)


def list_templates(platform: str = "") -> list[dict]:
    """列出模版；传 platform 时只返回该平台的。"""
    plat = (platform or "").strip().lower()
    items = _BY_PLATFORM.get(plat, []) if plat else TEMPLATES
    return [dict(t) for t in items]


def get_template(template_id: str) -> dict | None:
    return _BY_ID.get((template_id or "").strip())


def resolve(platform: str, template_id: str) -> dict | None:
    """取模版，并校验平台是否匹配。

    平台不匹配时返回 None（由调用方决定是忽略还是报错）：
    拿小红书的「痛点开场」去生成 Amazon Listing 是没有意义的，
    但也不该因此让整个生成失败——所以默认忽略，不抛异常。
    """
    tpl = get_template(template_id)
    if tpl is None:
        return None
    if platform and tpl["platform"] != (platform or "").strip().lower():
        return None
    return tpl


def structure_of(platform: str, template_id: str) -> str:
    """取结构约束文本；取不到返回空串（表示用平台默认风格，不加结构约束）。"""
    tpl = resolve(platform, template_id)
    return tpl["structure"] if tpl else ""


def default_template_id(platform: str) -> str:
    """该平台的默认模版（第一个）。"""
    items = _BY_PLATFORM.get((platform or "").strip().lower(), [])
    return items[0]["id"] if items else ""
