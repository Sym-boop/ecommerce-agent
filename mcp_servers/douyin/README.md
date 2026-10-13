# 抖音 · 巨量千川 MCP 工具（mcp_servers/douyin）

面向「广告投递」的抖音（巨量千川 / 巨量引擎开放平台）MCP 工具包。

> 按平台分目录隔离：本目录只服务抖音/千川；后续拼多多（多多进宝）等会放在 `mcp_servers/pinduoduo/` 等独立目录，互不影响。

## 目录结构

| 文件 | 作用 |
| --- | --- |
| `config.py` | 读取 `.env` 中的 `QIANCHUAN_*` 配置（只读，支持热更新） |
| `client.py` | 巨量引擎开放平台 HTTP 客户端：OAuth2 续期 + 统一请求/解析 |
| `catalog.py` | 千川接口目录（工具名 / 说明 / path），新增接口只需追加一行 |
| `tools.py` | 纯函数工具实现（MCP Server 与后端投递 Agent 共用），含投递参数定义 |
| `server.py` | FastMCP Server（stdio），注册目录工具与组合工具 |

## 快速开始

```bash
pip install -r mcp_servers/requirements.txt        # fastmcp / httpx / pydantic / python-dotenv
python mcp_servers/douyin/server.py                # 以 stdio 方式启动 MCP Server
```

在支持 MCP 的客户端（如 Claude Desktop）中配置：

```json
{
  "mcpServers": {
    "douyin-qianchuan": {
      "command": "python",
      "args": ["d:/project-ecommerce-agent/mcp_servers/douyin/server.py"]
    }
  }
}
```

## 环境变量（项目根目录 `.env`，示例见 `.env.example`）

| 变量 | 说明 |
| --- | --- |
| `QIANCHUAN_APP_ID` | 巨量引擎开放平台应用 App ID |
| `QIANCHUAN_APP_SECRET` | 应用 Secret（用于刷新 access_token） |
| `QIANCHUAN_ACCESS_TOKEN` | OAuth2 授权得到的 access_token（调用凭证） |
| `QIANCHUAN_REFRESH_TOKEN` | 刷新 token（access_token 过期时续期） |
| `QIANCHUAN_ADVERTISER_ID` | 千川广告主账户 ID（投递主体，默认注入到请求） |
| `QIANCHUAN_BASE_URL` | 开放平台网关，默认 `https://api.oceanengine.com` |
| `QIANCHUAN_DELIVERY_MODE` | `mock`（默认，不真实投放）/ `real`（真实调用创编接口） |
| `QIANCHUAN_CREATE_PLAN_PATH` | 创编（投放）接口 path，默认 `/open_api/v1.0/qianchuan/ad/create/` |

## 工具清单

### 组合工具（面向投递链路）

| 工具 | 说明 |
| --- | --- |
| `qianchuan_api_call` | 通用调用：传 `path` 或目录中的 `tool` 名 + `params`，可调任意千川接口（含目录未收录的） |
| `qianchuan_account_overview` | 账户概览：广告主信息 + 账户余额 + 钱包余额 |
| `qianchuan_delivery_context` | 投递上下文：可投商品 / 可投抖音号 / 可用素材 |
| `qianchuan_delivery_preflight` | 投递预检：必填校验 + 商品/抖音号/素材是否可投，返回 `ready` 与明细 |
| `qianchuan_submit_delivery` | 提交投放：mock 返回模拟计划；real 调用创编接口 |

### 目录工具（常用千川接口，按接口名调用）

账户权限：`advertiser_info_v2`、`advertiser_public_info_v2`、`user_info_v2`、`oauth2_advertiser_get`、`qianchuan_advertiser_type_get_v1`

余额财务：`qianchuan_account_balance_get_v1`、`qianchuan_finance_wallet_get_v1`、`qianchuan_account_budget_get_v1`、`qianchuan_finance_detail_get_v1`

店铺商品：`qianchuan_shop_advertiser_list_v1`、`qianchuan_product_available_get_v1`、`qianchuan_uni_promotion_product_get_v1`、`qianchuan_uni_promotion_product_aweme_get_v1`、`qianchuan_uni_promotion_authorizable_shop_list_v1`

抖音号：`qianchuan_aweme_authorized_get_v1`、`qianchuan_aweme_auth_list_get_v1`、`qianchuan_uni_aweme_authorized_get_v1`、`tools_aweme_info_search_v2`

素材：`qianchuan_video_get_v1`、`qianchuan_image_get_v1`、`qianchuan_carousel_get_v1`、`qianchuan_material_get_v1`、`qianchuan_file_video_aweme_get_v1`、`qianchuan_aweme_video_get_v1`、`qianchuan_carousel_aweme_get_v1`、`qianchuan_video_by_aweme_get_v1`

