from fastmcp import FastMCP
from pydantic import BaseModel

mcp = FastMCP("Ads Manager")

class AdCampaign(BaseModel):
    campaign_id: str
    name: str
    status: str
    budget: float

@mcp.tool()
async def get_campaigns(advertiser_id: str) -> list[AdCampaign]:
    """获取广告主下的所有广告计划（Mock数据）"""
    return [
        AdCampaign(campaign_id="CAMP-01", name="T恤夏季大促", status="ENABLE", budget=5000.0),
        AdCampaign(campaign_id="CAMP-02", name="牛仔外套清仓", status="DISABLE", budget=2000.0),
    ]

@mcp.tool()
async def update_budget(campaign_id: str, new_budget: float) -> dict:
    """调整广告计划预算"""
    return {"status": "success", "campaign_id": campaign_id, "new_budget": new_budget}

if __name__ == "__main__":
    mcp.run(transport="stdio")