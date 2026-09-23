from fastmcp import FastMCP
from pydantic import BaseModel

mcp = FastMCP("ERP Connector")

class Product(BaseModel):
    sku: str
    name: str
    price: float
    stock: int

@mcp.tool()
async def query_product(sku: str) -> Product:
    """根据 SKU 查询商品详细信息（目前返回模拟数据）"""
    mock_data = {
        "SKU-1001": Product(sku="SKU-1001", name="夏季纯棉T恤", price=59.9, stock=500),
        "SKU-1002": Product(sku="SKU-1002", name="复古牛仔外套", price=199.0, stock=120),
    }
    return mock_data.get(sku, Product(sku=sku, name="未知商品", price=0.0, stock=0))

@mcp.tool()
async def sync_inventory(sku: str, new_stock: int) -> dict:
    """更新指定 SKU 的库存数量"""
    return {"status": "success", "sku": sku, "new_stock": new_stock, "message": "Mock库存同步成功"}

@mcp.tool()
async def pull_orders(since: str, limit: int = 50) -> list:
    """拉取指定时间之后的订单列表"""
    return [{"order_id": "ORD-20260923-001", "sku": "SKU-1001", "quantity": 2, "price": 59.9}]

if __name__ == "__main__":
    mcp.run(transport="stdio")