计划广告：`qianchuan_ad_get_v1`、`qianchuan_ad_detail_get_v1`、`qianchuan_ad_material_get_v1`、`qianchuan_campaign_list_get_v1`、`qianchuan_ad_quota_get_v1`、`qianchuan_ad_reject_reason_v1`、`qianchuan_lq_ad_get_v1`、`qianchuan_uni_promotion_ad_detail_v1`、`qianchuan_uni_promotion_ad_product_get_v1`

建议预估：`qianchuan_suggest_bid_v1`、`qianchuan_suggest_budget_v1`、`qianchuan_suggest_roi_goal_v1`、`qianchuan_estimate_effect_v1`、`qianchuan_tools_estimate_audience_v1`

报表：`qianchuan_report_ad_get_v1`、`qianchuan_report_advertiser_get_v1`、`qianchuan_report_material_get_v1`、`qianchuan_report_search_word_get_v1`、`qianchuan_report_uni_promotion_get_v1`

定向工具：`qianchuan_orientation_package_get_v1`、`qianchuan_audience_list_get_v1`、`qianchuan_dmp_audiences_get_v1`、`qianchuan_keyword_package_get_v1`、`qianchuan_ad_recommend_keywords_get_v1`、`qianchuan_tools_gray_v1`、`qianchuan_tools_log_search_v1`、`tools_industry_get_v2`、`tools_aweme_multi_level_category_get_v2`

> 完整清单见 `catalog.py`；未收录接口用 `qianchuan_api_call(path=..., params=...)` 直接调用。

## 投递一条广告需要什么

一次「千川广告投放」需要把下面这些参数凑齐（也是 `qianchuan_delivery_requirements` 的内容）：

| 字段 | 必填 | 获取方式（对应工具） | 说明 |
| --- | --- | --- | --- |
| `advertiser_id` | ✅ | `oauth2_advertiser_get` / `.env QIANCHUAN_ADVERTISER_ID` | 投递主体（千川广告主账户 ID），须已授权当前 access_token |
| `shop_id` | ✅ | `qianchuan_shop_advertiser_list_v1` | 抖音店铺 ID；商家广告主必填，达人/机构广告主可空 |
| `product_id` | ✅ | `qianchuan_product_available_get_v1` | 小店**商品 ID**（不是 SKU），须在可投商品清单内 |
| `aweme_id` | ✅ | `qianchuan_aweme_authorized_get_v1` | 授权抖音号 ID；短视频投放需短视频带货权限，直播投放需直播带货权限 |
| `video_id` / `image_id` | ⭕ | `qianchuan_video_get_v1` / `qianchuan_image_get_v1` / `qianchuan_carousel_get_v1` | 素材 ID，视频与图文至少填一个 |
| `marketing_goal` | ✅ | 创编参数 | 营销目标 / 场景：如 短视频带货、直播间、商品全域 |
| `budget` | ✅ | `qianchuan_suggest_budget_v1` | 预算（元），不低于平台最低值 |
| `bid` / `roi_goal` | ⭕ | `qianchuan_suggest_bid_v1` / `qianchuan_suggest_roi_goal_v1` | 二选一：普通出价 或 支付 ROI 目标 |
| `orientation_package_id` | ⭕ | `qianchuan_orientation_package_get_v1` | 不填用系统智能定向 |
| `name` | ✅ | 创编参数 | 计划名称，便于在千川后台识别 |

投递前置条件（Agent 会先预检）：

1. 账户 **余额充足**（`qianchuan_account_balance_get_v1` / `qianchuan_finance_wallet_get_v1`）；
2. 当前 access_token **已授权**该 `advertiser_id`，且应用已开通所需接口权限；
3. 商品在**可投清单**、抖音号**已授权**、素材在**素材库**内。

## 与后端投递 Agent 的关系

后端 Agent 位于 `backend/app/agent/douyin_delivery_agent.py`，直接复用本目录的 `tools.py`：

- `GET  /api/v1/qianchuan/requirements` → 上面的投递参数表
- `GET  /api/v1/qianchuan/account` → 账户概览
- `POST /api/v1/qianchuan/preflight` → 投递预检
- `POST /api/v1/qianchuan/deliver` → 预检通过后提交投放（默认 mock）

## 切换到真实投放

1. 在 `.env` 填入 `QIANCHUAN_APP_ID / QIANCHUAN_APP_SECRET / QIANCHUAN_ACCESS_TOKEN / QIANCHUAN_ADVERTISER_ID`；
2. 将 `QIANCHUAN_DELIVERY_MODE` 改为 `real`；
3. 按巨量官方文档确认 `QIANCHUAN_CREATE_PLAN_PATH`（创编接口 path）与请求体字段；
4. 重启后端服务或 MCP Server（配置为每次调用实时读取，通常无需重启）。

## 新增接口 / 新增平台

- **新增千川接口**：在 `catalog.py` 的 `QIANCHUAN_TOOLS` 追加一行 `{"name", "description", "path"}` 即可，MCP 会自动注册。
- **新增平台（如拼多多）**：新建 `mcp_servers/pinduoduo/`，复用同一套结构（`config.py / client.py / catalog.py / tools.py / server.py`），与抖音完全隔离。