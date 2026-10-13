"""巨量千川接口目录（工具名 / 说明 / path）。

只收录「广告投递链路」最常用的一批接口，便于 MCP 客户端按名字直接调用；
目录之外的接口可用通用工具 `qianchuan_api_call` 直接传 path 调用，无需改代码。

数据来源：巨量千川 MCP 工具列表（用户提供）。新增接口时在 QIANCHUAN_TOOLS 追加一行即可。
"""
from __future__ import annotations

QIANCHUAN_TOOLS: list[dict] = [
    # ---- 账户 / 权限 ----
    {"name": "advertiser_info_v2", "description": "获取广告主账户详细信息", "path": "/open_api/2/advertiser/info/"},
    {"name": "advertiser_public_info_v2", "description": "获取广告主账户基础信息（无需申请权限）", "path": "/open_api/2/advertiser/public_info/"},
    {"name": "user_info_v2", "description": "获取 Access Token 对应的授权 User 信息", "path": "/open_api/2/user/info/"},
    {"name": "oauth2_advertiser_get", "description": "获取已授权的账号列表（店铺 / 代理商 / 组织）", "path": "/open_api/oauth2/advertiser/get/"},
    {"name": "qianchuan_advertiser_type_get_v1", "description": "获取千川账户类型（商家 / 达人 / 机构）", "path": "/open_api/v1.0/qianchuan/advertiser/type/get/"},
    # ---- 余额 / 财务 / 预算 ----
    {"name": "qianchuan_account_balance_get_v1", "description": "获取账户余额（通用 / 竞价 / 品牌）", "path": "/open_api/v1.0/qianchuan/account/balance/get/"},
    {"name": "qianchuan_finance_wallet_get_v1", "description": "获取千川账户钱包余额详情", "path": "/open_api/v1.0/qianchuan/finance/wallet/get/"},
    {"name": "qianchuan_account_budget_get_v1", "description": "获取账户日预算", "path": "/open_api/v1.0/qianchuan/account/budget/get/"},
    {"name": "qianchuan_finance_detail_get_v1", "description": "获取财务流水信息", "path": "/open_api/v1.0/qianchuan/finance/detail/get/"},
    # ---- 店铺 / 商品 ----
    {"name": "qianchuan_shop_advertiser_list_v1", "description": "查询店铺账户下管理的千川业务账户列表", "path": "/open_api/v1.0/qianchuan/shop/advertiser/list/"},
    {"name": "qianchuan_product_available_get_v1", "description": "获取店铺内可投商品列表（仅商家广告主）", "path": "/open_api/v1.0/qianchuan/product/available/get/"},
    {"name": "qianchuan_uni_promotion_product_get_v1", "description": "全域商家可选商品列表", "path": "/open_api/v1.0/qianchuan/uni_promotion/product/get/"},
    {"name": "qianchuan_uni_promotion_product_aweme_get_v1", "description": "全域达人 / 机构可选商品列表", "path": "/open_api/v1.0/qianchuan/uni_promotion/product/aweme/get/"},
    {"name": "qianchuan_uni_promotion_authorizable_shop_list_v1", "description": "商品全域可授权店铺列表", "path": "/open_api/v1.0/qianchuan/uni_promotion/authorizable_shop/list/"},
    # ---- 抖音号（aweme）----
    {"name": "qianchuan_aweme_authorized_get_v1", "description": "获取千川账户下可投广抖音号（含带货权限校验）", "path": "/open_api/v1.0/qianchuan/aweme/authorized/get/"},
    {"name": "qianchuan_aweme_auth_list_get_v1", "description": "获取抖音号授权列表", "path": "/open_api/v1.0/qianchuan/aweme_auth_list/get/"},
    {"name": "qianchuan_uni_aweme_authorized_get_v1", "description": "获取全域推广抖音号列表", "path": "/open_api/v1.0/qianchuan/uni_aweme/authorized/get/"},
    {"name": "tools_aweme_info_search_v2", "description": "查询绑定的抖音号信息（抖音账户与类目）", "path": "/open_api/2/tools/aweme_info_search/"},
    # ---- 素材（视频 / 图文 / 图片）----
    {"name": "qianchuan_video_get_v1", "description": "获取千川素材库视频", "path": "/open_api/v1.0/qianchuan/video/get/"},
    {"name": "qianchuan_image_get_v1", "description": "获取千川素材库图片", "path": "/open_api/v1.0/qianchuan/image/get/"},
    {"name": "qianchuan_carousel_get_v1", "description": "获取千川素材库图文", "path": "/open_api/v1.0/qianchuan/carousel/get/"},
    {"name": "qianchuan_material_get_v1", "description": "获取账户下素材列表和数据", "path": "/open_api/v1.0/qianchuan/material/get/"},
    {"name": "qianchuan_file_video_aweme_get_v1", "description": "获取抖音号下的视频素材（支持按商品 ID 过滤）", "path": "/open_api/v1.0/qianchuan/file/video/aweme/get/"},
    {"name": "qianchuan_aweme_video_get_v1", "description": "获取小店可投视频列表", "path": "/open_api/v1.0/qianchuan/aweme/video/get/"},
    {"name": "qianchuan_carousel_aweme_get_v1", "description": "通过商品 ID 获取抖音号下已有图文素材", "path": "/open_api/v1.0/qianchuan/carousel/aweme/get/"},
    {"name": "qianchuan_video_by_aweme_get_v1", "description": "通过抖音视频 ID/URL 查询素材库中的视频信息", "path": "/open_api/v1.0/qianchuan/video/by_aweme/get/"},
    # ---- 计划 / 广告 ----
    {"name": "qianchuan_ad_get_v1", "description": "获取千川账户下计划列表", "path": "/open_api/v1.0/qianchuan/ad/get/"},
    {"name": "qianchuan_ad_detail_get_v1", "description": "获取单个计划详情（含创意）", "path": "/open_api/v1.0/qianchuan/ad/detail/get/"},
    {"name": "qianchuan_ad_material_get_v1", "description": "获取计划下素材信息（含审核状态）", "path": "/open_api/v1.0/qianchuan/ad/material/get/"},
    {"name": "qianchuan_campaign_list_get_v1", "description": "获取账户下广告组列表", "path": "/open_api/v1.0/qianchuan/campaign_list/get/"},
    {"name": "qianchuan_ad_quota_get_v1", "description": "获取在投计划配额", "path": "/open_api/v1.0/qianchuan/ad/quota/get/"},
    {"name": "qianchuan_ad_reject_reason_v1", "description": "获取计划维度审核意见", "path": "/open_api/v1.0/qianchuan/ad/reject_reason/"},
    {"name": "qianchuan_lq_ad_get_v1", "description": "获取低效计划列表", "path": "/open_api/v1.0/qianchuan/lq_ad/get/"},
    {"name": "qianchuan_uni_promotion_ad_detail_v1", "description": "获取全域推广计划详情", "path": "/open_api/v1.0/qianchuan/uni_promotion/ad/detail/"},
    {"name": "qianchuan_uni_promotion_ad_product_get_v1", "description": "获取全域计划下商品列表", "path": "/open_api/v1.0/qianchuan/uni_promotion/ad/product/get/"},
    # ---- 建议 / 预估 ----
    {"name": "qianchuan_suggest_bid_v1", "description": "获取非 ROI 目标建议出价", "path": "/open_api/v1.0/qianchuan/suggest_bid/"},
    {"name": "qianchuan_suggest_budget_v1", "description": "获取建议预算", "path": "/open_api/v1.0/qianchuan/suggest/budget/"},
    {"name": "qianchuan_suggest_roi_goal_v1", "description": "获取建议支付 ROI 目标最大值", "path": "/open_api/v1.0/qianchuan/suggest/roi/goal/"},
    {"name": "qianchuan_estimate_effect_v1", "description": "获取投放预估效果", "path": "/open_api/v1.0/qianchuan/estimate/effect/"},
    {"name": "qianchuan_tools_estimate_audience_v1", "description": "预估受众数量与曝光量", "path": "/open_api/v1.0/qianchuan/tools/estimate_audience/"},
    # ---- 报表 ----
    {"name": "qianchuan_report_ad_get_v1", "description": "获取广告计划数据", "path": "/open_api/v1.0/qianchuan/report/ad/get/"},
    {"name": "qianchuan_report_advertiser_get_v1", "description": "获取广告主账户数据", "path": "/open_api/v1.0/qianchuan/report/advertiser/get/"},
    {"name": "qianchuan_report_material_get_v1", "description": "获取素材数据（视频 / 图片 / 图文）", "path": "/open_api/v1.0/qianchuan/report/material/get/"},
    {"name": "qianchuan_report_search_word_get_v1", "description": "获取搜索词 / 关键词数据报表", "path": "/open_api/v1.0/qianchuan/report/search_word/get/"},
    {"name": "qianchuan_report_uni_promotion_get_v1", "description": "获取全域推广账户维度数据", "path": "/open_api/v1.0/qianchuan/report/uni_promotion/get/"},
    # ---- 定向 / 人群 / 工具 ----
    {"name": "qianchuan_orientation_package_get_v1", "description": "获取定向包列表", "path": "/open_api/v1.0/qianchuan/orientation_package/get/"},
    {"name": "qianchuan_audience_list_get_v1", "description": "获取人群管理列表", "path": "/open_api/v1.0/qianchuan/audience_list/get/"},
    {"name": "qianchuan_dmp_audiences_get_v1", "description": "获取人群包列表", "path": "/open_api/v1.0/qianchuan/dmp/audiences/get/"},
    {"name": "qianchuan_keyword_package_get_v1", "description": "获取词包推荐关键词", "path": "/open_api/v1.0/qianchuan/keyword_package/get/"},
    {"name": "qianchuan_ad_recommend_keywords_get_v1", "description": "获取系统推荐的搜索关键词", "path": "/open_api/v1.0/qianchuan/ad/recommend_keywords/get/"},
    {"name": "qianchuan_tools_gray_v1", "description": "查询广告主命中的白名单功能", "path": "/open_api/v1.0/qianchuan/tools/gray/"},
    {"name": "qianchuan_tools_log_search_v1", "description": "查询账户 / 计划维度操作日志", "path": "/open_api/v1.0/qianchuan/tools/log_search/"},
    {"name": "tools_industry_get_v2", "description": "获取行业列表（二级用于开户、三级用于创意分类）", "path": "/open_api/2/tools/industry/get/"},
    {"name": "tools_aweme_multi_level_category_get_v2", "description": "获取抖音类目 id 值（用于达人定向）", "path": "/open_api/2/tools/aweme_multi_level_category/get/"},
]

_BY_NAME = {item["name"]: item for item in QIANCHUAN_TOOLS}


def resolve_path(name: str) -> str | None:
    """按工具名解析接口 path，未收录返回 None。"""
    item = _BY_NAME.get(name)
    return item["path"] if item else None


def tool_names() -> list[str]:
    return [item["name"] for item in QIANCHUAN_TOOLS]